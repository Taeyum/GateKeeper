"""그래프 상태 (docs/SPEC.md 2장).

4단계에는 v0이 쓰는 필드만 둔다. route, policy, impact, history, validation, approval은 v1 이후
단계가 필요할 때 더한다. errors와 metrics에 reducer를 지금 두는 것은 v1의 병렬 서브에이전트가
같은 키를 동시에 쓸 때 InvalidUpdateError가 나지 않게 하려는 것이다.
"""

import operator
from typing import Annotated, TypedDict

from app.schemas import ChangeRequest, Recommendation


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
    recommendation: Recommendation
    errors: Annotated[list[str], operator.add]
    metrics: Annotated[dict[str, int], add_counters]
