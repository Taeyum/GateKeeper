"""② classify: 검사 경로를 정한다 (docs/SPEC.md 3장).

규칙 엔진이 표준 변경 여부(is_standard)와 긴급 유효성(emergency_valid)을 확정하고, 작은 모델은 본문만 보고
유형과 긴급 여부를 따로 분류해 parse_request 결과를 한 번 더 점검한다. 작은 모델의 의견은 확정값을 바꾸지 않는다.

route
    standard   엔진이 표준 변경으로 확정 (정책 검사만 돈다)
    emergency  엔진이 유효한 긴급 변경으로 확정
    general    그 밖. 검사를 더 돈다(영향도 검사까지)는 뜻일 뿐이다
작은 모델이 표준/긴급 여부에서 parse 결과와 다르거나 호출이 실패하면 route만 general로 돌린다(fail-safe).
이때도 standard_change_id와 emergency_valid는 엔진 확정값 그대로다. 지우면 작은 모델이 틀릴 때마다 표준 변경의
판정이 "자동 승인"에서 "승인"으로 바뀌어 작은 모델의 오류가 판정에 새어 든다(판정은 코드가 정한다, 리뷰 K3).
대신 불일치는 warnings로 남기고, 7단계 approval_gate는 route가 standard일 때만 자동 승인을 실행한다.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app import rules_engine
from app.agent.prompting import load_prompt, render_standard_changes
from app.agent.state import GKState
from app.config import get_settings
from app.llm.factory import get_llm, structured_llm
from app.schemas import ChangeRequest, ChangeType, Route

MISMATCH_WARNING = "분류 불일치: 사람 확인 필요 (작은 모델의 1차 분류가 요청 구조화 결과와 표준/긴급 여부에서 다르다)"


class RouteGuess(BaseModel):
    """작은 모델의 1차 분류. 선택 필드 없이 anthropic에도 그대로 보낸다."""

    change_type: ChangeType = Field(description="변경 유형")
    is_emergency: bool = Field(description="요청자가 긴급이라고 표시했는지")


def build_messages(state: GKState) -> list:
    system = load_prompt("classify.md").format(standard_changes=render_standard_changes())
    return [SystemMessage(content=system), HumanMessage(content=f"요청 본문:\n{state['masked_text']}")]


def disagrees(guess: RouteGuess, request: ChangeRequest) -> bool:
    """표준 변경 여부나 긴급 여부가 다르면 True. 방화벽 대 배포 같은 유형 차이는 경로에 영향이 없어 보지 않는다."""
    return (guess.change_type == "standard") != (request.change_type == "standard") or (
        guess.is_emergency != request.is_emergency
    )


def decide_route(is_standard: bool, emergency_is_valid: bool, flagged: bool) -> Route:
    """flagged는 작은 모델 의견을 못 믿을 때(불일치, 호출 실패)다. 확정값과 무관하게 general로 돌린다."""
    if flagged:
        return "general"
    if is_standard:
        return "standard"
    return "emergency" if emergency_is_valid else "general"


async def classify(state: GKState, config: RunnableConfig) -> dict:
    request = state["structured"]
    standard = rules_engine.is_standard(request)
    emergency = rules_engine.emergency_valid(request) if request.is_emergency else None

    settings = get_settings()
    chain = structured_llm(get_llm("small", settings), RouteGuess, settings)
    metrics = {"route_disagreements": 0}
    warnings: list[str] = []
    errors: list[str] = []
    flagged = False
    try:
        guess = await chain.ainvoke(build_messages(state), config)
    except Exception as exc:  # 분류는 보조 의견이다. 어떤 실패든 확정값은 엔진이 갖고 있으므로 general로 돌리고 진행한다
        flagged = True
        errors.append(f"classify: 작은 모델 1차 분류 실패 ({type(exc).__name__})")
        metrics["partial_failures"] = 1
    else:
        if disagrees(guess, request):
            flagged = True
            warnings.append(MISMATCH_WARNING)
            metrics["route_disagreements"] = 1

    return {
        "route": decide_route(standard.is_standard, emergency is not None and emergency.valid, flagged),
        "standard_change_id": standard.standard_change_id,
        "emergency_valid": emergency.valid if emergency is not None else None,
        "warnings": warnings,
        "errors": errors,
        "metrics": metrics,
    }
