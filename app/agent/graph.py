"""GK_VERSION에 따라 그래프를 조립한다 (docs/SPEC.md 4장).

버전마다 저장소를 나누지 않고 이 함수 하나에서 고른다 (CLAUDE.md 절대 규칙 6). 모든 버전은
같은 상태(GKState)로 같은 출력(structured, recommendation)을 낸다. 평가 하네스가 버전과 무관하게
`await graph.ainvoke(state)` 하나로 측정할 수 있게 하려는 것이다.

사람 승인(⑥ approval_gate)과 확정(⑦ finalize)은 선택이다. approval=True일 때만 마지막 노드 뒤에 붙고, 인자를 주지 않은
그래프는 7단계 이전과 같다. 평가 하네스는 승인 없이 ⑥ 직전에서 끝나는 그래프를 측정한다(측정 지점 유지).
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agent.mcp_client import MCPClient
from app.agent.nodes.approval_gate import approval_gate
from app.agent.nodes.baseline_v0 import baseline_v0
from app.agent.nodes.classify import classify
from app.agent.nodes.decide import decide
from app.agent.nodes.finalize import make_finalize
from app.agent.nodes.history_agent import make_history_agent
from app.agent.nodes.impact_agent import make_impact_agent
from app.agent.nodes.parse_request import parse_request
from app.agent.nodes.policy_agent import make_policy_agent
from app.agent.nodes.validate import route_after_validate, validate
from app.agent.state import GKState
from app.audit import AuditLog
from app.config import GKVersion, get_settings

APPROVAL_GATE = "approval_gate"


def _build_v0(finish: str) -> StateGraph:
    graph = StateGraph(GKState)
    graph.add_node("baseline_v0", baseline_v0)
    graph.add_edge(START, "baseline_v0")
    graph.add_edge("baseline_v0", finish)
    return graph


def route_after_classify(state: GKState) -> list[str]:
    """표준 변경은 정책 검사만 돈다 (규정 3장: 표준 변경은 영향도와 이력 검사를 생략한다). 그 밖은 병렬로 돈다."""
    return ["policy_agent"] if state["route"] == "standard" else ["policy_agent", "impact_agent"]


def _build_v1(mcp: MCPClient, finish: str) -> StateGraph:
    """① parse_request → ② classify → ③ policy_agent, impact_agent (병렬 서브그래프) → ④ decide.

    finish는 decide 다음 지점이다. 평가용 그래프는 END, 사람 승인을 붙이면 approval_gate다. decide로 가는 엣지는
    서브에이전트마다 따로 둔다. 둘을 한 묶음(리스트)으로 이으면 모두 끝나기를 기다려서, impact_agent가 돌지 않는 표준 변경
    경로에서 decide가 영영 시작하지 못한다.
    """
    graph = StateGraph(GKState)
    graph.add_node("parse_request", parse_request)
    graph.add_node("classify", classify)
    graph.add_node("policy_agent", make_policy_agent(mcp))
    graph.add_node("impact_agent", make_impact_agent(mcp))
    graph.add_node("decide", decide)
    graph.add_edge(START, "parse_request")
    graph.add_edge("parse_request", "classify")
    graph.add_conditional_edges("classify", route_after_classify, ["policy_agent", "impact_agent"])
    graph.add_edge("policy_agent", "decide")
    graph.add_edge("impact_agent", "decide")
    graph.add_edge("decide", finish)
    return graph


def route_after_classify_v2(state: GKState) -> list[str]:
    """표준 변경은 정책 검사만 돈다. 그 밖은 정책, 영향도, 이력 검토 세 서브에이전트가 병렬로 돈다."""
    return ["policy_agent"] if state["route"] == "standard" else ["policy_agent", "impact_agent", "history_agent"]


def _build_v2(mcp: MCPClient, finish: str) -> StateGraph:
    """v1에 ③ history_agent(병렬 세 번째 갈래)와 ⑤ validate를 더한 전체 그래프.

        ① parse_request → ② classify → ③ policy_agent / impact_agent / history_agent (병렬) → ④ decide → ⑤ validate
                                                                                     ↑__________| (실패하면 1회)

    validate는 표준 변경 경로에서도 돈다(docs/API_CONTRACT.md 경로 예시). 실패하면 사유를 들고 decide로 돌아가 설명을 다시
    쓰게 하고(최대 1회), 두 번째도 실패하면 "근거 검증 미완료" 경고를 붙여 끝낸다. 측정 지점(⑥ 직전)은 validate 뒤다.
    그래서 finish(END 또는 approval_gate)는 validate가 끝난 뒤의 지점이다. decide로 가는 엣지는 서브에이전트마다 따로 둔다
    (v1과 같은 이유). validate에서 돌아올 때는 이 엣지가 아니라 조건부 엣지로만 들어오므로 서브에이전트는 다시 돌지 않는다.
    """
    graph = StateGraph(GKState)
    graph.add_node("parse_request", parse_request)
    graph.add_node("classify", classify)
    graph.add_node("policy_agent", make_policy_agent(mcp))
    graph.add_node("impact_agent", make_impact_agent(mcp))
    graph.add_node("history_agent", make_history_agent(mcp))
    graph.add_node("decide", decide)
    graph.add_node("validate", validate)
    graph.add_edge(START, "parse_request")
    graph.add_edge("parse_request", "classify")
    graph.add_conditional_edges("classify", route_after_classify_v2, ["policy_agent", "impact_agent", "history_agent"])
    graph.add_edge("policy_agent", "decide")
    graph.add_edge("impact_agent", "decide")
    graph.add_edge("history_agent", "decide")
    graph.add_edge("decide", "validate")
    graph.add_conditional_edges("validate", route_after_validate, {"decide": "decide", END: finish})
    return graph


def _attach_approval(graph: StateGraph, audit: AuditLog) -> None:
    """마지막 노드 뒤에 ⑥ approval_gate → ⑦ finalize를 붙인다. 앞 노드들이 approval_gate로 이어 두었다."""
    graph.add_node(APPROVAL_GATE, approval_gate)
    graph.add_node("finalize", make_finalize(audit))
    graph.add_edge(APPROVAL_GATE, "finalize")
    graph.add_edge("finalize", END)


def build_graph(
    version: GKVersion | None = None,
    *,
    mcp: MCPClient | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    audit: AuditLog | None = None,
    approval: bool = False,
):
    """version이 None이면 설정(GK_VERSION)을 따른다.

    v1, v2는 서브에이전트가 쓰는 MCP 클라이언트가 필요하다. 호출자(평가 하네스, 7단계 API의 lifespan)가 한 번 만들어 넘긴다.

    approval=True이면 사람 승인(interrupt)과 확정(감사 로그)이 붙는다. interrupt는 상태를 저장해야 하므로 체크포인터가
    필요하고, finalize가 쓸 감사 로그도 받아야 한다. 평가 하네스는 둘 다 주지 않는다.
    """
    version = version or get_settings().gk_version
    if approval and (checkpointer is None or audit is None):
        raise ValueError("approval=True는 checkpointer와 audit이 필요하다. interrupt()가 상태를 저장하고 finalize가 감사 로그를 쓴다.")
    finish = APPROVAL_GATE if approval else END

    if version == "v0":
        graph = _build_v0(finish)
    elif version in ("v1", "v2"):
        if mcp is None:
            raise ValueError(f"{version} 그래프는 MCP 클라이언트(mcp=)가 필요하다.")
        graph = _build_v1(mcp, finish) if version == "v1" else _build_v2(mcp, finish)
    else:
        raise ValueError(f"알 수 없는 버전: {version!r}")

    if approval:
        _attach_approval(graph, audit)
    return graph.compile(checkpointer=checkpointer)
