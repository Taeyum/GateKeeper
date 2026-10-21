"""⑤ validate: 근거 검증 (v2, docs/SPEC.md 3장).

decide가 쓴 권고의 설명이 규정 검사 결과와 모순되지 않는지 확인한다. 판정, 위반, 위험도는 코드가 정한 값이라 여기서
바꾸지 않는다. 검증하는 것은 LLM이 쓴 부분(설명, 조건부 대안)과 그것을 감싼 인용이다.

1. 코드 검사 (순수 함수 code_checks): 권고의 위반 목록이 규정 검사 결과와 같은지, 판정이 위반 목록으로 계산한 값과 같은지,
   인용한 조항 id가 규정/표준 목록에 있고 "위반 + (유효한 긴급이면 CHG-07) + 표준 id"와 정확히 같은지, 설명과 대안 문장이
   언급한 조항 번호가 위반/면제/인용에 있는지. 하나라도 어긋나면 LLM 검사 없이 실패다.
2. LLM 검사 (main 모델, MODEL_JUDGE는 쓰지 않는다. 그것은 평가 전용이다): 설명이 위반 목록과 판정에 모순되는지.
   LLM 호출이 실패하면 막지 않는다. 판정이 이미 코드로 정해졌으므로 errors와 경고에 남기고 통과시킨다.

실패하면 사유를 들고 decide로 1회 돌아간다(재작성). 두 번째도 실패하면 권고의 warnings에 "근거 검증 미완료"를 붙이고
진행한다. 재작성 횟수는 metrics의 validator_rewrites, 끝내 못 끝낸 횟수는 validator_incomplete다.
"""

import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from pydantic import BaseModel, Field

from app import rules_engine
from app.agent.prompting import load_prompt
from app.agent.results import ValidationState
from app.agent.state import GKState
from app.config import get_settings
from app.data_loader import load_rules
from app.llm.factory import get_llm, structured_llm
from app.rules_engine.emergency import RULE_ID as EMERGENCY_RULE_ID
from app.schemas import PolicyCheckResult, Recommendation, RulesConfig

MAX_REWRITES = 1
INCOMPLETE_WARNING = "근거 검증 미완료"
_RULE_MENTION = re.compile(r"\b(?:CHG|STD)-\d{2}\b")


class ValidationCheck(BaseModel):
    """LLM 검사가 돌려주는 출력. 선택 필드가 없어 anthropic에도 그대로 보낸다."""

    consistent: bool = Field(description="설명과 조건부 대안이 위반 목록과 판정에 모순되지 않으면 true")
    issues: list[str] = Field(description="모순이나 근거 없는 주장을 한 문장씩. 없으면 빈 목록")


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


def known_rule_ids(rules: RulesConfig) -> set[str]:
    return {spec.rule_id for spec in rules.rules} | {std.std_id for std in rules.standard_changes}


def code_checks(
    rec: Recommendation,
    check: PolicyCheckResult,
    *,
    emergency_is_valid: bool | None,
    standard_id: str | None,
    rules: RulesConfig,
) -> list[str]:
    """코드로 확인하는 어긋남. 비어 있으면 통과다."""
    reasons: list[str] = []
    known = known_rule_ids(rules)
    violated = [v.rule_id for v in check.violations]
    if [v.rule_id for v in rec.violations] != violated:
        reasons.append("권고의 위반 목록이 규정 검사 결과와 다르다.")
    expected_verdict = rules_engine.verdict_from(check.violations, standard_id is not None)
    if rec.verdict != expected_verdict:
        reasons.append(f"판정({rec.verdict})이 위반 목록으로 계산한 값({expected_verdict})과 다르다.")

    allowed = list(dict.fromkeys([*violated, *([EMERGENCY_RULE_ID] if emergency_is_valid else []), *([standard_id] if standard_id else [])]))
    unknown = [c for c in rec.citations if c not in known]
    if unknown:
        reasons.append(f"존재하지 않는 조항을 인용했다: {', '.join(unknown)}.")
    extra = [c for c in rec.citations if c in known and c not in allowed]
    if extra:
        reasons.append(f"위반 목록에 없는 조항을 인용했다: {', '.join(extra)}.")
    missing = [c for c in allowed if c not in rec.citations]
    if missing:
        reasons.append(f"위반 조항이 인용에서 빠졌다: {', '.join(missing)}.")

    text = " ".join(part for part in (rec.explanation, rec.conditional_alternative) if part)
    mentioned = list(dict.fromkeys(_RULE_MENTION.findall(text)))
    mentionable = set(allowed) | {v.rule_id for v in check.exempted}
    unknown_mentions = [m for m in mentioned if m not in known]
    if unknown_mentions:
        reasons.append(f"설명이 존재하지 않는 조항을 언급했다: {', '.join(unknown_mentions)}.")
    off_list = [m for m in mentioned if m in known and m not in mentionable]
    if off_list:
        reasons.append(f"설명이 위반 목록에 없는 조항을 언급했다: {', '.join(off_list)}.")
    return reasons


def render_validation_input(rec: Recommendation, check: PolicyCheckResult) -> str:
    severity = {"forbidden": "금지", "fixable": "보완"}
    violations = "\n".join(f"- {v.rule_id} ({severity.get(v.severity, v.severity)}): {v.evidence}" for v in rec.violations) or "- 없음"
    exempted = "\n".join(f"- {v.rule_id}: {v.evidence}" for v in check.exempted) or "- 없음"
    return (
        "## 코드가 계산한 결과 (바꾸지 않는다)\n"
        f"- 판정: {rec.verdict}\n- 위험도: {rec.risk or '없음'}\n\n"
        f"## 위반 조항 (규칙 엔진)\n{violations}\n\n"
        f"## 유효한 긴급 변경으로 면제된 조항\n{exempted}\n\n"
        f"## 검증할 설명\n{rec.explanation}\n\n"
        f"## 검증할 조건부 대안\n{rec.conditional_alternative or '없음'}"
    )


def route_after_validate(state: GKState) -> str:
    """실패했고 아직 끝내지 않았으면 decide로 돌아간다. 통과했거나 '검증 미완료'로 끝냈으면 그래프를 마친다."""
    validation = state.get("validation")
    return "decide" if validation is not None and not validation.passed and not validation.incomplete else END


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------


async def validate(state: GKState, config: RunnableConfig) -> dict:
    rec: Recommendation = state["recommendation"]
    check = PolicyCheckResult.model_validate(state["policy"].tool_output)
    previous = state.get("validation")
    used = previous.rewrites if previous is not None else 0
    errors: list[str] = []
    metrics: dict[str, int] = {}
    warnings: list[str] = []

    reasons = code_checks(
        rec,
        check,
        emergency_is_valid=state.get("emergency_valid"),
        standard_id=state.get("standard_change_id"),
        rules=load_rules(),
    )
    if not reasons:  # 코드 검사를 통과했을 때만 LLM 검사를 한다 (이미 실패면 호출을 아낀다)
        settings = get_settings()
        chain = structured_llm(get_llm("main", settings), ValidationCheck, settings)
        messages = [SystemMessage(content=load_prompt("validate.md")), HumanMessage(content=render_validation_input(rec, check))]
        try:
            verdict = await chain.ainvoke(messages, config)
        except Exception as exc:  # 판정은 이미 코드가 정했다. 검증을 못 했다는 사실만 남기고 막지 않는다
            errors.append(f"validate: LLM 검사 실패 ({type(exc).__name__})")
            metrics["partial_failures"] = 1
            warnings.append("근거 검증: LLM 검사를 하지 못했다. 코드 검사만 통과했다.")
        else:
            if not verdict.consistent:
                issues = [issue.strip() for issue in verdict.issues if issue.strip()]
                reasons = [f"설명 검증: {issue}" for issue in issues] or ["설명 검증: 설명이 위반 목록과 모순된다고 했지만 사유를 적지 않았다."]

    if not reasons:
        update: dict = {"validation": ValidationState(passed=True, rewrites=used), "errors": errors, "metrics": metrics}
        if warnings:
            update["recommendation"] = rec.model_copy(update={"warnings": list(dict.fromkeys([*rec.warnings, *warnings]))})
        return update

    if used < MAX_REWRITES:  # decide로 돌아가 설명을 고쳐 쓰게 한다
        return {
            "validation": ValidationState(passed=False, reasons=reasons, rewrites=used + 1),
            "errors": errors,
            "metrics": {**metrics, "validator_rewrites": 1},
        }

    note = f"{INCOMPLETE_WARNING}: 설명이 규정 검사 결과와 맞는지 끝내 확인하지 못했다. 승인자가 설명을 직접 확인해야 한다 ({'; '.join(reasons)})"
    return {
        "validation": ValidationState(passed=False, reasons=reasons, rewrites=used, incomplete=True),
        "recommendation": rec.model_copy(update={"warnings": list(dict.fromkeys([*rec.warnings, *warnings, note]))}),
        "errors": errors,
        "metrics": {**metrics, "validator_incomplete": 1},
    }
