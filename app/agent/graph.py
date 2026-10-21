"""GK_VERSION에 따라 그래프를 조립한다 (docs/SPEC.md 4장).

버전마다 저장소를 나누지 않고 이 함수 하나에서 고른다 (CLAUDE.md 절대 규칙 6). 모든 버전은
같은 상태(GKState)로 같은 출력(structured, recommendation)을 낸다. 평가 하네스가 버전과 무관하게
`await graph.ainvoke(state)` 하나로 측정할 수 있게 하려는 것이다.
"""

from langgraph.graph import END, START, StateGraph

from app.agent.mcp_client import MCPClient
from app.agent.nodes.baseline_v0 import baseline_v0
from app.agent.nodes.classify import classify
from app.agent.nodes.decide import decide
from app.agent.nodes.impact_agent import make_impact_agent
from app.agent.nodes.parse_request import parse_request
from app.agent.nodes.policy_agent import make_policy_agent
from app.agent.state import GKState
from app.config import GKVersion, get_settings


def _build_v0():
    graph = StateGraph(GKState)
    graph.add_node("baseline_v0", baseline_v0)
    graph.add_edge(START, "baseline_v0")
    graph.add_edge("baseline_v0", END)
    return graph.compile()


def route_after_classify(state: GKState) -> list[str]:
    """표준 변경은 정책 검사만 돈다 (규정 3장: 표준 변경은 영향도와 이력 검사를 생략한다). 그 밖은 병렬로 돈다."""
    return ["policy_agent"] if state["route"] == "standard" else ["policy_agent", "impact_agent"]


def _build_v1(mcp: MCPClient):
    """① parse_request → ② classify → ③ policy_agent, impact_agent (병렬 서브그래프) → ④ decide.

    ⑥ approval_gate와 ⑦ finalize는 체크포인터, interrupt와 함께 7단계에서 붙인다. 평가의 측정 지점(⑥ 직전)과
    같은 곳에서 끝난다. decide로 가는 엣지는 서브에이전트마다 따로 둔다. 둘을 한 묶음(리스트)으로 이으면 모두 끝나기를
    기다려서, impact_agent가 돌지 않는 표준 변경 경로에서 decide가 영영 시작하지 못한다.
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
    graph.add_edge("decide", END)
    return graph.compile()


def build_graph(version: GKVersion | None = None, *, mcp: MCPClient | None = None):
    """version이 None이면 설정(GK_VERSION)을 따른다. 체크포인터는 7단계에서 인자로 더한다.

    v1은 서브에이전트가 쓰는 MCP 클라이언트가 필요하다. 호출자(평가 하네스, 7단계 API의 lifespan)가 한 번 만들어 넘긴다.
    """
    version = version or get_settings().gk_version
    if version == "v0":
        return _build_v0()
    if version == "v1":
        if mcp is None:
            raise ValueError("v1 그래프는 MCP 클라이언트(mcp=)가 필요하다.")
        return _build_v1(mcp)
    if version == "v2":
        raise NotImplementedError("v2 그래프는 아직 없다 (6단계).")
    raise ValueError(f"알 수 없는 버전: {version!r}")
