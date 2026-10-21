"""러너(그래프 실행 -> SSE 이벤트)와 저장소 복구 (docs/API_CONTRACT.md 4장).

fake LLM, 메모리 내 MCP, 실제 규칙 엔진과 실제 SQLite 체크포인터로 v2 그래프를 돌려 이벤트의 순서와 모양,
실패 경로, 서버를 다시 띄운 뒤의 복구를 본다. dev 라벨만 쓴다.
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app import data_loader
from app.agent import graph as graph_module
from app.agent.graph import build_graph
from app.agent.mcp_client import MCPClient, MCPTimeoutError
from app.agent.results import ApprovalRecord
from app.api import views
from app.api.errors import INTERNAL_MESSAGE
from app.api.runner import NODE_FAILED, REVIEW_INCOMPLETE_MESSAGES, TIMEOUT_MESSAGE, TOP_LEVEL_NODES, Runner
from app.api.store import RESTART_MESSAGE, RequestRecord, RequestStore, rehydrate
from app.audit import AuditLog
from app.clock import now_iso
from app.llm.fake import set_fake_responses_for
from app.mcp_server.server import build_server
from app.persistence.checkpointer import open_checkpointer, thread_config
from app.schemas import ChangeRequest
from eval.harness import initial_state
from tests._graph_helpers import BAD, OK, draft, gold, label, queue
from tests._history import make_index


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


@pytest.fixture
async def mcp():
    async with MCPClient(build_server(history_index=make_index()), base_delay=0.01) as client:
        yield client


@asynccontextmanager
async def environment(mcp, tmp_path, *, timeout_s: float = 30.0):
    """lifespan이 만드는 것과 같은 묶음. 같은 tmp_path로 다시 열면 서버를 다시 띄운 것과 같다."""
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as saver:
        audit = AuditLog(tmp_path / "audit.jsonl")
        graph = build_graph("v2", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
        yield SimpleNamespace(
            graph=graph, saver=saver, audit=audit, store=RequestStore(), runner=Runner(graph, audit, timeout_s=timeout_s)
        )


def new_record(env, dev_id: str, request_id: str | None = None) -> tuple[RequestRecord, dict]:
    item = gold(dev_id)
    request_id = request_id or env.store.new_id()
    record = RequestRecord(request_id=request_id, requester=item.requester, submitted_at=now_iso(), own_text=item.text)
    env.store.add(record)
    graph_input = {
        **initial_state(item, item.text),
        "request_id": request_id,
        "request_text": item.text,
        "parent_request_id": None,
        "trace_id": uuid.uuid4().hex,
    }
    return record, graph_input


async def submit(env, dev_id: str, request_id: str | None = None) -> RequestRecord:
    record, graph_input = new_record(env, dev_id, request_id)
    env.runner.start(record, graph_input)
    await record.task
    return record


def sequence(record: RequestRecord) -> list[tuple[str, str | None]]:
    return [(name, data.get("node")) for name, data in record.log.events]


def audit_lines(env) -> list[dict]:
    path = env.audit.path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def approval(decision: str = "approve") -> ApprovalRecord:
    return ApprovalRecord(decision=decision, approver="박CAB", comment="확인했습니다", at=now_iso())


# ---------------------------------------------------------------------------
# 이벤트 순서와 모양
# ---------------------------------------------------------------------------


async def test_일반_변경은_계약의_순서로_이벤트를_보내고_승인_대기에서_끝난다(mcp, tmp_path):
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")

        seq = sequence(record)
        assert seq[:4] == [("node_started", "parse_request"), ("node_completed", "parse_request"), ("node_started", "classify"), ("node_completed", "classify")]
        assert {node for _, node in seq if node} <= TOP_LEVEL_NODES  # 서브그래프 안의 노드는 나오지 않는다

        agents = ("policy_agent", "impact_agent", "history_agent")
        started = [seq.index(("node_started", a)) for a in agents]
        completed = [seq.index(("node_completed", a)) for a in agents]
        assert max(started) < min(completed)  # 병렬 세 개의 시작이 먼저 온다
        assert max(completed) < seq.index(("node_started", "decide")) < seq.index(("node_started", "validate"))
        assert seq.index(("node_started", "validate")) < seq.index(("recommendation", None)) < seq.index(("node_started", "approval_gate"))
        assert seq[-1] == ("awaiting_approval", None) and seq.count(("recommendation", None)) == 1
        assert ("node_completed", "approval_gate") not in seq  # 승인 대기 중에는 approval_gate가 끝나지 않았다
        assert all(data["error"] is None for name, data in record.log.events if name == "node_completed")


async def test_parse_request와_classify의_종료_이벤트는_구조화_결과와_경로를_싣는다(mcp, tmp_path):
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")
        completed = {data["node"]: data for name, data in record.log.events if name == "node_completed"}
        assert completed["parse_request"]["structured"]["target"] == "payments-db"
        assert completed["classify"]["route"] == "general"
        assert "structured" not in completed["decide"] and "route" not in completed["decide"]


async def test_권고_이벤트는_상세_응답과_같은_묶음이다(mcp, tmp_path):
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")
        (payload,) = [data for name, data in record.log.events if name == "recommendation"]
        assert set(payload) == {"recommendation", "alternative_diff", "impact", "similar_incidents", "has_similar_incident"}
        assert payload["recommendation"]["verdict"] == "반려"
        assert [item["field"] for item in payload["alternative_diff"]] == ["start", "source", "expires_days"]
        assert payload["impact"]["id"] == "payments-db"

        values = (await env.graph.aget_state(thread_config(record.request_id))).values
        assert payload == views.recommendation_payload(values, data_loader.load_rules())


async def test_진행_기록은_노드마다_한_항목이고_승인_대기_노드만_running이다(mcp, tmp_path):
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")
        assert record.status == "awaiting_approval"
        order = list(record.nodes)  # 시작한 순서. 병렬 세 노드의 순서는 정해져 있지 않다
        assert order[:2] == ["parse_request", "classify"] and set(order[2:5]) == {"policy_agent", "impact_agent", "history_agent"}
        assert order[5:] == ["decide", "validate", "approval_gate"]
        assert {run.status for node, run in record.nodes.items() if node != "approval_gate"} == {"done"}
        assert record.nodes["approval_gate"].status == "running" and record.nodes["approval_gate"].ended_at is None


async def test_승인자가_결정하면_이어서_finalize와_done을_보낸다(mcp, tmp_path):
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")
        before = len(record.log.events)
        decision = approval("approve")
        await env.runner.resume(record, decision)

        assert [(n, d.get("node")) for n, d in record.log.events[before:]] == [
            ("node_completed", "approval_gate"), ("node_started", "finalize"), ("node_completed", "finalize"), ("done", None),
        ]  # fmt: skip
        done = record.log.events[-1][1]
        assert done == {"request_id": record.request_id, "status": "approved", "at": decision.at, "approval": decision.model_dump(), "message": None}
        assert record.status == "approved" and record.nodes["approval_gate"].status == "done"
        assert sequence(record).count(("recommendation", None)) == 1  # 재개해도 권고를 다시 보내지 않는다
        assert sequence(record).count(("node_started", "approval_gate")) == 1

        (line,) = audit_lines(env)
        assert (line["event"], line["outcome"], line["approver"], line["request_id"]) == ("decision", "approved", "박CAB", record.request_id)


async def test_반려_결정은_rejected로_끝난다(mcp, tmp_path):
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")
        await env.runner.resume(record, approval("reject"))
        assert record.status == "rejected" and record.log.events[-1][1]["status"] == "rejected"


async def test_표준_변경은_승인_대기_없이_자동_승인으로_끝난다(mcp, tmp_path):
    queue("REQ-D04")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D04")
        seq = sequence(record)
        assert ("node_started", "impact_agent") not in seq and ("node_started", "history_agent") not in seq  # 표준 경로는 정책 검사만
        assert ("node_started", "validate") in seq
        assert seq.index(("recommendation", None)) < seq.index(("node_started", "approval_gate")) < seq.index(("node_completed", "approval_gate"))
        assert ("awaiting_approval", None) not in seq
        assert seq[-3:] == [("node_started", "finalize"), ("node_completed", "finalize"), ("done", None)]
        done = record.log.events[-1][1]
        assert (done["status"], done["approval"], done["message"]) == ("auto_approved", None, None)
        assert record.status == "auto_approved"
        assert audit_lines(env)[0]["event"] == "auto_approved"


async def test_서브에이전트가_확인_불가면_그_노드의_종료_이벤트에_사용자용_문장이_실린다(mcp, tmp_path, monkeypatch):
    async def failing(*args, **kwargs):
        raise MCPTimeoutError("시간 초과")

    monkeypatch.setattr(mcp, "search_history", failing)
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")
        completed = {data["node"]: data for name, data in record.log.events if name == "node_completed"}
        assert completed["history_agent"]["error"] == "이력: 확인 불가"
        assert completed["policy_agent"]["error"] is None
        assert record.nodes["history_agent"].status == "failed"
        assert record.status == "awaiting_approval"  # 부분 실패는 흐름을 막지 않는다
        values = (await env.graph.aget_state(thread_config(record.request_id))).values
        assert views.unavailable_errors(values) == ["이력: 확인 불가"]


async def test_검증에_실패해_decide가_다시_돌면_시작_이벤트를_다시_보내고_진행_기록은_한_항목이다(mcp, tmp_path):
    queue("REQ-D06", drafts=[draft("처음 설명"), draft("고친 설명")], checks=[BAD, OK])
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D06")
        seq = sequence(record)
        assert seq.count(("node_started", "decide")) == 2 and seq.count(("node_started", "validate")) == 2
        assert seq.count(("recommendation", None)) == 1  # 권고는 마지막 것 하나
        assert [node for node in record.nodes].count("decide") == 1
        assert record.nodes["decide"].status == "done"
        (payload,) = [data for name, data in record.log.events if name == "recommendation"]
        assert payload["recommendation"]["explanation"] == "고친 설명"


# ---------------------------------------------------------------------------
# 실패
# ---------------------------------------------------------------------------


async def test_검토_불가는_done_manual_review로_보내고_요청_값은_싣지_않는다(mcp, tmp_path):
    queue("REQ-D01")
    wrong = label("REQ-D01", target="없는-서비스")
    set_fake_responses_for("main", ChangeRequest, [wrong, wrong])  # 대상 확인 재시도까지 모두 틀린다
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")

        assert record.status == "manual_review" and record.message == REVIEW_INCOMPLETE_MESSAGES["parse_request"]
        name, done = record.log.events[-1]
        assert name == "done" and done["status"] == "manual_review" and done["message"] == REVIEW_INCOMPLETE_MESSAGES["parse_request"]
        assert ("node_completed", "parse_request") in sequence(record)  # 진행 중이던 노드를 실패로 닫는다
        assert record.nodes["parse_request"].status == "failed" and record.nodes["parse_request"].error == NODE_FAILED
        assert "error" not in [n for n, _ in record.log.events]
        assert "없는-서비스" not in json.dumps(record.log.events, ensure_ascii=False)

        values = (await env.graph.aget_state(thread_config(record.request_id))).values
        assert values["outcome"] == "manual_review" and values["message"] == record.message  # 재시작 뒤에도 보인다
        (line,) = audit_lines(env)
        assert (line["event"], line["stage"], line["request_id"]) == ("manual_review", "parse_request", record.request_id)
        assert "없는-서비스" not in json.dumps(line, ensure_ascii=False)


async def test_예상하지_못한_오류는_error_이벤트이고_예외_내용은_어디에도_남지_않는다(mcp, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)

    async def boom(state, config):
        raise RuntimeError("내부 secret-token=abc123 노출 금지")

    monkeypatch.setattr(graph_module, "decide", boom)
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as env:
        record = await submit(env, "REQ-D01")

        assert record.log.events[-1] == ("error", {"code": "internal", "message": INTERNAL_MESSAGE})
        assert record.status == "manual_review" and record.nodes["decide"].status == "failed"
        assert "done" not in [n for n, _ in record.log.events]
        blob = json.dumps(record.log.events, ensure_ascii=False) + env.audit.path.read_text(encoding="utf-8") + caplog.text
        assert "abc123" not in blob
        assert "RuntimeError" in caplog.text  # 타입 이름은 남긴다
        assert audit_lines(env)[0]["stage"] == "RuntimeError"
        assert (await env.graph.aget_state(thread_config(record.request_id))).values["outcome"] == "manual_review"


async def test_제한_시간을_넘으면_수동_검토로_넘긴다(mcp, tmp_path, monkeypatch):
    async def slow(state, config):
        await asyncio.sleep(5)

    monkeypatch.setattr(graph_module, "decide", slow)
    queue("REQ-D01")
    async with environment(mcp, tmp_path, timeout_s=0.3) as env:
        record = await submit(env, "REQ-D01")
        assert record.status == "manual_review" and record.message == TIMEOUT_MESSAGE
        assert record.log.events[-1][1]["message"] == TIMEOUT_MESSAGE and record.log.events[-1][0] == "done"
        assert audit_lines(env)[0]["stage"] == "timeout"


# ---------------------------------------------------------------------------
# 서버를 다시 띄운 뒤의 복구
# ---------------------------------------------------------------------------


async def test_재시작하면_체크포인터에서_승인_대기와_자동_승인_요청을_복구하고_이어서_결정할_수_있다(mcp, tmp_path):
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as first:
        waiting = await submit(first, "REQ-D01")
        queue("REQ-D04")
        auto = await submit(first, "REQ-D04")
        assert (waiting.request_id, auto.request_id) == ("REQ-0001", "REQ-0002")

    async with environment(mcp, tmp_path) as second:  # 서버를 다시 띄운다
        assert await rehydrate(second.store, second.graph, second.saver, second.audit) == []
        restored = second.store.get("REQ-0001")
        assert (restored.status, restored.requester, restored.own_text) == ("awaiting_approval", waiting.requester, waiting.own_text)
        assert second.store.get("REQ-0002").status == "auto_approved"
        assert restored.nodes == {} and restored.log.events == []  # 진행 기록과 이벤트는 복구하지 않는다
        assert second.store.new_id() == "REQ-0003"  # 번호는 이어서 매긴다

        values = (await second.graph.aget_state(thread_config("REQ-0001"))).values
        assert views.terminal_event(restored, values)[0] == "awaiting_approval"

        await second.runner.resume(restored, approval("reject"))  # 재시작 전에 멈춘 요청을 이어서 재개한다
        assert restored.status == "rejected"
        assert restored.log.events[-1][1]["status"] == "rejected"
        assert sequence(restored).count(("recommendation", None)) == 0  # 권고는 이미 보여 준 뒤라 다시 보내지 않는다


async def test_실행_도중_서버가_꺼진_요청은_다시_뜰_때_수동_검토로_닫는다(mcp, tmp_path, monkeypatch):
    async def slow(state, config):
        await asyncio.sleep(30)

    monkeypatch.setattr(graph_module, "decide", slow)
    queue("REQ-D01")
    async with environment(mcp, tmp_path) as first:
        record, graph_input = new_record(first, "REQ-D01")
        first.runner.start(record, graph_input)
        for _ in range(200):  # decide가 돌기 시작할 때까지
            if "decide" in record.nodes:
                break
            await asyncio.sleep(0.02)
        assert record.nodes["decide"].status == "running"
        await first.runner.shutdown()  # 서버가 꺼진다
        assert record.status == "reviewing"  # 꺼지는 중에는 실패로 표시하지 않는다

    monkeypatch.undo()
    async with environment(mcp, tmp_path) as second:
        closed = await rehydrate(second.store, second.graph, second.saver, second.audit)
        assert closed == ["REQ-0001"]
        restored = second.store.get("REQ-0001")
        assert (restored.status, restored.message) == ("manual_review", RESTART_MESSAGE)
        values = (await second.graph.aget_state(thread_config("REQ-0001"))).values
        assert (values["outcome"], values["message"]) == ("manual_review", RESTART_MESSAGE)
        (line,) = audit_lines(second)
        assert (line["event"], line["stage"]) == ("manual_review", "restart")

    async with environment(mcp, tmp_path) as third:  # 한 번 닫은 요청은 다시 뜰 때 또 닫지 않는다
        assert await rehydrate(third.store, third.graph, third.saver, third.audit) == []
        assert third.store.get("REQ-0001").status == "manual_review"
