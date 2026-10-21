"""GK_VERSION에 따라 그래프를 조립한다 (docs/SPEC.md 4장).

버전마다 저장소를 나누지 않고 이 함수 하나에서 고른다 (CLAUDE.md 절대 규칙 6). 모든 버전은
같은 상태(GKState)로 같은 출력(structured, recommendation)을 낸다. 평가 하네스가 버전과 무관하게
`await graph.ainvoke(state)` 하나로 측정할 수 있게 하려는 것이다.
"""

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.baseline_v0 import baseline_v0
from app.agent.state import GKState
from app.config import GKVersion, get_settings


def _build_v0():
    graph = StateGraph(GKState)
    graph.add_node("baseline_v0", baseline_v0)
    graph.add_edge(START, "baseline_v0")
    graph.add_edge("baseline_v0", END)
    return graph.compile()


def build_graph(version: GKVersion | None = None):
    """version이 None이면 설정(GK_VERSION)을 따른다. 체크포인터는 7단계에서 인자로 더한다."""
    version = version or get_settings().gk_version
    if version == "v0":
        return _build_v0()
    if version in ("v1", "v2"):
        raise NotImplementedError(f"{version} 그래프는 아직 없다 (v1은 5단계, v2는 6단계).")
    raise ValueError(f"알 수 없는 버전: {version!r}")
