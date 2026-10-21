"""⑥ approval_gate: 자동 승인 조건(리뷰 K3)과 interrupt 재개 (docs/SPEC.md 3장).

interrupt는 그래프 안에서만 동작하므로 노드 하나짜리 그래프에 체크포인터를 붙여 확인한다.
"""

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import ValidationError

from app.agent.nodes.approval_gate import approval_gate, auto_approves
from app.agent.results import ApprovalRecord
from app.agent.state import GKState
from app.persistence.checkpointer import open_checkpointer, thread_config
from app.schemas import Recommendation

AUTO = Recommendation(verdict="자동 승인", risk=None, explanation="표준 변경")
REJECT = Recommendation(verdict="반려", risk="높음", explanation="금지 시간대")
DECISION = {"decision": "approve", "approver": "박CAB", "comment": "확인했습니다", "at": "2026-10-29T16:20:00+09:00"}


def gate_graph(saver):
    graph = StateGraph(GKState)
    graph.add_node("approval_gate", approval_gate)
    graph.add_edge(START, "approval_gate")
    graph.add_edge("approval_gate", END)
    return graph.compile(checkpointer=saver)


# ---------------------------------------------------------------------------
# 자동 승인 조건
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "verdict", "expected"),
    [
        ("standard", "자동 승인", True),
        ("general", "자동 승인", False),  # K3: 분류 불일치로 general이 된 표준 변경은 사람에게 간다
        ("emergency", "자동 승인", False),
        ("standard", "반려", False),
        ("standard", "조건부 승인", False),
        ("standard", "승인", False),
        ("general", "승인", False),
        ("general", "반려", False),
    ],
)
def test_자동_승인은_표준_경로이고_판정이_자동_승인일_때만이다(route, verdict, expected):
    state = {"route": route, "recommendation": Recommendation(verdict=verdict)}
    assert auto_approves(state) is expected


def test_경로나_권고가_없으면_자동_승인하지_않는다():
    assert auto_approves({"recommendation": AUTO}) is False  # v0은 route가 없다
    assert auto_approves({"route": "standard"}) is False


async def test_자동_승인이면_멈추지_않고_승인_기록_없이_결과만_남긴다():
    update = await approval_gate({"request_id": "REQ-0001", "route": "standard", "recommendation": AUTO})
    assert update["approval"] is None
    assert update["outcome"] == "auto_approved"
    assert update["decided_at"].endswith("+09:00")


# ---------------------------------------------------------------------------
# interrupt와 재개
# ---------------------------------------------------------------------------


async def test_사람이_결정할_요청은_멈추고_결정으로_재개한다():
    async with open_checkpointer() as saver:
        graph = gate_graph(saver)
        config = thread_config("REQ-0001")
        await graph.ainvoke({"request_id": "REQ-0001", "route": "general", "recommendation": REJECT}, config)

        waiting = await graph.aget_state(config)
        assert waiting.next == ("approval_gate",)
        assert [i.value for i in waiting.tasks[0].interrupts] == [{"request_id": "REQ-0001", "verdict": "반려", "risk": "높음"}]
        assert "approval" not in waiting.values and "outcome" not in waiting.values  # 결정 전에는 기록이 없다

        await graph.ainvoke(Command(resume=DECISION), config)
        done = await graph.aget_state(config)
        assert done.next == ()
        assert done.values["approval"] == ApprovalRecord(**DECISION)
        assert (done.values["outcome"], done.values["decided_at"]) == ("approved", DECISION["at"])
        assert done.values["recommendation"] == REJECT  # 사람의 결정이 시스템 권고를 바꾸지 않는다


async def test_반려_결정은_rejected로_남는다():
    async with open_checkpointer() as saver:
        graph = gate_graph(saver)
        config = thread_config("REQ-0001")
        await graph.ainvoke({"route": "general", "recommendation": REJECT}, config)
        await graph.ainvoke(Command(resume={**DECISION, "decision": "reject"}), config)
        assert (await graph.aget_state(config)).values["outcome"] == "rejected"


async def test_분류가_어긋난_표준_변경은_판정이_자동_승인이어도_사람을_거친다():
    async with open_checkpointer() as saver:
        graph = gate_graph(saver)
        config = thread_config("REQ-0001")
        await graph.ainvoke({"route": "general", "standard_change_id": "STD-01", "recommendation": AUTO}, config)
        waiting = await graph.aget_state(config)
        assert waiting.next == ("approval_gate",)
        assert waiting.values["recommendation"].verdict == "자동 승인"  # 엔진 판정은 그대로, 승인만 사람이 한다


@pytest.mark.parametrize(
    "bad",
    [
        {**DECISION, "decision": "maybe"},
        {**DECISION, "approver": ""},
        {**DECISION, "comment": "가" * 1001},
        {"decision": "approve"},
    ],
)
async def test_잘못된_재개_값은_받지_않는다(bad):
    async with open_checkpointer() as saver:
        graph = gate_graph(saver)
        config = thread_config("REQ-0001")
        await graph.ainvoke({"route": "general", "recommendation": REJECT}, config)
        with pytest.raises(ValidationError):
            await graph.ainvoke(Command(resume=bad), config)
