"""요청 저장소 (docs/SPEC.md 6장).

요청의 진행 상태와 결과의 원천은 체크포인터다. 이 저장소는 체크포인터에 없는 것, 곧 요청의 머리글(요청자, 제출 시각,
이번에 새로 쓴 본문), 처리 상태, 노드별 진행 기록, SSE 이벤트 기록, 실행 중인 task만 메모리에 둔다. 서버를 다시 띄우면
체크포인터를 한 번 훑어 머리글과 상태를 복구한다(rehydrate). 노드별 진행 기록과 이벤트는 복구하지 않는다(계약: 재시작 뒤에는
nodes가 비어 있을 수 있고 종료 이벤트 하나만 보낸다).
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.api.events import EventLog
from app.audit import AuditLog, record_of
from app.persistence.checkpointer import list_thread_ids, thread_config

logger = logging.getLogger(__name__)

Status = Literal["reviewing", "awaiting_approval", "approved", "rejected", "auto_approved", "manual_review"]
TERMINAL_STATUSES: tuple[Status, ...] = ("approved", "rejected", "auto_approved", "manual_review")
RESTART_MESSAGE = "서버가 다시 시작되어 검토가 중단됐어요. 수동 검토로 넘겨요."

_REQUEST_ID = re.compile(r"^REQ-(\d+)$")


@dataclass
class NodeRun:
    node: str
    status: Literal["running", "done", "failed"]
    started_at: str
    ended_at: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"node": self.node, "status": self.status, "started_at": self.started_at, "ended_at": self.ended_at, "error": self.error}


@dataclass
class RequestRecord:
    request_id: str
    requester: str
    submitted_at: str
    # 이번 요청이 새로 쓴 본문(마스킹). 재요청이어도 부모 본문은 들어 있지 않다. 응답의 text가 이것이다.
    own_text: str
    parent_request_id: str | None = None
    status: Status = "reviewing"
    # manual_review일 때의 사용자용 문장
    message: str | None = None
    nodes: dict[str, NodeRun] = field(default_factory=dict)
    log: EventLog = field(default_factory=EventLog)
    # 결정(resume)이 겹치지 않게 한다. 승인 대기 확인과 재개를 한 묶음으로 처리한다.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None
    recommendation_sent: bool = False
    # 서버를 다시 띄워 체크포인터에서 복구한 요청은 그 뒤에 이벤트가 쌓여도 중간부터만 있다. 그런 기록은 다시 보내지 않고
    # 종료 이벤트 하나만 보낸다(계약: 재시작 뒤에는 체크포인터 상태로 종료 이벤트 하나).
    replayable: bool = True


class RequestStore:
    def __init__(self) -> None:
        self._records: dict[str, RequestRecord] = {}
        self._next = 1

    def new_id(self) -> str:
        """REQ-0001부터 순서대로. await 없이 끝나는 동기 함수라서 이벤트 루프 안에서는 겹치지 않는다."""
        request_id = f"REQ-{self._next:04d}"
        self._next += 1
        return request_id

    def add(self, record: RequestRecord) -> None:
        self._records[record.request_id] = record
        match = _REQUEST_ID.match(record.request_id)
        if match:  # 복구한 번호보다 큰 번호부터 새로 매긴다
            self._next = max(self._next, int(match.group(1)) + 1)

    def get(self, request_id: str) -> RequestRecord | None:
        return self._records.get(request_id)

    def newest_first(self) -> list[RequestRecord]:
        return sorted(self._records.values(), key=lambda r: (r.submitted_at, r.request_id), reverse=True)


def status_of(snapshot: Any) -> Status:
    """체크포인트 스냅샷에서 처리 상태를 읽는다. reviewing은 실행 도중 서버가 꺼졌다는 뜻이다."""
    outcome = snapshot.values.get("outcome")
    if outcome is not None:
        return outcome
    waiting = any(task.interrupts for task in snapshot.tasks)
    return "awaiting_approval" if snapshot.next == ("approval_gate",) and waiting else "reviewing"


async def rehydrate(store: RequestStore, graph: Any, saver: Any, audit: AuditLog) -> list[str]:
    """체크포인터를 훑어 저장소를 복구한다. 실행 도중 꺼진 요청은 manual_review로 닫고 닫은 request_id를 돌려준다.

    자동으로 다시 실행하지 않는다. LLM 비용이 다시 들고, 같은 요청이 두 번 실행될 위험이 있어서다.
    """
    closed: list[str] = []
    for thread_id in reversed(await list_thread_ids(saver)):  # 오래된 것부터 넣어 번호 복구가 안정적이게 한다
        snapshot = await graph.aget_state(thread_config(thread_id))
        values = snapshot.values
        if "request_id" not in values:
            continue  # 이 API가 만든 thread가 아니다
        status = status_of(snapshot)
        record = RequestRecord(
            request_id=values["request_id"],
            requester=values.get("requester", ""),
            submitted_at=values.get("submitted_at", ""),
            own_text=values.get("request_text", ""),
            parent_request_id=values.get("parent_request_id"),
            status=status,
            message=values.get("message"),
            recommendation_sent=values.get("recommendation") is not None,
            replayable=False,
        )
        if status == "reviewing":
            await graph.aupdate_state(
                thread_config(thread_id), {"outcome": "manual_review", "message": RESTART_MESSAGE}, as_node="finalize"
            )
            record.status, record.message = "manual_review", RESTART_MESSAGE
            closed.append(record.request_id)
            try:
                await audit.append("manual_review", {**record_of({**values, "outcome": "manual_review"}), "stage": "restart"})
            except OSError as exc:
                logger.error("감사 로그를 쓰지 못했다 (%s): manual_review", type(exc).__name__)
        store.add(record)
    return closed
