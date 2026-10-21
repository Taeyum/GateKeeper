"""그래프 실행과 SSE 이벤트 변환 (docs/API_CONTRACT.md 4장).

POST가 요청을 받으면 그래프를 백그라운드 task로 돌리고, 이 러너가 LangGraph의 "tasks" 스트림(노드의 시작과 종료)을 계약의
이벤트로 바꿔 요청별 기록(EventLog)에 쌓는다. 서브그래프 안의 노드는 이 스트림에 나오지 않는다(부모 그래프의 노드만 나온다).
이벤트는 최상위 노드 9개만 보낸다.

실행은 SSE 연결과 상관없이 계속된다. 승인이 필요하면 approval_gate의 interrupt에서 스트림이 끝나고(상태는 체크포인터에 저장된다),
그때 awaiting_approval을 보낸다. 사람이 결정하면 resume이 같은 thread를 이어서 돌려 done을 보낸다.

실패는 두 가지다. 예상한 검토 불가(ReviewIncomplete)와 제한 시간 초과는 done(manual_review)에 사용자용 문장을 실어 보내고, 예상하지
못한 오류는 error 이벤트로 보낸다. 둘 다 요청을 manual_review로 닫고 상태에도 남겨서 서버를 다시 띄워도 보이게 한다.
"""

import asyncio
import logging
import uuid
from typing import Any

from langgraph.types import Command

from app.agent.errors import ReviewIncomplete
from app.agent.results import ApprovalRecord
from app.api import views
from app.api.errors import INTERNAL_MESSAGE
from app.api.store import NodeRun, RequestRecord
from app.audit import AuditLog, record_of
from app.clock import now_iso
from app.data_loader import load_rules
from app.persistence.checkpointer import thread_config

logger = logging.getLogger(__name__)

TOP_LEVEL_NODES = frozenset(
    {"parse_request", "classify", "policy_agent", "impact_agent", "history_agent", "decide", "validate", "approval_gate", "finalize"}
)
NODE_FAILED = "확인하지 못했어요"
TIMEOUT_MESSAGE = "검토가 너무 오래 걸려 수동 검토로 넘겨요."
# ReviewIncomplete의 stage별 사용자용 문장. 요청에서 온 값은 싣지 않는다(예외의 detail에는 들어 있을 수 있어 쓰지 않는다).
REVIEW_INCOMPLETE_MESSAGES = {
    "parse_request": "요청에서 대상 서비스나 작업 시각을 읽지 못해 자동 검토를 끝내지 못했어요. 수동 검토로 넘겨요.",
    "decide": "규정 검사 결과를 얻지 못해 자동 검토를 끝내지 못했어요. 수동 검토로 넘겨요.",
}


class Runner:
    def __init__(self, graph: Any, audit: AuditLog, *, timeout_s: float) -> None:
        self.graph = graph
        self.audit = audit
        self.timeout_s = timeout_s
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ 진입점

    def start(self, record: RequestRecord, graph_input: dict[str, Any]) -> None:
        """그래프를 백그라운드로 시작한다. task는 가비지 수집되지 않게 붙들어 두고 서버가 닫힐 때 취소한다."""
        task = asyncio.create_task(self._run(record, graph_input), name=f"gk-{record.request_id}")
        record.task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def resume(self, record: RequestRecord, approval: ApprovalRecord) -> None:
        """승인 대기 중인 요청에 승인자의 결정을 넣어 이어서 실행하고, 끝날 때까지 기다린다(finalize까지 빠르다)."""
        await self._run(record, Command(resume=approval.model_dump()))

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _values(self, record: RequestRecord) -> dict[str, Any]:
        """체크포인터에 저장된 지금의 상태. 실행 설정(run_id 등)과 섞지 않고 thread만 지정해 읽는다."""
        return (await self.graph.aget_state(thread_config(record.request_id))).values

    # ------------------------------------------------------------ 실행

    async def _run(self, record: RequestRecord, graph_input: dict[str, Any] | Command) -> None:
        trace_id = graph_input["trace_id"] if isinstance(graph_input, dict) else uuid.uuid4().hex
        config = {
            **thread_config(record.request_id),
            "run_id": uuid.UUID(trace_id),  # 그래프 루트 실행의 id. 감사 로그의 trace_id와 같아 LangSmith trace와 이어 볼 수 있다
            "run_name": record.request_id,
            "metadata": {"request_id": record.request_id},
        }
        scope = asyncio.timeout(self.timeout_s)
        try:
            async with scope:
                interrupted = False
                async for _mode, data in self.graph.astream(graph_input, config, stream_mode=["tasks"], durability="sync"):
                    interrupted = await self._on_task(record, data) or interrupted
                await self._finish(record, interrupted)
        except asyncio.CancelledError:
            raise  # 서버가 닫히는 중이다. 요청을 실패로 표시하지 않는다(다음에 뜰 때 복구가 닫는다)
        except ReviewIncomplete as exc:
            message = REVIEW_INCOMPLETE_MESSAGES.get(exc.stage or "", INTERNAL_MESSAGE)
            await self._to_manual_review(record, message, stage=exc.stage or "review", unexpected=False)
        except TimeoutError as exc:
            if scope.expired():
                await self._to_manual_review(record, TIMEOUT_MESSAGE, stage="timeout", unexpected=False)
            else:
                await self._unexpected(record, exc)
        except Exception as exc:
            await self._unexpected(record, exc)

    async def _unexpected(self, record: RequestRecord, exc: Exception) -> None:
        # 예외 메시지에는 요청에서 온 값이 있을 수 있어서 타입 이름만 남긴다
        logger.error("검토 중 예상하지 못한 오류 (%s): %s", type(exc).__name__, record.request_id)
        await self._to_manual_review(record, INTERNAL_MESSAGE, stage=type(exc).__name__, unexpected=True)

    # ------------------------------------------------------------ 이벤트 변환

    async def _on_task(self, record: RequestRecord, data: dict[str, Any]) -> bool:
        """노드 시작/종료 하나를 이벤트로 바꾼다. approval_gate가 사람을 기다리는 중이면 True."""
        name = data["name"]
        if name not in TOP_LEVEL_NODES:
            return False
        if "triggers" in data:
            await self._node_started(record, name)
            return False
        if data.get("interrupts"):
            return True  # approval_gate가 interrupt()로 멈췄다. 끝난 것이 아니다
        await self._node_completed(record, name, data)
        return False

    async def _node_started(self, record: RequestRecord, name: str) -> None:
        now = now_iso()
        existing = record.nodes.get(name)
        if name == "approval_gate":
            if existing is not None and existing.status == "running":
                return  # 승인 대기를 재개한 것이다. 이미 보여 줬다
            if not record.recommendation_sent:
                # 권고는 승인 단계가 시작될 때 한 번 보낸다. 재작성이 있었어도 마지막 것 하나다.
                values = await self._values(record)
                await record.log.append("recommendation", views.recommendation_payload(values, load_rules()))
                record.recommendation_sent = True
        if existing is None:
            record.nodes[name] = NodeRun(name, "running", now)
        else:  # validate가 실패해 decide, validate가 다시 도는 경우. 시작 시각은 처음 것을 유지한다
            existing.status, existing.ended_at, existing.error = "running", None, None
        await record.log.append("node_started", {"node": name, "at": now})

    async def _node_completed(self, record: RequestRecord, name: str, data: dict[str, Any]) -> None:
        now = now_iso()
        written: dict[str, Any] = data.get("result") or {}  # 이 노드가 쓴 채널 이름 -> 값
        error = self._partial_failure(name, written)
        run = record.nodes.get(name)
        if run is not None:
            run.status, run.ended_at, run.error = ("failed" if error else "done"), now, error
        payload: dict[str, Any] = {"node": name, "at": now, "error": error}
        if name == "parse_request" and "structured" in written:
            payload["structured"] = written["structured"].model_dump(mode="json")
        if name == "classify" and "route" in written:
            payload["route"] = written["route"]
        await record.log.append("node_completed", payload)

    @staticmethod
    def _partial_failure(name: str, written: dict[str, Any]) -> str | None:
        """서브에이전트가 끝내 확인하지 못했으면 그 사용자용 요약("영향도: 확인 불가")이 노드의 error다."""
        key = views.SUBAGENT_OF_NODE.get(name)
        result = written.get(key) if key else None
        return result.summary if result is not None and result.status == "unavailable" else None

    async def _finish(self, record: RequestRecord, interrupted: bool) -> None:
        now = now_iso()
        if interrupted:
            record.status = "awaiting_approval"
            await record.log.append("awaiting_approval", {"request_id": record.request_id, "status": "awaiting_approval", "at": now})
            return
        values = await self._values(record)
        outcome = values.get("outcome")
        if outcome not in ("approved", "rejected", "auto_approved"):
            raise RuntimeError("그래프가 결과 없이 끝났다")
        record.status = outcome
        await record.log.append(
            "done",
            {"request_id": record.request_id, "status": outcome, "at": values.get("decided_at") or now, "approval": views.approval_view(values), "message": None},
        )

    async def _to_manual_review(self, record: RequestRecord, message: str, *, stage: str, unexpected: bool) -> None:
        """요청을 수동 검토로 닫는다. 진행 중이던 노드는 실패로 표시하고, 상태와 감사 로그에 남기고, 종료 이벤트를 보낸다."""
        now = now_iso()
        for run in record.nodes.values():
            if run.status == "running":
                run.status, run.ended_at, run.error = "failed", now, NODE_FAILED
                await record.log.append("node_completed", {"node": run.node, "at": now, "error": NODE_FAILED})
        record.status, record.message = "manual_review", message

        values: dict[str, Any] = {}
        try:
            # 서버를 다시 띄워도 manual_review로 보이게 상태에 남긴다. as_node는 그래프의 끝 노드라 다음에 돌 노드가 없어진다.
            await self.graph.aupdate_state(
                thread_config(record.request_id), {"outcome": "manual_review", "message": message}, as_node="finalize"
            )
            values = await self._values(record)
        except Exception as exc:
            logger.error("수동 검토 표시를 상태에 남기지 못했다 (%s): %s", type(exc).__name__, record.request_id)
        try:
            await self.audit.append("manual_review", {**record_of(values or {"request_id": record.request_id}), "stage": stage})
        except OSError as exc:
            logger.error("감사 로그를 쓰지 못했다 (%s): manual_review", type(exc).__name__)

        if unexpected:
            await record.log.append("error", {"code": "internal", "message": message})
        else:
            await record.log.append(
                "done", {"request_id": record.request_id, "status": "manual_review", "at": now, "approval": None, "message": message}
            )
