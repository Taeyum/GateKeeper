"""대안 품질 LLM-as-Judge (docs/EVAL.md 3장).

gold 판정이 반려/조건부 승인인 요청의 `conditional_alternative`만 평가한다. 모델은 MODEL_JUDGE
(`get_llm("judge")`)다. 시스템 모델과 같으면 자기 출력을 자기가 채점하는 것이라 summary에
judge_independent=false로 남기고 리포트가 주석을 단다.

Judge에는 v0과 같은 정적 컨텍스트(규정, CMDB, 변경 캘린더, 장애 티켓)를 준다. "새 위반을 만들지 않는가"를
판단하려면 대안 일정이 동결 기간이나 예정 변경과 겹치는지 볼 수 있어야 한다.
"""

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.agent.prompting import file_sha256, format_submitted_at, render_static_context
from app.config import Settings, get_settings
from app.llm.factory import get_llm, structured_llm
from app.schemas import LabeledRequest

JUDGE_PROMPT_PATH = Path(__file__).resolve().parent / "judge_prompt.md"
_ALTERNATIVE_VERDICTS = ("반려", "조건부 승인")


class JudgeScore(BaseModel):
    score: int = Field(ge=1, le=5, description="1~5점")
    reason: str = Field(description="근거 한두 문장")


def needs_judging(gold: LabeledRequest) -> bool:
    return gold.expected.verdict in _ALTERNATIVE_VERDICTS


def judge_prompt_hash() -> str:
    return file_sha256(JUDGE_PROMPT_PATH)


def judge_independent(settings: Settings | None = None) -> bool:
    """Judge 모델이 시스템(main) 모델과 다른가."""
    settings = settings or get_settings()
    return settings.model_for("judge") != settings.model_for("main")


def _system_prompt() -> str:
    context = render_static_context()
    template = JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(**{k: context[k] for k in ("policy", "cmdb", "calendar", "incidents")})


def _user_prompt(gold: LabeledRequest, masked_text: str, alternative: str) -> str:
    violations = ", ".join(gold.expected.violations) or "없음"
    return (
        f"요청 시각: {format_submitted_at(gold.submitted_at.isoformat())}\n"
        f"요청 본문:\n{masked_text}\n\n"
        f"정답 판정: {gold.expected.verdict}\n"
        f"정답 위반 조항: {violations}\n"
        f"정답 대안 방향(예시): {gold.expected.conditional_alternative or '없음'}\n\n"
        f"시스템이 제안한 대안:\n{alternative}"
    )


async def judge_alternative(
    gold: LabeledRequest,
    alternative: str | None,
    masked_text: str,
    *,
    config: RunnableConfig | None = None,
    settings: Settings | None = None,
) -> JudgeScore | None:
    """대안을 채점한다. 대안이 필요 없는 요청이면 None, 대안이 비었으면 호출 없이 1점."""
    if not needs_judging(gold):
        return None
    if not alternative or not alternative.strip():
        return JudgeScore(score=1, reason="대안이 없다.")
    settings = settings or get_settings()
    chain = structured_llm(get_llm("judge", settings), JudgeScore, settings)
    messages = [
        SystemMessage(content=_system_prompt()),
        HumanMessage(content=_user_prompt(gold, masked_text, alternative.strip())),
    ]
    try:
        return await chain.ainvoke(messages, config)
    except ValueError:
        # 구조화 출력이 틀리면 1회 재시도한다. 두 번째 실패는 호출한 쪽(하네스)이 Judge 오류로 기록한다.
        return await chain.ainvoke(messages, config)
