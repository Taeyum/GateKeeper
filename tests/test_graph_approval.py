"""승인 노드를 붙인 그래프 통합 테스트 (fake LLM, 메모리 내 MCP와 FakeEmbedder 색인, 실제 규칙 엔진, 실제 SQLite).

두 가지를 본다. 하나, 평가용 그래프(approval 없음)가 7단계 전과 같은 모양이라 측정 지점이 그대로인 것. 둘, 승인을 붙이면
일반 변경은 사람에게서 멈추고 표준 변경만 자동 승인되며(리뷰 K3 포함), 결정이 감사 로그에 남는 것.
"""

import json
import logging

import pytest
from langgraph.types import Command

from app import data_loader
from app.agent.graph import build_graph
from app.agent.mcp_client import MCPClient
from app.agent.nodes.classify import MISMATCH_WARNING
from app.audit import AuditLog
from app.mcp_server.server import build_server
from app.persistence.checkpointer import open_checkpointer, thread_config
from eval.harness import initial_state
from tests._graph_helpers import gold, queue, reset_serde_warnings, serde_warnings
from tests._history import make_index

DECISION = {"decision": "approve", "approver": "박CAB", "comment": "확인했습니다", "at": "2026-10-29T16:20:00+09:00"}


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


@pytest.fixture
async def mcp():
    async with MCPClient(build_server(history_index=make_index()), base_delay=0.01) as client:
        yield client


@pytest.fixture
async def saver(tmp_path):
    async with open_checkpointer(tmp_path / "checkpoints.sqlite") as opened:
        yield opened


@pytest.fixture
def audit(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl")


def audit_lines(audit: AuditLog) -> list[dict]:
    if not audit.path.exists():
        return []
    return [json.loads(line) for line in audit.path.read_text(encoding="utf-8").splitlines()]


async def start(graph, request_id: str) -> dict:
    item = gold(request_id)
    return await graph.ainvoke(initial_state(item, item.text), thread_config(f"test-{request_id}"), durability="sync")


async def snapshot(graph, request_id: str):
    return await graph.aget_state(thread_config(f"test-{request_id}"))


def node_names(graph) -> set[str]:
    return set(graph.get_graph().nodes) - {"__start__", "__end__"}


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------


def test_평가용_그래프는_승인_노드가_없다_측정_지점_유지(mcp):
    assert node_names(build_graph("v0")) == {"baseline_v0"}
    assert node_names(build_graph("v1", mcp=mcp)) == {"parse_request", "classify", "policy_agent", "impact_agent", "decide"}
    assert node_names(build_graph("v2", mcp=mcp)) == {
        "parse_request", "classify", "policy_agent", "impact_agent", "history_agent", "decide", "validate",
    }  # fmt: skip


def test_평가용_그래프의_끝은_그대로다(mcp):
    """validate의 조건부 엣지가 END로 가는 것도, decide가 END로 가는 것도 7단계 전과 같다."""
    v2 = {(e.source, e.target, e.conditional) for e in build_graph("v2", mcp=mcp).get_graph().edges}
    assert ("validate", "__end__", True) in v2 and ("validate", "decide", True) in v2
    v1 = {(e.source, e.target) for e in build_graph("v1", mcp=mcp).get_graph().edges}
    assert ("decide", "__end__") in v1


def test_승인을_붙이면_마지막_노드_뒤에_approval_gate와_finalize가_붙는다(mcp, saver, audit):
    graph = build_graph("v2", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
    assert {"approval_gate", "finalize"} <= node_names(graph)
    edges = {(e.source, e.target, e.conditional) for e in graph.get_graph().edges}
    assert ("validate", "approval_gate", True) in edges  # 검증을 통과했거나 "미완료"로 끝난 뒤에만 승인 단계로 간다
    assert ("validate", "decide", True) in edges  # 재작성 경로는 그대로
    assert ("approval_gate", "finalize", False) in edges and ("finalize", "__end__", False) in edges
    assert ("validate", "__end__", True) not in edges


def test_승인은_체크포인터와_감사_로그가_없으면_만들_수_없다(mcp, saver, audit):
    with pytest.raises(ValueError, match="checkpointer와 audit"):
        build_graph("v2", mcp=mcp, approval=True)
    with pytest.raises(ValueError, match="checkpointer와 audit"):
        build_graph("v2", mcp=mcp, checkpointer=saver, approval=True)
    with pytest.raises(ValueError, match="checkpointer와 audit"):
        build_graph("v2", mcp=mcp, audit=audit, approval=True)


# ---------------------------------------------------------------------------
# 사람 승인
# ---------------------------------------------------------------------------


async def test_일반_변경은_권고까지_만들고_승인자_앞에서_멈춘다(mcp, saver, audit):
    graph = build_graph("v2", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
    queue("REQ-D01")
    await start(graph, "REQ-D01")

    waiting = await snapshot(graph, "REQ-D01")
    assert waiting.next == ("approval_gate",)
    assert waiting.values["recommendation"].verdict == "반려"
    assert "outcome" not in waiting.values
    assert audit_lines(audit) == []  # 결정 전에는 감사 기록이 없다


async def test_승인자가_결정하면_결과가_상태와_감사_로그에_남고_권고는_바뀌지_않는다(mcp, saver, audit):
    graph = build_graph("v2", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
    queue("REQ-D01")
    await start(graph, "REQ-D01")
    before = (await snapshot(graph, "REQ-D01")).values["recommendation"]

    await graph.ainvoke(Command(resume=DECISION), thread_config("test-REQ-D01"), durability="sync")
    done = await snapshot(graph, "REQ-D01")
    assert done.next == ()
    assert done.values["outcome"] == "approved"  # 사람은 시스템 권고(반려)와 다르게 결정할 수 있다
    assert done.values["approval"].approver == "박CAB"
    assert done.values["recommendation"] == before

    (record,) = audit_lines(audit)
    assert record["event"] == "decision"
    assert (record["request_id"], record["verdict"], record["outcome"], record["approver"]) == ("REQ-D01", "반려", "approved", "박CAB")
    assert record["violations"] == ["CHG-01", "CHG-02", "CHG-03", "CHG-09", "CHG-10"]


async def test_반려_결정도_남는다(mcp, saver, audit):
    graph = build_graph("v2", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
    queue("REQ-D01")
    await start(graph, "REQ-D01")
    await graph.ainvoke(Command(resume={**DECISION, "decision": "reject"}), thread_config("test-REQ-D01"), durability="sync")
    assert (await snapshot(graph, "REQ-D01")).values["outcome"] == "rejected"
    assert audit_lines(audit)[0]["decision"] == "reject"


async def test_표준_변경은_멈추지_않고_자동_승인되어_감사_로그에_남는다(mcp, saver, audit):
    graph = build_graph("v2", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
    queue("REQ-D04")
    await start(graph, "REQ-D04")

    done = await snapshot(graph, "REQ-D04")
    assert done.next == ()
    assert (done.values["route"], done.values["recommendation"].verdict) == ("standard", "자동 승인")
    assert (done.values["outcome"], done.values["approval"]) == ("auto_approved", None)
    (record,) = audit_lines(audit)
    assert (record["event"], record["standard_change_id"], record["approver"]) == ("auto_approved", "STD-01", None)


async def test_K3_작은_모델이_표준_변경을_배포로_분류하면_자동_승인하지_않고_사람에게_보낸다(mcp, saver, audit):
    graph = build_graph("v2", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
    queue("REQ-D04", small_change_type="deploy")  # 엔진은 표준 변경으로 확정, 작은 모델은 배포라고 답한다
    await start(graph, "REQ-D04")

    waiting = await snapshot(graph, "REQ-D04")
    assert waiting.values["route"] == "general"
    assert waiting.values["standard_change_id"] == "STD-01"  # 엔진 확정값은 유지한다
    assert waiting.values["recommendation"].verdict == "자동 승인"  # 판정도 엔진 그대로
    assert MISMATCH_WARNING in waiting.values["recommendation"].warnings
    assert waiting.next == ("approval_gate",)  # 승인만 사람이 한다
    assert audit_lines(audit) == []


async def test_서브그래프_체크포인트까지_모두_경고_없이_읽힌다(mcp, saver, audit, caplog, monkeypatch):
    """직렬화 허용 목록에서 서브그래프 상태의 모델이 빠지면 그 체크포인트의 값이 조용히 막힌다(Blocked deserialization)."""
    reset_serde_warnings(monkeypatch)
    caplog.set_level(logging.WARNING)
    graph = build_graph("v2", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
    queue("REQ-D01")
    await start(graph, "REQ-D01")

    checkpoints = [checkpoint async for checkpoint in saver.alist(None)]  # 부모와 서브그래프의 체크포인트 전부
    assert len({c.config["configurable"].get("checkpoint_ns", "") for c in checkpoints}) > 1  # 서브그래프 것이 실제로 있다
    assert serde_warnings(caplog) == []


async def test_v1_그래프에도_같은_승인_단계가_붙는다(mcp, saver, audit):
    graph = build_graph("v1", mcp=mcp, checkpointer=saver, audit=audit, approval=True)
    queue("REQ-D06")
    await start(graph, "REQ-D06")
    assert (await snapshot(graph, "REQ-D06")).next == ("approval_gate",)
    await graph.ainvoke(Command(resume=DECISION), thread_config("test-REQ-D06"), durability="sync")
    assert (await snapshot(graph, "REQ-D06")).values["outcome"] == "approved"
    assert audit_lines(audit)[0]["event"] == "decision"
