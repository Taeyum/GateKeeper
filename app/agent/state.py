"""그래프 상태 (docs/SPEC.md 2장).

4단계에는 v0이 쓰는 필드만 뒀고, 5단계부터 v1 노드가 쓰는 필드를 더한다. policy, impact, history, validation,
approval은 쓰는 노드가 생길 때 더한다. errors와 metrics에 reducer를 지금 두는 것은 v1의 병렬 서브에이전트가
같은 키를 동시에 쓸 때 InvalidUpdateError가 나지 않게 하려는 것이다.
"""

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from app.agent.results import AlternativePlan, ApprovalRecord, Outcome, SubagentResult, ValidationState
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
    # history_agent는 v2에서만 돈다. similar_incidents는 docs/API_CONTRACT.md의 모양(history.json 원래 키)이고
    # 정확 일치를 먼저 둔 상위 3건이다. has_similar_incident는 코드가 이력 전체에서 "같은 서비스 + 같은 유형 + 장애"로
    # 정한 값이며 위험도를 한 단계 올리는 근거다. v0, v1과 이력 검색이 실패한 경우에는 없다(없으면 False로 본다).
    history: SubagentResult
    similar_incidents: list[dict[str, Any]]
    has_similar_incident: bool
    recommendation: Recommendation
    # ④ decide가 대안 문장을 조립하기 전의 구성 요소 (7단계 API의 alternative_diff 계산용)
    alternative: AlternativePlan
    # ⑤ validate 결과 (v2). 실패하면 사유를 들고 decide로 1회 돌아간다.
    validation: ValidationState
    # ⑥ approval_gate와 ⑦ finalize 결과 (7단계). 평가 하네스의 그래프는 여기까지 오지 않는다.
    # approval은 승인자가 결정한 뒤에만 있고(자동 승인이면 None), outcome이 요청이 끝난 모양을 말한다.
    # API가 검토를 끝내지 못한 요청을 manual_review로 표시할 때도 outcome과 message를 상태에 남겨 서버를 다시 띄워도 보이게 한다.
    approval: ApprovalRecord | None
    outcome: Outcome
    decided_at: str
    message: str
    # API가 넣는 요청 정보. request_text는 이번에 새로 쓴 본문(마스킹)이고, 재요청이면 masked_text는 부모 본문과 합친 문장이다.
    # raw_text는 상태에 두지 않는다. 마스킹 미들웨어가 앞에서 가려서 원문이 서버 안으로 들어오지 않는다.
    parent_request_id: str | None
    request_text: str
    # 그래프 루트 실행의 run_id와 같은 값. 감사 로그와 LangSmith trace를 잇는다.
    trace_id: str
    # 승인자에게 보여 줄 경고. 노드마다 더하고 decide가 Recommendation.warnings로 옮긴다.
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    metrics: Annotated[dict[str, int], add_counters]
