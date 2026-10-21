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

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from pydantic import BaseModel, Field

from app import rules_engine
from app.agent.nodes.decide import ALTERNATIVE_VERDICTS, EXPIRY_RULE_ID, render_history_section
from app.agent.nodes.impact_agent import work_window
from app.agent.prompting import load_prompt
from app.agent.results import ValidationState
from app.agent.state import GKState
from app.config import get_settings
from app.data_loader import load_cmdb, load_rules
from app.llm.factory import get_llm, structured_llm
from app.rules_engine._format import fmt_range
from app.rules_engine.context import effective_change_type
from app.rules_engine.emergency import RULE_ID as EMERGENCY_RULE_ID
from app.schemas import ChangeRequest, PolicyCheckResult, Recommendation, RulesConfig

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


def render_facts(state: GKState, rec: Recommendation, rules: RulesConfig) -> str:
    """설명이 근거로 삼아도 되는 사실. decide가 설명을 쓸 때 본 것과 같은 자료다.

    검증자에게 판정, 위반, 설명만 보여 주면 설명이 정당하게 쓴 영향도, 유사 장애 이력, 작업 구간, 파트너 IP를
    전부 "입력에 없는 근거 없는 주장"으로 지적한다(dev 첫 측정에서 10건 중 7건이 이 이유로 "근거 검증 미완료"가 됐다).
    """
    request: ChangeRequest = state["structured"]
    service = load_cmdb().get_service(request.target)
    effective = effective_change_type(request.change_type, state.get("standard_change_id") is not None)
    window = work_window(request)
    impact = state.get("impact")
    if impact is None:
        impact_text = "해당 없음 (표준 변경은 영향도 검사를 생략한다)"
    else:
        impact_text = "확인 불가" if impact.status == "unavailable" else impact.summary
    notes = "\n".join(f"  - {n.rule_id}: {n.note}" for n in state["policy"].notes) or "  - 없음"
    parts = [
        f"- 요청 대상: {request.target} ({service.tier if service else '등급 불명'}), 변경 유형: {effective}",
        f"- 작업 구간: {fmt_range(*window) if window else '알 수 없음'}",
        f"- 구조화된 요청: {json.dumps(request.model_dump(mode='json', exclude_none=True), ensure_ascii=False)}",
        f"- 영향도 요약: {impact_text}",
        f"- 조항별 설명 (규정 원문을 읽은 보조 에이전트):\n{notes}",
    ]
    history_section = render_history_section(
        state.get("history"), list(state.get("similar_incidents", [])), bool(state.get("has_similar_incident", False))
    )
    if history_section is not None:
        parts.append("- 유사 장애 이력 (위험도 상향 여부는 코드가 이력 전체에서 정했다):\n" + "\n".join(f"  {line}" for line in history_section.splitlines()))
    plan = state.get("alternative")
    if plan is not None and plan.start is not None and plan.end is not None:
        parts.append(f"- 대안 작업 시각: {fmt_range(plan.start, plan.end)} (규칙 엔진이 검증했거나 엔진이 찾은 값)")
    if rec.verdict in ALTERNATIVE_VERDICTS and effective == "firewall":
        days = rules.get_rule(EXPIRY_RULE_ID).params["max_days"]  # type: ignore[union-attr]
        partners = ", ".join(f"{p.partner} {p.ip} ({p.purpose})" for p in load_cmdb().partners)
        parts.append(f"- 등록된 파트너 출발지: {partners}")
        parts.append(f"- 외부 출발지의 허용 기한은 최대 {days}일이다. 대안 문장의 만료 날짜는 코드가 작업일에 이 일수를 더해 채운 값이다")
    return "\n".join(parts)


def render_validation_input(rec: Recommendation, check: PolicyCheckResult, facts: str) -> str:
    severity = {"forbidden": "금지", "fixable": "보완"}
    violations = "\n".join(f"- {v.rule_id} ({severity.get(v.severity, v.severity)}): {v.evidence}" for v in rec.violations) or "- 없음"
    exempted = "\n".join(f"- {v.rule_id}: {v.evidence}" for v in check.exempted) or "- 없음"
    return (
        "## 코드가 계산한 결과 (바꾸지 않는다)\n"
        f"- 판정: {rec.verdict}\n- 위험도: {rec.risk or '없음'}\n\n"
        f"## 위반 조항 (규칙 엔진)\n{violations}\n\n"
        f"## 유효한 긴급 변경으로 면제된 조항\n{exempted}\n\n"
        f"## 근거 자료 (설명이 근거로 삼아도 되는 사실)\n{facts}\n\n"
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
        facts = render_facts(state, rec, load_rules())
        messages = [
            SystemMessage(content=load_prompt("validate.md")),
            HumanMessage(content=render_validation_input(rec, check, facts)),
        ]
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
