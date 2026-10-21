"""④ decide: 판정, 위험도, 설명, 조건부 대안 (docs/SPEC.md 3장).

판정은 코드가 정한다 (CLAUDE.md 절대 규칙 2). 위반 목록은 policy_agent가 가져온 엔진 결과 그대로이고, 판정은
verdict_from, 위험도는 risk_from(이력 상향은 v2), 인용 조항은 위반 조항에서 코드가 만든다. LLM은 설명(explanation)과
조건(conditions), 대안 일정 제안만 쓴다. 그래서 판정과 위반 목록은 구조적으로 어긋나지 않는다.

citations는 위반 조항, 유효한 긴급 변경이면 CHG-07, 표준 변경이면 STD id다. 면제된 조항은 넣지 않고 explanation과
warnings에 쓴다 (리뷰 I2, 평가의 오인용 정의와 같다).

대안 일정 (SPEC ④): 시간 조항(CHG-01, 05, 06, 10)을 위반했으면 일정을 옮겨야 한다. LLM이 제안한 일정을 check_window로
다시 검사하고, 위반이거나 일정이 없으면 위반 근거를 알려 1회 다시 쓰게 한다. 그래도 위반이면 next_valid_window가 찾은
가장 빠른 통과 시간대로 바꾼다. 대안 문장은 검증을 통과한 시각으로 코드가 조립하므로 설명에 틀린 시각이 들어가지 않고
(프롬프트가 explanation에 시각을 쓰지 못하게 한다), 문장으로 만들기 전의 구성 요소는 state.alternative에 남긴다.

조건 문장 (conditions): 요청자가 그대로 따라 쓸 수 있게 구체 값과 예시를 쓰게 한다. 값은 코드가 데이터에서 읽어 입력에
준다(등록 파트너 출발지, 허용 기한 최대 일수, 변경 사유). 날짜 덧셈은 모델이 틀리고 대안 일정은 검증이나 대체로 바뀔
수 있으므로, 만료 날짜는 모델이 EXPIRY_TOKEN만 쓰고 검증을 통과한 최종 작업일에서 코드가 채운다.

이력 (v2): history_agent가 돌았으면 위험도를 한 단계 올릴지(has_similar_incident)는 이미 코드가 이력 전체에서 정했고,
decide는 그 값을 risk_from에 넘길 뿐이다(상향은 결정론, 판정은 바뀌지 않는다). similar_history는 찾은 사례 id다.
이력을 설명에 쓰게 하는 지시는 decide.md가 아니라 decide_history.md에 두고 history_agent가 돈 경로에서만 시스템
프롬프트 뒤에 붙인다. v1의 프롬프트와 입력이 한 글자도 바뀌지 않아 기존 v1 측정이 그대로 유효하다.

시각 누락 (리뷰 H1): 시작 시각이 없으면 엔진이 시간 조항을 검사하지 못한다. 판정은 엔진 값 그대로 두고, 확인하지 못한
조항을 코드가 계산해 warnings와 explanation에 붙인다. 시각이 없으면 엔진이 표준 변경으로 확정하지 않으므로 자동 승인은
나오지 않는다.

실패: 규정 검사를 얻지 못했으면 판정을 만들지 않는다(ReviewIncomplete). 설명을 쓰는 LLM이 실패하면 코드가 만든 설명으로
대신하고 경고를 붙인다. 판정이 LLM에 기대지 않으므로 이렇게 물러설 수 있다.
"""

from datetime import datetime, timedelta

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app import rules_engine
from app.agent.errors import ReviewIncomplete
from app.agent.nodes.impact_agent import work_window
from app.agent.prompting import format_submitted_at, load_prompt, render_static_context
from app.agent.results import AlternativePlan, SubagentResult
from app.agent.state import GKState
from app.config import get_settings
from app.data_loader import load_cmdb, load_rules
from app.llm.factory import get_llm, structured_llm
from app.rules_engine._format import WEEKDAY_KO, fmt_range  # 근거 문장(evidence)과 같은 시각 표기를 쓰려는 것
from app.rules_engine.chg09 import RULE_ID as EXPIRY_RULE_ID
from app.rules_engine.context import effective_change_type
from app.rules_engine.emergency import RULE_ID as EMERGENCY_RULE_ID
from app.rules_engine.registry import WINDOW_RULES
from app.schemas import ChangeRequest, ChangeType, KstDatetime, PolicyCheckResult, Recommendation, RulesConfig, Verdict, Violation

ALTERNATIVE_VERDICTS: tuple[Verdict, ...] = ("반려", "조건부 승인")

# 조건 문장 안에서 허용 기한의 만료 날짜 자리를 나타낸다. 프롬프트(decide.md)의 지시와 같은 문자열이다.
EXPIRY_TOKEN = "<만료일>"

_INVALID = (OutputParserException, ValueError)


class DecideDraft(BaseModel):
    """decide가 LLM에게 받는 출력. 선택 필드가 없어 anthropic에도 그대로 보낸다."""

    explanation: str = Field(description="판정과 이유를 한국어 세 문장 안팎으로. 대안 일정의 시각은 쓰지 않는다")
    conditions: list[str] = Field(
        description="반려나 조건부 승인일 때 요청자가 해야 할 일을 구체적인 값과 예시를 담은 한 문장씩. 아니면 빈 목록"
    )
    alternative_start: KstDatetime | None = Field(description="일정 대안이 필요할 때만 새 작업 시작 시각(+09:00), 아니면 null")
    alternative_end: KstDatetime | None = Field(description="일정 대안이 필요할 때만 새 작업 종료 시각(+09:00), 아니면 null")


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


def time_violations(check: PolicyCheckResult) -> list[Violation]:
    """작업 시각을 옮겨야 풀리는 위반. 시간 조항(WINDOW_RULES)의 위반이다."""
    return [v for v in check.violations if v.rule_id in WINDOW_RULES]


def unchecked_time_rules(request: ChangeRequest, effective_type: ChangeType, exempt: bool, rules: RulesConfig) -> list[str]:
    """시작 시각이 없어 엔진이 검사하지 못한 시간 조항 (리뷰 H1). 적용 대상과 면제는 rules.json이 정한다."""
    if request.start is not None:
        return []
    return [
        spec.rule_id
        for spec in rules.rules
        if spec.rule_id in WINDOW_RULES and effective_type in spec.applies_to and not (exempt and spec.emergency_exempt)
    ]


def missing_start_sentence(rule_ids: list[str]) -> str:
    return f"작업 시각이 없어 시간 조항({', '.join(rule_ids)})을 확인하지 못했다. 승인 전에 작업 시각을 확인해야 한다."


def expiry_text(base: datetime | None, rules: RulesConfig) -> str:
    """허용 기한(rules.json CHG-09 max_days)의 만료 날짜 표기. base는 최종 작업 시작 시각이다.

    날짜 덧셈은 모델이 틀리므로 코드가 한다 (parse_request가 종료 시각 기본값을 코드로 계산하는 것과 같은 이유).
    작업일을 모르면 날짜 대신 말로 쓴다.
    """
    days = rules.get_rule(EXPIRY_RULE_ID).params["max_days"]  # type: ignore[union-attr]
    if base is None:
        return f"작업일로부터 {days}일째 되는 날"
    day = (base + timedelta(days=days)).date()
    return f"{day.month}/{day.day}({WEEKDAY_KO[day.weekday()]})"


def fill_expiry(texts: list[str], base: datetime | None, rules: RulesConfig) -> list[str]:
    """texts의 EXPIRY_TOKEN을 만료 날짜로 바꾼다. 토큰이 없으면 규정을 읽지 않고 그대로 돌려준다."""
    if not any(EXPIRY_TOKEN in text for text in texts):
        return texts
    shown = expiry_text(base, rules)
    return [text.replace(EXPIRY_TOKEN, shown) for text in texts]


def render_condition_values(request: ChangeRequest, effective_type: ChangeType, rules: RulesConfig) -> str:
    """조건 문장에 쓸 구체 값. 데이터에서 읽은 값만 주고 모델이 값을 지어내지 못하게 한다."""
    lines = [f"- 변경 사유: {request.reason or '적혀 있지 않음'}"]
    if effective_type == "firewall":
        days = rules.get_rule(EXPIRY_RULE_ID).params["max_days"]  # type: ignore[union-attr]
        partners = ", ".join(f"{p.partner} {p.ip} ({p.purpose})" for p in load_cmdb().partners)
        lines += [
            f"- 등록된 파트너 출발지: {partners}",
            f'- 외부 출발지의 허용 기한은 최대 {days}일이다. 만료 날짜 자리에는 "{EXPIRY_TOKEN}"이라고 그대로 쓴다 '
            "(코드가 최종 작업일 기준으로 채운다)",
        ]
    return "\n".join(lines)


def incomplete_detail(state: GKState) -> str:
    """규정 검사를 얻지 못한 이유를 찾으려는 진단 문장 (ReviewIncomplete.detail, dev 기록 전용).

    하위 오류 종류(errors)와 구조화된 작업 구간을 함께 적는다. 작업 구간이 틀리면 check_policy와 get_calendar가
    같이 실패하므로 둘을 같이 봐야 원인이 보인다.
    """
    errors = "; ".join(state.get("errors", [])) or "오류 기록 없음"
    request = state.get("structured")
    if request is None:
        return f"{errors} / 구조화 결과 없음"
    start = request.start.isoformat() if request.start else None
    end = request.end.isoformat() if request.end else None
    return f"{errors} / 구조화 작업 구간 start={start}, end={end}"


def build_citations(check: PolicyCheckResult, emergency_is_valid: bool, standard_id: str | None) -> list[str]:
    ids = [v.rule_id for v in check.violations]
    if emergency_is_valid:
        ids.append(EMERGENCY_RULE_ID)
    if standard_id:
        ids.append(standard_id)
    return list(dict.fromkeys(ids))


def fallback_explanation(verdict: Verdict, check: PolicyCheckResult) -> str:
    """LLM 없이 코드가 만드는 설명. 엔진 근거 문장을 그대로 잇는다."""
    parts = [f"규칙 엔진 판정은 {verdict}이다."]
    parts += [v.evidence for v in check.violations] or ["규정 위반이 없다."]
    if check.exempted:
        parts.append(f"유효한 긴급 변경으로 면제된 조항: {', '.join(v.rule_id for v in check.exempted)}.")
    return " ".join(parts)


def assemble_alternative(plan: AlternativePlan, verdict: Verdict) -> str | None:
    """검증을 통과한 구성 요소로 대안 문장을 만든다. 반려와 조건부 승인이 아니거나 낼 내용이 없으면 None."""
    if verdict not in ALTERNATIVE_VERDICTS:
        return None
    sentences = []
    if plan.start is not None and plan.end is not None:
        note = " (규정을 통과하는 가장 이른 시간대)" if plan.fallback else ""
        sentences.append(f"작업 시각을 다음으로 옮긴다{note}: {fmt_range(plan.start, plan.end)}.")
    sentences += [c if c.endswith(".") else f"{c}." for c in plan.conditions]
    if not sentences:
        return None
    sentences.append("반영해 다시 제출한다." if verdict == "반려" else "이 조건을 충족하면 조건부로 승인할 수 있다.")
    return " ".join(sentences)


def schedule_problems(
    draft: DecideDraft, request: ChangeRequest, effective_type: ChangeType, submitted_at: datetime
) -> list[str]:
    """LLM이 제안한 대안 일정이 쓸 수 없는 이유. 비어 있으면 통과다."""
    start, end = draft.alternative_start, draft.alternative_end
    if start is None or end is None:
        return ["대안 일정(alternative_start, alternative_end)이 없다. 시간 조항 위반이 있으므로 일정을 옮겨야 한다."]
    if end <= start:
        return ["대안 종료 시각은 시작 시각보다 늦어야 한다."]
    problems = ["대안 일정은 요청 시각 이후여야 한다."] if start <= submitted_at else []
    problems += [f"{v.rule_id}: {v.evidence}" for v in rules_engine.check_window(request.target, effective_type, start, end, submitted_at)]
    return problems


def retry_message(draft: DecideDraft, problems: list[str]) -> HumanMessage:
    listed = "\n".join(f"- {problem}" for problem in problems)
    return HumanMessage(
        content=(
            "앞서 낸 대안 일정으로는 규정을 통과하지 못한다. 규칙 엔진의 검사 결과:\n"
            f"{listed}\n\n앞선 출력:\n{draft.model_dump_json()}\n\n"
            "규정과 변경 캘린더를 다시 확인해 통과하는 다른 시각으로 전체 필드를 다시 출력한다."
        )
    )


def _lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- 없음"


def render_history_section(history: SubagentResult | None, incidents: list[dict], has_similar: bool) -> str | None:
    """입력의 '유사 장애 이력' 절. history_agent가 돌지 않은 경로(v1, 표준 변경)는 None이라 입력에 절이 없다."""
    if history is None:
        return None
    if history.status != "ok":
        return "확인 불가 (조회에 실패했다). 위험도는 이력 상향 없이 계산했다."
    head = (
        "같은 서비스와 유형의 장애 이력이 있어 코드가 위험도를 한 단계 올렸다."
        if has_similar
        else "같은 서비스와 유형의 장애 이력은 없다 (위험도 상향 없음). 아래는 참고용 비슷한 사례다."
    )
    cases = []
    for item in incidents:
        line = f"- {item['history_id']} ({item['service']}, {item['change_type']}, 결과 {item['outcome']}) {item['title']}"
        if item.get("incident"):
            line += f" / 교훈: {item['incident']['lesson']}"
        cases.append(line)
    return f"{head}\n요약: {history.summary or '-'}\n" + "\n".join(cases)


def render_decide_input(
    *,
    request: ChangeRequest,
    submitted_at: str,
    effective_type: ChangeType,
    verdict: Verdict,
    risk: str | None,
    check: PolicyCheckResult,
    standard_id: str | None,
    emergency_is_valid: bool | None,
    policy: SubagentResult,
    impact: SubagentResult | None,
    time_violation_ids: list[str],
    needs_schedule: bool,
    history_section: str | None = None,
) -> str:
    service = load_cmdb().get_service(request.target)
    window = work_window(request)
    severity = {"forbidden": "금지", "fixable": "보완"}
    emergency_text = {True: "유효 (장애 티켓 확인)", False: "유효하지 않음 (일반 변경으로 처리)", None: "해당 없음"}[emergency_is_valid]
    if impact is None:
        impact_text = "해당 없음 (표준 변경은 영향도 검사를 생략한다)"
    elif impact.status == "unavailable":
        impact_text = "확인 불가"
    else:
        impact_text = impact.summary
    schedule_text = (
        f"예 (시간 조항 위반: {', '.join(time_violation_ids)}). 원래 작업 구간은 {fmt_range(*window)}이고 같은 길이를 유지한다."
        if needs_schedule and window
        else "아니오"
    )
    history_block = f"## 유사 장애 이력\n{history_section}\n\n" if history_section is not None else ""
    values = (
        "## 조건에 쓸 값 (코드가 데이터에서 읽은 값. 이 밖의 값은 지어내지 않는다)\n"
        f"{render_condition_values(request, effective_type, load_rules())}\n\n"
        if verdict in ALTERNATIVE_VERDICTS
        else ""
    )
    return (
        f"요청 시각: {format_submitted_at(submitted_at)}\n"
        f"요청 대상: {request.target} ({service.tier if service else '등급 불명'}), 변경 유형: {effective_type}\n"
        f"원래 작업 구간: {fmt_range(*window) if window else '알 수 없음'}\n\n"
        "## 코드가 계산한 결과 (바꾸지 않는다)\n"
        f"- 판정: {verdict}\n- 위험도: {risk or '없음 (표준 변경은 매기지 않는다)'}\n"
        f"- 표준 변경: {standard_id or '아님'}\n- 긴급 변경: {emergency_text}\n\n"
        "## 위반 조항 (규칙 엔진, 근거 문장)\n"
        f"{_lines([f'{v.rule_id} ({severity.get(v.severity, v.severity)}): {v.evidence}' for v in check.violations])}\n\n"
        "## 유효한 긴급 변경으로 면제된 조항\n"
        f"{_lines([f'{v.rule_id}: {v.evidence}' for v in check.exempted])}\n\n"
        "## 조항별 설명 (규정 원문을 읽은 보조 에이전트)\n"
        f"{_lines([f'{n.rule_id}: {n.note}' for n in policy.notes])}\n\n"
        f"## 영향도 요약\n{impact_text}\n\n"
        f"{history_block}"
        f"{values}"
        f"## 일정 대안 필요: {schedule_text}"
    )


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------


async def decide(state: GKState, config: RunnableConfig) -> dict:
    policy: SubagentResult = state["policy"]
    if policy.status != "ok" or policy.tool_output is None:
        raise ReviewIncomplete(
            "규정 검사 결과를 얻지 못해 판정을 만들지 않는다.", stage="decide", detail=incomplete_detail(state)
        )
    request: ChangeRequest = state["structured"]
    submitted_at = datetime.fromisoformat(state["submitted_at"])
    rules = load_rules()
    check = PolicyCheckResult.model_validate(policy.tool_output)

    # 코드가 정하는 값. 표준 변경과 긴급 유효성은 classify가 엔진으로 확정한 값 그대로다 (리뷰 K3).
    standard_id = state.get("standard_change_id")
    emergency_is_valid = state.get("emergency_valid")
    effective_type = effective_change_type(request.change_type, standard_id is not None)
    verdict = rules_engine.verdict_from(check.violations, standard_id is not None)
    # 이력 상향: v2에서만 true일 수 있다. history_agent가 이력 전체에서 코드로 정한 값을 그대로 넘긴다.
    history: SubagentResult | None = state.get("history")
    has_similar = bool(state.get("has_similar_incident", False))
    incidents: list[dict] = list(state.get("similar_incidents", []))
    risk = rules_engine.risk_from(request.target, effective_type, has_similar)

    time_found = time_violations(check)
    window = work_window(request)
    needs_schedule = verdict in ALTERNATIVE_VERDICTS and window is not None and bool(time_found)

    settings = get_settings()
    chain = structured_llm(get_llm("main", settings), DecideDraft, settings)
    context = render_static_context()
    system = load_prompt("decide.md").format(policy=context["policy"], calendar=context["calendar"])
    if history is not None:  # history_agent가 돈 경로에서만 이력 지시를 붙인다 (v1의 프롬프트는 그대로)
        system = f"{system}\n{load_prompt('decide_history.md')}"
    messages: list[BaseMessage] = [
        SystemMessage(content=system),
        HumanMessage(
            content=render_decide_input(
                request=request,
                submitted_at=state["submitted_at"],
                effective_type=effective_type,
                verdict=verdict,
                risk=risk,
                check=check,
                standard_id=standard_id,
                emergency_is_valid=emergency_is_valid,
                policy=policy,
                impact=state.get("impact"),
                time_violation_ids=[v.rule_id for v in time_found],
                needs_schedule=needs_schedule,
                history_section=render_history_section(history, incidents, has_similar),
            )
        ),
    ]

    metrics = {"decide_retries": 0, "alternative_regenerations": 0, "alternative_fallbacks": 0}
    errors: list[str] = []
    warnings: list[str] = list(state.get("warnings", []))

    async def ask(prompt: list[BaseMessage]) -> DecideDraft | None:
        """구조화 출력 검증 실패는 1회 재시도한다. 끝내 실패하면 None (설명은 코드가 대신한다)."""
        for attempt in range(2):
            try:
                return await chain.ainvoke(prompt, config)
            except _INVALID:
                if attempt == 0:
                    metrics["decide_retries"] += 1
            except Exception as exc:  # 설명과 대안 문장은 부가 정보다. 판정은 이미 코드가 정했다
                errors.append(f"decide: 설명 작성 실패 ({type(exc).__name__})")
                return None
        errors.append("decide: 설명 작성 실패 (검증 오류)")
        return None

    draft = await ask(messages)

    # 대안 일정: 검증을 통과한 것만 쓴다
    schedule: tuple[datetime, datetime] | None = None
    fallback = False
    if needs_schedule:
        problems = schedule_problems(draft, request, effective_type, submitted_at) if draft else ["대안 일정이 없다."]
        if draft is not None and problems:
            metrics["alternative_regenerations"] = 1
            retried = await ask([*messages, retry_message(draft, problems)])
            if retried is not None:
                draft = retried
                problems = schedule_problems(draft, request, effective_type, submitted_at)
        if draft is not None and not problems:
            schedule = (draft.alternative_start, draft.alternative_end)  # type: ignore[assignment]
        else:
            fallback = True
            metrics["alternative_fallbacks"] = 1
            duration = window[1] - window[0]  # type: ignore[index]
            schedule = rules_engine.next_valid_window(
                request.target, effective_type, duration, max(window[0], submitted_at), submitted_at  # type: ignore[index]
            )
            warnings.append(
                "대안 일정: LLM이 제안한 일정이 규정을 통과하지 못해 규칙 엔진이 찾은 가장 이른 통과 시간대로 대체했다."
                if schedule
                else "대안 일정: 탐색 범위 안에서 규정을 통과하는 시간대를 찾지 못했다. 승인자가 일정을 정해야 한다."
            )

    conditions = [c.strip() for c in (draft.conditions if draft else []) if c.strip()] if verdict in ALTERNATIVE_VERDICTS else []
    work_start = schedule[0] if schedule else request.start  # 만료 날짜는 검증을 통과한 최종 작업일에서 센다
    conditions = fill_expiry(conditions, work_start, rules)
    plan = AlternativePlan(
        start=schedule[0] if schedule else None, end=schedule[1] if schedule else None, conditions=conditions, fallback=fallback
    )
    alternative = assemble_alternative(plan, verdict)
    if verdict in ALTERNATIVE_VERDICTS and alternative is None:
        warnings.append("조건부 대안을 만들지 못했다. 승인자가 조건을 정해야 한다.")

    explanation = draft.explanation.strip() if draft else ""
    if not explanation:
        explanation = fallback_explanation(verdict, check)
        warnings.append("설명 작성 실패: 규칙 엔진 결과로 코드가 만든 설명으로 대신했다.")
        metrics["partial_failures"] = 1
    explanation = fill_expiry([explanation], work_start, rules)[0]  # 토큰이 설명에 새도 화면에 날것으로 나가지 않게 한다

    # 리뷰 H1: 확인하지 못한 시간 조항
    unchecked = unchecked_time_rules(request, effective_type, bool(emergency_is_valid), rules)
    if unchecked:
        sentence = missing_start_sentence(unchecked)
        explanation = f"{explanation} {sentence}"
        warnings.append(sentence)
    if emergency_is_valid:
        hours = rules.get_rule(EMERGENCY_RULE_ID).params["post_report_hours"]  # type: ignore[union-attr]
        warnings.append(f"유효한 긴급 변경이라 실행 후 {hours}시간 안에 사후 보고가 필요하다.")
        if check.exempted:
            warnings.append(f"면제 적용: 유효한 긴급 변경이라 {', '.join(v.rule_id for v in check.exempted)}을 면제했다.")
    impact = state.get("impact")
    if impact is not None and impact.status == "unavailable":
        warnings.append("영향도: 확인 불가 (조회에 실패했다).")
    if history is not None and history.status == "unavailable":
        warnings.append("이력: 확인 불가 (조회에 실패했다). 위험도는 이력 상향 없이 계산했다.")

    recommendation = Recommendation(
        violations=check.violations,
        verdict=verdict,
        risk=risk,
        # 찾은 사례 id(정확 일치 먼저). v0, v1과 이력 검색이 실패한 경우는 None이다.
        similar_history=[item["history_id"] for item in incidents] if history is not None and history.status == "ok" else None,
        emergency_valid=emergency_is_valid,
        post_report_required=bool(emergency_is_valid),
        explanation=explanation,
        conditional_alternative=alternative,
        citations=build_citations(check, bool(emergency_is_valid), standard_id),
        warnings=list(dict.fromkeys(warnings)),
    )
    # warnings는 이미 recommendation에 합쳤다. 상태 키(reducer가 더한다)로 다시 돌려주면 중복된다.
    return {"recommendation": recommendation, "alternative": plan, "errors": errors, "metrics": metrics}
