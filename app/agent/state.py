"""그래프 상태 (docs/SPEC.md 2장).

4단계에는 v0이 쓰는 필드만 뒀고, 5단계부터 v1 노드가 쓰는 필드를 더한다. policy, impact, history, validation,
approval은 쓰는 노드가 생길 때 더한다. errors와 metrics에 reducer를 지금 두는 것은 v1의 병렬 서브에이전트가
같은 키를 동시에 쓸 때 InvalidUpdateError가 나지 않게 하려는 것이다.
"""

import operator
from typing import Annotated

from typing_extensions import TypedDict

from app.agent.results import AlternativePlan, SubagentResult
from app.schemas import ChangeRequest, Recommendation, Route, StandardId


def add_counters(left: dict[str, int] | None, right: dict[str, int] | None) -> dict[str, int]:
    """metrics 카운터를 키별로 더한다 (parse_retries, tool_retries, partial_failures ...)."""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        merged[key] = merged.get(key, 0) + value
    return merged


class GKState(TypedDict, total=False):
    # 요청 메타데이터. submitted_at은 KST ISO 문자열이다.
    request_id: str
    submitted_at: str
    requester: str
    # LLM과 추적에는 masked_text만 간다 (CLAUDE.md 절대 규칙 5). 평가 하네스는 raw_text를 넣지 않는다.
    raw_text: str
    masked_text: str
    # ① 구조화 결과와 ④ 권고. 평가 대상은 ⑥ approval_gate 직전의 recommendation이다.
    structured: ChangeRequest
    # ② classify 결과. standard_change_id와 emergency_valid는 규칙 엔진이 확정한 값이고, route는 어디까지 검사를 도는지만 정한다.
    route: Route
    standard_change_id: StandardId | None
    emergency_valid: bool | None
    # ③ 서브에이전트 결과. 각자 컴파일된 서브그래프이고 자기 상태를 가진다(nodes/policy_agent.py, impact_agent.py).
    policy: SubagentResult
    impact: SubagentResult
    recommendation: Recommendation
    # ④ decide가 대안 문장을 조립하기 전의 구성 요소 (7단계 API의 alternative_diff 계산용)
    alternative: AlternativePlan
    # 승인자에게 보여 줄 경고. 노드마다 더하고 decide가 Recommendation.warnings로 옮긴다.
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    metrics: Annotated[dict[str, int], add_counters]
