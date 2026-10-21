"""SQLite 체크포인터 팩토리 (docs/SPEC.md 6장).

승인 대기가 서버 재시작을 견디는지, pydantic 상태가 경고 없이 저장되는지를 실제 SQLite 파일로 확인한다.
그래프는 6단계까지의 상태(GKState)와 같은 모델을 쓰는 작은 그래프다. 병렬 서브에이전트처럼 래퍼 노드가
서브그래프를 부르는 모양도 흉내 내서, 서브그래프 체크포인트가 섞여도 thread 목록이 맞는지 본다.
"""

import logging
from datetime import datetime

import pytest
from langgraph.checkpoint.serde import jsonplus
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from app.agent.results import AlternativePlan, RuleNote, SubagentResult, ValidationState
from app.agent.state import GKState
from app.persistence.checkpointer import (
    list_thread_ids,
    make_serde,
    open_checkpointer,
    state_allowlist,
    state_models,
    thread_config,
)
from app.schemas import ChangeRequest, Recommendation, Violation

REQUEST = ChangeRequest(target="payments-db", change_type="firewall")
RECOMMENDATION = Recommendation(
    violations=[Violation(rule_id="CHG-01", severity="forbidden", evidence="금요일 저녁이라 걸린다")],
    verdict="반려",
    risk="높음",
    explanation="설명",
)


def build(saver):
    """parse_request -> policy_agent(서브그래프를 부르는 래퍼) -> decide -> approval_gate(interrupt)."""

    async def inner(state: dict) -> dict:
        return {"result": SubagentResult(summary="정책 요약", tool_output={"k": [1, 2]})}

    sub = StateGraph(dict)
    sub.add_node("check_policy", inner)
    sub.add_edge(START, "check_policy")
    sub.add_edge("check_policy", END)
    sub = sub.compile(name="policy_agent")

    async def parse_request(state: GKState) -> dict:
        return {"structured": REQUEST}

    async def policy_agent(state: GKState, config) -> dict:
        out = await sub.ainvoke({}, config)
        return {"policy": out["result"]}

    async def decide(state: GKState) -> dict:
        return {"recommendation": RECOMMENDATION}

    async def approval_gate(state: GKState) -> dict:
        answer = interrupt({"verdict": state["recommendation"].verdict})
        return {"warnings": [f"결정:{answer['decision']}"]}

    graph = StateGraph(GKState)
    graph.add_node("parse_request", parse_request)
    graph.add_node("policy_agent", policy_agent)
    graph.add_node("decide", decide)
    graph.add_node("approval_gate", approval_gate)
    graph.add_edge(START, "parse_request")
    graph.add_edge("parse_request", "policy_agent")
    graph.add_edge("policy_agent", "decide")
    graph.add_edge("decide", "approval_gate")
    graph.add_edge("approval_gate", END)
    return graph.compile(checkpointer=saver)


async def run_until_gate(graph, request_id: str) -> None:
    await graph.ainvoke({"request_id": request_id}, thread_config(request_id), durability="sync")


# ---------------------------------------------------------------------------
# 직렬화 허용 목록
# ---------------------------------------------------------------------------


def test_허용_목록은_GKState의_pydantic_모델을_중첩까지_모두_담는다():
    names = {model.__name__ for model in state_models()}
    # Recommendation 안의 Violation, SubagentResult 안의 RuleNote처럼 필드에 중첩된 모델도 포함한다
    assert {"ChangeRequest", "Recommendation", "Violation", "SubagentResult", "RuleNote", "ValidationState", "AlternativePlan"} <= names
    assert ("app.schemas", "ChangeRequest") in state_allowlist()


def test_허용_목록의_모델은_직렬화_왕복에서_같은_타입과_값으로_돌아온다():
    serde = make_serde()
    samples = [
        REQUEST,
        RECOMMENDATION,
        SubagentResult(summary="요약", notes=[RuleNote(rule_id="CHG-01", note="설명")]),
        AlternativePlan(start=datetime.fromisoformat("2026-11-03T14:00:00+09:00"), conditions=["조건"], fallback=True),
        ValidationState(passed=False, reasons=["이유"], rewrites=1),
    ]
    for sample in samples:
        assert serde.loads_typed(serde.dumps_typed(sample)) == sample


def test_허용_목록에_없는_모델은_되살리지_않는다():
    class Stranger(BaseModel):
        value: int = 1

    written = JsonPlusSerializer(allowed_msgpack_modules=True).dumps_typed(Stranger())
    assert not isinstance(make_serde().loads_typed(written), Stranger)


def test_thread_id는_request_id다():
    assert thread_config("REQ-0188") == {"configurable": {"thread_id": "REQ-0188"}}


# ---------------------------------------------------------------------------
# 재시작 뒤에도 승인 대기가 이어진다
# ---------------------------------------------------------------------------


async def test_앱을_닫았다_다시_열어도_대기_중인_요청을_이어서_재개한다(tmp_path):
    path = tmp_path / "아직 없는 폴더" / "checkpoints.sqlite"  # 폴더가 없으면 만든다
    config = thread_config("REQ-0001")

    async with open_checkpointer(path) as saver:
        graph = build(saver)
        await run_until_gate(graph, "REQ-0001")
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("approval_gate",)
        assert [i.value for i in snapshot.tasks[0].interrupts] == [{"verdict": "반려"}]

    assert path.exists()
    async with open_checkpointer(path) as saver:  # 서버 재시작과 같다: 새 연결, 새 그래프 객체
        graph = build(saver)
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("approval_gate",)
        assert snapshot.values["structured"] == REQUEST  # 모델이 dict가 아니라 원래 타입으로 돌아온다
        assert snapshot.values["recommendation"] == RECOMMENDATION
        assert snapshot.values["policy"].summary == "정책 요약"

        await graph.ainvoke(Command(resume={"decision": "reject"}), config, durability="sync")
        done = await graph.aget_state(config)
        assert done.next == ()
        assert done.values["warnings"] == ["결정:reject"]


async def test_thread_목록은_최신_순이고_서브그래프_체크포인트가_섞여도_중복이_없다(tmp_path):
    async with open_checkpointer(tmp_path / "cp.sqlite") as saver:
        graph = build(saver)
        await run_until_gate(graph, "REQ-0001")
        await run_until_gate(graph, "REQ-0002")
        assert await list_thread_ids(saver) == ["REQ-0002", "REQ-0001"]


async def test_메모리_모드도_같은_인터페이스다():
    async with open_checkpointer() as saver:
        graph = build(saver)
        await run_until_gate(graph, "REQ-0001")
        assert (await graph.aget_state(thread_config("REQ-0001"))).next == ("approval_gate",)


# ---------------------------------------------------------------------------
# 직렬화 경고
# ---------------------------------------------------------------------------


def unregistered_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "unregistered type" in r.getMessage()]


@pytest.fixture
def fresh_warning_state(monkeypatch):
    """LangGraph는 같은 타입의 경고를 프로세스에서 한 번만 낸다. 다른 테스트가 먼저 냈어도 이 테스트가 보게 비운다."""
    monkeypatch.setattr(jsonplus, "_warned_unregistered_types", set())


async def test_우리_팩토리는_pydantic_상태를_읽어도_미등록_타입_경고가_없다(tmp_path, caplog, fresh_warning_state):
    caplog.set_level(logging.WARNING)
    path = tmp_path / "cp.sqlite"
    async with open_checkpointer(path) as saver:
        await run_until_gate(build(saver), "REQ-0001")
    async with open_checkpointer(path) as saver:
        await build(saver).aget_state(thread_config("REQ-0001"))
    assert unregistered_warnings(caplog) == []


async def test_대조군_기본_saver는_같은_상태를_읽을_때_경고를_낸다(tmp_path, caplog, fresh_warning_state):
    """위 테스트가 경고를 실제로 잡아낼 수 있는지 확인한다. LangGraph가 경고를 없애면 이 테스트와 팩토리의 허용 목록을 함께 정리한다."""
    caplog.set_level(logging.WARNING)
    path = tmp_path / "cp.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await run_until_gate(build(saver), "REQ-0001")
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await build(saver).aget_state(thread_config("REQ-0001"))
    assert unregistered_warnings(caplog) != []
