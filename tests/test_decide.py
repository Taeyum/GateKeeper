"""④ decide 노드 테스트 (fake LLM, 실제 규칙 엔진). 판정은 코드가 정하고 LLM은 설명만 쓴다."""

from datetime import datetime, timedelta

import pytest

from app import data_loader, rules_engine
from app.agent.errors import ReviewIncomplete
from app.agent.nodes import decide as node
from app.agent.nodes.decide import (
    EXPIRY_TOKEN,
    DecideDraft,
    assemble_alternative,
    build_citations,
    expiry_text,
    fallback_explanation,
    fill_expiry,
    incomplete_detail,
    missing_start_sentence,
    render_decide_input,
    retry_message,
    schedule_problems,
    time_violations,
    unchecked_time_rules,
)
from app.agent.prompting import load_prompt
from app.agent.results import AlternativePlan, RuleNote, SubagentResult
from app.data_loader import load_cmdb, load_requests, load_rules
from app.rules_engine.context import effective_change_type
from app.llm.fake import set_fake_responses
from app.llm.usage import UsageCollector
from app.schemas import KST, ChangeRequest, PolicyCheckResult, Violation
from eval.evaluators import self_contradiction


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


def gold(request_id: str):
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def request_of(request_id: str, **overrides) -> ChangeRequest:
    data = gold(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


def state_for(request_id: str, request: ChangeRequest | None = None, **extra) -> dict:
    """classify, policy_agent, impact_agent가 하는 일을 실제 엔진으로 채운 부모 상태."""
    item = gold(request_id)
    request = request or request_of(request_id)
    check = rules_engine.check_all(request, item.submitted_at)
    standard = rules_engine.is_standard(request)
    emergency = rules_engine.emergency_valid(request) if request.is_emergency else None
    state = {
        "request_id": request_id,
        "submitted_at": item.submitted_at.isoformat(),
        "requester": item.requester,
        "masked_text": item.text,
        "structured": request,
        "route": "standard" if standard.is_standard else "general",
        "standard_change_id": standard.standard_change_id,
        "emergency_valid": emergency.valid if emergency else None,
        "policy": SubagentResult(tool_output=check.model_dump(mode="json"), summary="정책 요약"),
        "impact": None if standard.is_standard else SubagentResult(summary="영향도 요약"),
        "warnings": [],
    }
    state.update(extra)
    return state


def draft(*, start=None, end=None, conditions=(), explanation="설명") -> DecideDraft:
    return DecideDraft(explanation=explanation, conditions=list(conditions), alternative_start=start, alternative_end=end)


class Recorder:
    def __init__(self, chain):
        self.chain, self.calls = chain, []

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return await self.chain.ainvoke(messages, config)


@pytest.fixture
def recorder(monkeypatch):
    holder: list[Recorder] = []
    real = node.structured_llm

    def wrapped(llm, schema, settings=None):
        holder.append(Recorder(real(llm, schema, settings)))
        return holder[-1]

    monkeypatch.setattr(node, "structured_llm", wrapped)
    return holder


async def run(state: dict, responses: list):
    set_fake_responses("main", responses)
    collector = UsageCollector()
    return await node.decide(state, {"callbacks": [collector]}), collector


def valid_window(request_id: str = "REQ-D01", request: ChangeRequest | None = None):
    """엔진이 찾은 가장 빠른 통과 시간대. LLM이 이것을 제안하면 검증을 통과한다."""
    request = request or request_of(request_id)
    submitted = gold(request_id).submitted_at
    found = rules_engine.next_valid_window(
        request.target, "firewall" if request.change_type == "firewall" else request.change_type,
        request.end - request.start, max(request.start, submitted), submitted,
    )  # fmt: skip
    assert found is not None
    return found


# ---------------------------------------------------------------------------
# 판정, 위험도, 인용은 코드가 정한다
# ---------------------------------------------------------------------------


async def test_dev_10건의_판정과_위반_긴급_여부가_라벨과_모두_같다_LLM이_못_써도():
    for item in load_requests("dev"):
        out, _ = await run(state_for(item.request_id), [])  # main 응답이 없어 설명 LLM이 실패해도 판정은 같다
        rec = out["recommendation"]
        assert rec.verdict == item.expected.verdict, item.request_id
        assert sorted(v.rule_id for v in rec.violations) == sorted(item.expected.violations), item.request_id
        assert rec.emergency_valid == item.expected.emergency_valid
        assert rec.post_report_required == item.expected.post_report_required


async def test_dev_10건의_판정과_자기_위반_목록은_모순되지_않는다():
    rules = load_rules()
    for item in load_requests("dev"):
        out, _ = await run(state_for(item.request_id), [])
        rec = out["recommendation"]
        assert self_contradiction(rec.verdict, sorted(v.rule_id for v in rec.violations), rules) is False, item.request_id


async def test_위험도는_등급_기준이고_이력_상향은_하지_않는다_표준_변경은_매기지_않는다():
    expected = {"REQ-D01": "높음", "REQ-D05": "낮음", "REQ-D06": "중간", "REQ-D04": None, "REQ-D10": None}
    for request_id, risk in expected.items():
        out, _ = await run(state_for(request_id), [])
        rec = out["recommendation"]
        assert rec.risk == risk, request_id
        assert rec.similar_history is None  # 이력은 v2의 RAG가 더하는 정보다


async def test_표준_후보인데_확정_못_하면_배포로_보고_위험도를_매긴다():
    request = request_of("REQ-D04", restart_required=True)  # STD-01 조건 위반: 재시작 필요
    out, _ = await run(state_for("REQ-D04", request), [])
    rec = out["recommendation"]
    assert rec.verdict != "자동 승인" and rec.risk is not None  # standard가 아니라 deploy 기준


async def test_인용은_위반_조항이고_긴급이면_CHG_07_표준이면_STD이며_면제_조항은_넣지_않는다():
    out, _ = await run(state_for("REQ-D01"), [draft(explanation="x")] * 3)
    assert out["recommendation"].citations == [v.rule_id for v in out["recommendation"].violations]

    out, _ = await run(state_for("REQ-D07"), [draft()])
    rec = out["recommendation"]
    assert rec.citations == ["CHG-07"] and rec.violations == [] and rec.emergency_valid is True
    assert rec.post_report_required is True

    out, _ = await run(state_for("REQ-D04"), [draft()])
    assert out["recommendation"].citations == ["STD-01"] and out["recommendation"].verdict == "자동 승인"

    check = PolicyCheckResult(
        violations=[Violation(rule_id="CHG-06", severity="forbidden", evidence="가")],
        exempted=[Violation(rule_id="CHG-01", severity="forbidden", evidence="나")],
    )
    assert build_citations(check, True, None) == ["CHG-06", "CHG-07"]  # 면제된 CHG-01은 없다


async def test_설명이_위반_조항과_다른_말을_해도_판정과_위반은_엔진_값이다():
    liar = draft(explanation="위반이 없어 승인한다. CHG-99도 걸렸다.")
    out, _ = await run(state_for("REQ-D10"), [liar])  # 표준 변경이지만 동결(CHG-10)에 걸려 반려
    rec = out["recommendation"]
    assert rec.verdict == "반려" and [v.rule_id for v in rec.violations] == ["CHG-10"]
    assert "CHG-99" not in rec.citations


async def test_상태에_warnings를_다시_돌려주지_않는다_reducer가_더해_중복되기_때문이다():
    out, _ = await run(state_for("REQ-D06", warnings=["분류 불일치"]), [draft()])
    assert "warnings" not in out and out["recommendation"].warnings == ["분류 불일치"]


# ---------------------------------------------------------------------------
# 시작 시각 누락 (리뷰 H1)
# ---------------------------------------------------------------------------


async def test_배포에_작업_시각이_없으면_확인하지_못한_시간_조항을_경고하고_판정은_엔진_값이다():
    request = request_of("REQ-D06", start=None, end=None)
    out, _ = await run(state_for("REQ-D06", request), [draft(explanation="문제없다")])
    rec = out["recommendation"]
    sentence = missing_start_sentence(["CHG-01", "CHG-05", "CHG-06", "CHG-10"])
    assert rec.verdict == "승인" and sentence in rec.warnings
    assert rec.explanation == f"문제없다 {sentence}"
    assert "승인 전에 작업 시각을 확인해야 한다" in sentence


async def test_표준_후보인데_작업_시각이_없으면_자동_승인이_나오지_않는다():
    request = request_of("REQ-D04", start=None, end=None)
    state = state_for("REQ-D04", request)
    assert state["standard_change_id"] is None  # 엔진이 확정하지 않는다
    out, _ = await run(state, [draft()])
    rec = out["recommendation"]
    assert rec.verdict != "자동 승인" and rec.citations == [v.rule_id for v in rec.violations]
    assert any("시간 조항" in w for w in rec.warnings)


async def test_유효한_긴급_변경은_면제된_시간_조항을_빼고_경고한다():
    request = request_of("REQ-D07", start=None, end=None)
    out, _ = await run(state_for("REQ-D07", request), [draft()])
    warning = next(w for w in out["recommendation"].warnings if "시간 조항" in w)
    assert "CHG-06" in warning and "CHG-01" not in warning and "CHG-10" not in warning and "CHG-05" not in warning


async def test_방화벽도_시각이_없으면_경고한다():
    request = request_of("REQ-D01", start=None, end=None)
    out, _ = await run(state_for("REQ-D01", request), [draft()])
    assert any("작업 시각이 없어 시간 조항" in w for w in out["recommendation"].warnings)


async def test_작업_시각이_있으면_경고가_없다():
    out, _ = await run(state_for("REQ-D06"), [draft()])
    assert not any("시간 조항" in w for w in out["recommendation"].warnings)


def test_확인하지_못한_조항은_rules_json의_적용_대상과_면제에서_계산한다():
    rules = load_rules()
    request = request_of("REQ-D06", start=None, end=None)
    assert unchecked_time_rules(request, "deploy", False, rules) == ["CHG-01", "CHG-05", "CHG-06", "CHG-10"]
    assert unchecked_time_rules(request, "deploy", True, rules) == ["CHG-06"]
    assert unchecked_time_rules(request, "standard", False, rules) == ["CHG-06", "CHG-10"]  # 표준 변경은 이 둘만 적용
    assert unchecked_time_rules(request_of("REQ-D06"), "deploy", False, rules) == []  # 시각이 있으면 엔진이 검사했다
    # 규정 값을 바꾸면 따라간다 (코드에 조항 목록이 없다)
    changed = rules.model_copy(update={"rules": [r.model_copy(update={"applies_to": []}) if r.rule_id == "CHG-05" else r for r in rules.rules]})
    assert unchecked_time_rules(request, "deploy", False, changed) == ["CHG-01", "CHG-06", "CHG-10"]


# ---------------------------------------------------------------------------
# 대안 일정 (SPEC ④)
# ---------------------------------------------------------------------------


async def test_시간_조항_위반이_있으면_LLM_일정을_엔진으로_검증해_통과하면_그대로_쓴다(monkeypatch):
    start, end = valid_window()
    out, collector = await run(state_for("REQ-D01"), [draft(start=start, end=end, conditions=["출발지를 특정한다"])])
    rec, plan = out["recommendation"], out["alternative"]
    assert (plan.start, plan.end, plan.fallback) == (start, end, False)
    assert out["metrics"]["alternative_regenerations"] == 0 and out["metrics"]["alternative_fallbacks"] == 0
    assert collector.snapshot().llm_calls == 1
    assert rec.conditional_alternative.startswith("작업 시각을 다음으로 옮긴다: ") and "출발지를 특정한다." in rec.conditional_alternative
    assert rec.conditional_alternative.endswith("반영해 다시 제출한다.")  # 반려는 재제출


async def test_LLM_일정이_위반이면_위반_근거를_알려_1회_다시_쓰게_하고_통과하면_쓴다(recorder):
    request = request_of("REQ-D01")
    bad = draft(start=request.start, end=request.end)  # 원래 일정 그대로라 CHG-01, CHG-10 위반
    start, end = valid_window()
    out, collector = await run(state_for("REQ-D01"), [bad, draft(start=start, end=end)])
    plan = out["alternative"]
    assert (plan.start, plan.end, plan.fallback) == (start, end, False)
    assert out["metrics"]["alternative_regenerations"] == 1 and out["metrics"]["alternative_fallbacks"] == 0
    assert collector.snapshot().llm_calls == 2
    feedback = recorder[0].calls[1][2].content
    assert "CHG-01:" in feedback and "CHG-10:" in feedback and bad.model_dump_json() in feedback


async def test_일정이_없으면_다시_쓰게_하고_그래도_없으면_엔진이_찾은_시간대로_대체한다():
    expected = valid_window()
    out, collector = await run(state_for("REQ-D01"), [draft(), draft()])
    plan, rec = out["alternative"], out["recommendation"]
    assert (plan.start, plan.end, plan.fallback) == (*expected, True)
    assert out["metrics"]["alternative_regenerations"] == 1 and out["metrics"]["alternative_fallbacks"] == 1
    assert collector.snapshot().llm_calls == 2  # 세 번째 호출은 없다
    assert any("가장 이른 통과 시간대로 대체" in w for w in rec.warnings)
    assert "(규정을 통과하는 가장 이른 시간대)" in rec.conditional_alternative


async def test_LLM이_위반_일정을_두_번_내면_받아들이지_않고_엔진이_찾은_시간대로_대체한다():
    request = request_of("REQ-D01")
    bad = draft(start=request.start, end=request.end)  # 원래 일정 그대로: CHG-01, CHG-10 위반
    out, collector = await run(state_for("REQ-D01"), [bad, bad])
    plan = out["alternative"]
    assert plan.fallback is True and (plan.start, plan.end) == valid_window()
    assert (plan.start, plan.end) != (request.start, request.end)  # 위반 일정을 그대로 쓰지 않는다
    assert collector.snapshot().llm_calls == 2
    assert out["metrics"]["alternative_regenerations"] == 1 and out["metrics"]["alternative_fallbacks"] == 1


async def test_대체한_일정은_항상_엔진_검사를_통과한다():
    for item in load_requests("dev"):
        out, _ = await run(state_for(item.request_id), [draft(), draft()])
        plan, request = out["alternative"], request_of(item.request_id)
        if plan.start is None:
            continue
        # 엔진이 표준 변경으로 확정한 요청은 표준 기준(CHG-06, 10만)으로, 아니면 요청 유형 그대로 검사한다
        effective = effective_change_type(request.change_type, rules_engine.is_standard(request).is_standard)
        found = rules_engine.check_window(request.target, effective, plan.start, plan.end, item.submitted_at)
        assert found == [], item.request_id


async def test_요청_시각_이전의_일정은_엔진_위반이_없어도_쓰지_않는다():
    request = request_of("REQ-D01")
    submitted = gold("REQ-D01").submitted_at
    past = submitted - timedelta(days=3)
    problems = schedule_problems(draft(start=past, end=past + timedelta(hours=1)), request, "firewall", submitted)
    assert "대안 일정은 요청 시각 이후여야 한다." in problems


def test_일정_검증은_없음과_역전을_구분한다():
    request, submitted = request_of("REQ-D01"), gold("REQ-D01").submitted_at
    assert "대안 일정(alternative_start, alternative_end)이 없다" in schedule_problems(draft(), request, "firewall", submitted)[0]
    later = submitted + timedelta(days=9)
    assert schedule_problems(draft(start=later, end=later), request, "firewall", submitted) == ["대안 종료 시각은 시작 시각보다 늦어야 한다."]
    start, end = valid_window()
    assert schedule_problems(draft(start=start, end=end), request, "firewall", submitted) == []


async def test_엔진도_통과_시간대를_못_찾으면_일정_없이_경고만_남긴다(monkeypatch):
    monkeypatch.setattr(rules_engine, "next_valid_window", lambda *args, **kwargs: None)
    out, _ = await run(state_for("REQ-D01"), [draft(conditions=["출발지를 특정한다"]), draft(conditions=["출발지를 특정한다"])])
    plan, rec = out["alternative"], out["recommendation"]
    assert plan.start is None and plan.end is None and plan.fallback is True
    assert any("통과하는 시간대를 찾지 못했다" in w for w in rec.warnings)
    assert "작업 시각을 다음으로" not in rec.conditional_alternative and "출발지를 특정한다." in rec.conditional_alternative


async def test_시간_조항_위반이_없는_조건부_승인은_일정을_받지_않고_조건만_쓴다(monkeypatch):
    calls = []
    original = rules_engine.check_window
    monkeypatch.setattr(rules_engine, "check_window", lambda *a, **k: calls.append(a) or original(*a, **k))
    state = state_for("REQ-D03")  # CHG-04(롤백 없음)만 위반
    assert time_violations(PolicyCheckResult.model_validate(state["policy"].tool_output)) == []
    stray = datetime(2026, 11, 2, 10, 0, tzinfo=KST)
    out, collector = await run(state, [draft(start=stray, end=stray + timedelta(hours=1), conditions=["롤백 절차를 제출한다"])])
    rec, plan = out["recommendation"], out["alternative"]
    assert rec.verdict == "조건부 승인" and calls == [] and collector.snapshot().llm_calls == 1
    assert (plan.start, plan.end, plan.conditions) == (None, None, ["롤백 절차를 제출한다"])  # 낸 일정은 쓰지 않는다
    assert rec.conditional_alternative == "롤백 절차를 제출한다. 이 조건을 충족하면 조건부로 승인할 수 있다."


async def test_승인과_자동_승인은_대안이_없다():
    for request_id in ("REQ-D06", "REQ-D04"):
        out, _ = await run(state_for(request_id), [draft(conditions=["필요 없는 조건"], start=datetime(2026, 11, 2, 10, tzinfo=KST), end=datetime(2026, 11, 2, 11, tzinfo=KST))])
        assert out["recommendation"].conditional_alternative is None
        assert out["alternative"] == AlternativePlan()


async def test_대안_구성_요소가_상태에_남는다():
    start, end = valid_window()
    out, _ = await run(state_for("REQ-D01"), [draft(start=start, end=end, conditions=["  출발지를 특정한다  ", " "])])
    assert out["alternative"] == AlternativePlan(start=start, end=end, conditions=["출발지를 특정한다"], fallback=False)


# ---------------------------------------------------------------------------
# 조건 문장의 구체 값 (v1 대안 구체성)
# 1차 anthropic 측정에서 Judge가 "일반론에 그쳐 구체성이 부족"하다고 한 조건 문장을 구체 값으로 쓰게 한다.
# ---------------------------------------------------------------------------


def rules_with_max_days(days: int):
    rules = load_rules().model_copy(deep=True)
    rules.get_rule("CHG-09").params["max_days"] = days
    return rules


def render_for(request_id: str, verdict: str, effective_type: str) -> str:
    state = state_for(request_id)
    check = PolicyCheckResult.model_validate(state["policy"].tool_output)
    return render_decide_input(
        request=state["structured"], submitted_at=state["submitted_at"], effective_type=effective_type, verdict=verdict,
        risk="높음", check=check, standard_id=None, emergency_is_valid=None, policy=state["policy"], impact=state["impact"],
        time_violation_ids=[], needs_schedule=False,
    )  # fmt: skip


def test_조건은_구체적인_값과_예시로_쓰게_하고_일반론과_지어내기를_막는다():
    text = load_prompt("decide.md")
    line = next(row for row in text.splitlines() if row.startswith("- conditions:"))
    assert "구체적인 값과 예시를 함께 쓰고" in line and '"구체적으로 적어 달라" 같은 일반론으로 끝내지 않는다' in line
    assert "지어내지 않는다" in line and '간결한 평서문("~한다")' in line
    for hint in ("출발지가 문제면", "허용 기한이 문제면", "롤백 계획이 문제면", "테스트 증적이 문제면"):
        assert hint in text, hint
    assert f'"{EXPIRY_TOKEN}"이라고 그대로 쓴다' in text and "날짜를 직접 계산하지 않는다" in text
    # 이 파일은 .format(policy, calendar)을 거치므로 다른 중괄호가 있으면 프롬프트를 만들 때 깨진다
    assert "{" not in text.replace("{policy}", "").replace("{calendar}", "")


def test_근거가_없으면_예시를_지어내지_말고_형식만_쓰게_한다():
    """Haiku v1이 변경 사유가 비었는데 인덱스 추가 예시를 만든 사례(REQ-D08, 3점)를 막는 지시다."""
    line = next(row for row in load_prompt("decide.md").splitlines() if row.startswith("- conditions:"))
    assert "변경 사유나 데이터에 근거가 없으면 구체 예시를 지어내지 말고 무엇을 적어야 하는지 형식만 쓴다" in line
    assert '변경 사유가 적혀 있지 않으면 롤백 예시를 만들지 않고 "되돌리는 절차를 한 줄 이상 적는다"처럼 쓴다' in line


def test_조건에_쓸_값은_코드가_데이터에서_읽어_입력에_넣는다():
    request = request_of("REQ-D01")
    text = render_for("REQ-D01", "반려", "firewall")
    assert "## 조건에 쓸 값" in text and f"- 변경 사유: {request.reason or '적혀 있지 않음'}" in text
    for partner in load_cmdb().partners:
        assert f"{partner.partner} {partner.ip} ({partner.purpose})" in text
    max_days = load_rules().get_rule("CHG-09").params["max_days"]
    assert f"허용 기한은 최대 {max_days}일이다" in text and f'"{EXPIRY_TOKEN}"이라고 그대로 쓴다' in text


def test_허용_기한의_최대_일수는_rules_json을_따르고_프롬프트에_데이터_값을_쓰지_않는다(monkeypatch):
    monkeypatch.setattr(node, "load_rules", lambda: rules_with_max_days(14))
    assert "허용 기한은 최대 14일이다" in render_for("REQ-D01", "반려", "firewall")
    prompt = load_prompt("decide.md").split("# 출력")[1]
    max_days = load_rules().get_rule("CHG-09").params["max_days"]
    assert f"{max_days}일" not in prompt  # 규정 값은 입력으로 받는다 (CLAUDE.md 절대 규칙 3)
    assert not any(partner.ip in prompt or partner.partner in prompt for partner in load_cmdb().partners)


def test_방화벽이_아니면_파트너와_허용_기한을_주지_않고_승인에는_섹션이_없다():
    db = render_for("REQ-D03", "조건부 승인", "db_change")
    assert "## 조건에 쓸 값" in db and "등록된 파트너" not in db and "허용 기한" not in db
    assert "## 조건에 쓸 값" not in render_for("REQ-D06", "승인", "deploy")


def test_만료_날짜는_작업일에_최대_일수를_더해_코드가_계산한다():
    base = datetime(2026, 10, 27, 14, tzinfo=KST)
    assert expiry_text(base, rules_with_max_days(30)) == "11/26(목)"
    assert expiry_text(base, rules_with_max_days(14)) == "11/10(화)"
    assert expiry_text(datetime(2026, 12, 20, 9, tzinfo=KST), rules_with_max_days(30)) == "1/19(화)"  # 해를 넘긴다
    assert expiry_text(None, rules_with_max_days(30)) == "작업일로부터 30일째 되는 날"  # 작업일을 모르면 날짜를 지어내지 않는다


def test_토큰이_없으면_규정을_읽지_않고_그대로_돌려준다():
    texts = ["롤백 절차를 제출한다"]
    assert fill_expiry(texts, None, None) is texts  # rules=None이라 읽으려 하면 실패한다
    assert fill_expiry([f"{EXPIRY_TOKEN}까지, {EXPIRY_TOKEN}"], None, rules_with_max_days(7)) == [
        "작업일로부터 7일째 되는 날까지, 작업일로부터 7일째 되는 날"
    ]


async def test_만료_날짜는_검증을_통과한_대안_작업일에서_센다():
    start, end = valid_window()
    condition = f"허용 기한을 최대 30일로 줄여 {EXPIRY_TOKEN}까지로 지정한다"
    out, _ = await run(state_for("REQ-D01"), [draft(start=start, end=end, conditions=[condition])])
    expected = expiry_text(start, load_rules())
    assert out["alternative"].conditions == [f"허용 기한을 최대 30일로 줄여 {expected}까지로 지정한다"]
    assert expected != expiry_text(request_of("REQ-D01").start, load_rules())  # 원래 작업일 기준이 아니다
    assert EXPIRY_TOKEN not in out["recommendation"].conditional_alternative


async def test_일정이_엔진_대체로_바뀌어도_만료_날짜는_대체된_작업일에서_센다():
    condition = f"허용 기한을 최대 30일로 줄여 {EXPIRY_TOKEN}까지로 지정한다"
    out, _ = await run(state_for("REQ-D01"), [draft(conditions=[condition]), draft(conditions=[condition])])
    plan = out["alternative"]
    assert plan.fallback is True
    assert plan.conditions == [f"허용 기한을 최대 30일로 줄여 {expiry_text(plan.start, load_rules())}까지로 지정한다"]


async def test_일정을_옮기지_않는_조건부_승인은_요청의_작업일에서_센다():
    request = request_of("REQ-D09")  # CHG-09만 위반: 작업 시각은 그대로
    condition = f"허용 기한을 최대 30일로 줄여 {EXPIRY_TOKEN}까지로 지정한다"
    out, _ = await run(state_for("REQ-D09"), [draft(conditions=[condition])])
    assert out["alternative"].start is None
    assert out["alternative"].conditions == [f"허용 기한을 최대 30일로 줄여 {expiry_text(request.start, load_rules())}까지로 지정한다"]
    assert out["alternative"].conditions[0].endswith("까지로 지정한다") and "<" not in out["alternative"].conditions[0]


async def test_작업일을_모르면_날짜를_지어내지_않고_말로_쓴다():
    request = request_of("REQ-D09", start=None, end=None)
    condition = f"허용 기한을 최대 30일로 줄여 {EXPIRY_TOKEN}까지로 지정한다"
    out, _ = await run(state_for("REQ-D09", request), [draft(conditions=[condition])])
    assert out["alternative"].conditions == ["허용 기한을 최대 30일로 줄여 작업일로부터 30일째 되는 날까지로 지정한다"]


async def test_설명에_토큰이_새도_화면에_날것으로_나가지_않는다():
    out, _ = await run(state_for("REQ-D09"), [draft(explanation=f"기한은 {EXPIRY_TOKEN}까지로 줄여야 해요.", conditions=["기한을 줄인다"])])
    assert EXPIRY_TOKEN not in out["recommendation"].explanation and "기한은 11/26(목)까지로 줄여야 해요." == out["recommendation"].explanation


def test_대안_문장_조립():
    start, end = datetime(2026, 11, 2, 9, tzinfo=KST), datetime(2026, 11, 2, 10, tzinfo=KST)
    plan = AlternativePlan(start=start, end=end, conditions=["롤백 절차를 제출한다", "테스트 결과를 첨부해요."])
    assert assemble_alternative(plan, "반려") == "작업 시각을 다음으로 옮긴다: 11/2(월) 09:00~10:00. 롤백 절차를 제출한다. 테스트 결과를 첨부해요. 반영해 다시 제출한다."
    assert assemble_alternative(plan.model_copy(update={"fallback": True}), "조건부 승인").startswith("작업 시각을 다음으로 옮긴다 (규정을 통과하는 가장 이른 시간대): ")
    assert assemble_alternative(plan, "승인") is None and assemble_alternative(AlternativePlan(), "반려") is None


# ---------------------------------------------------------------------------
# 실패
# ---------------------------------------------------------------------------


async def test_규정_검사를_얻지_못했으면_판정을_만들지_않는다():
    unavailable = SubagentResult(status="unavailable", summary="규정 검사: 확인 불가")
    with pytest.raises(ReviewIncomplete):
        await run(state_for("REQ-D01", policy=unavailable), [draft()])
    with pytest.raises(ReviewIncomplete):
        await run(state_for("REQ-D01", policy=SubagentResult(status="ok", tool_output=None)), [draft()])


async def test_판정을_못_만들면_원인을_찾을_진단_정보를_단다():  # N1
    unavailable = SubagentResult(status="unavailable", summary="규정 검사: 확인 불가")
    errors = ["policy_agent: 규정 검사 실패 (MCPToolError)", "impact_agent: 조회 실패 (MCPToolError)"]
    with pytest.raises(ReviewIncomplete) as error:
        await run(state_for("REQ-D01", policy=unavailable, errors=errors), [draft()])

    assert error.value.stage == "decide"
    assert "policy_agent: 규정 검사 실패 (MCPToolError); impact_agent: 조회 실패 (MCPToolError)" in error.value.detail
    assert "구조화 작업 구간 start=2026-10-30T20:00:00+09:00, end=2026-10-30T21:00:00+09:00" in error.value.detail
    assert "2026" not in str(error.value) and "payments" not in str(error.value)  # 메시지에는 요청 값이 없다
    assert error.value.diagnosis() == f"decide: {error.value.detail}"


def test_진단_정보는_오류_기록이나_구조화_결과가_없어도_만들어진다():
    assert incomplete_detail({}) == "오류 기록 없음 / 구조화 결과 없음"
    no_window = request_of("REQ-D06", start=None, end=None)
    assert incomplete_detail({"structured": no_window}) == "오류 기록 없음 / 구조화 작업 구간 start=None, end=None"


async def test_설명_LLM이_실패하면_코드가_만든_설명으로_대신하고_판정은_그대로다():
    out, collector = await run(state_for("REQ-D01"), [])  # main 응답 큐가 비어 있다
    rec = out["recommendation"]
    assert rec.verdict == "반려" and rec.explanation.startswith("규칙 엔진 판정은 반려이다.")
    assert all(v.evidence in rec.explanation for v in rec.violations)
    assert "설명 작성 실패: 규칙 엔진 결과로 코드가 만든 설명으로 대신했다." in rec.warnings
    assert out["errors"] == ["decide: 설명 작성 실패 (FakeResponsesExhausted)"] and out["metrics"]["partial_failures"] == 1
    assert out["alternative"].fallback is True  # LLM이 없으면 엔진이 찾은 시간대를 바로 쓴다
    # 실패한 호출 1번만 있고 재작성 호출은 없다
    assert out["metrics"]["alternative_regenerations"] == 0 and collector.snapshot().llm_calls == 1


async def test_구조화_출력이_검증에_실패하면_1회_재시도한다():
    out, collector = await run(state_for("REQ-D06"), [{"explanation": "항목 없음"}, draft(explanation="재시도 설명")])
    assert out["recommendation"].explanation == "재시도 설명" and out["metrics"]["decide_retries"] == 1
    assert collector.snapshot().llm_calls == 2 and out["errors"] == []


async def test_두_번_연속_검증에_실패하면_코드_설명으로_대신한다():
    out, _ = await run(state_for("REQ-D06"), [{"explanation": "x"}, {"explanation": "y"}])
    assert out["recommendation"].explanation.startswith("규칙 엔진 판정은 승인이다.")
    assert out["errors"] == ["decide: 설명 작성 실패 (검증 오류)"] and out["metrics"]["decide_retries"] == 1


# ---------------------------------------------------------------------------
# 경고
# ---------------------------------------------------------------------------


async def test_유효한_긴급_변경은_사후_보고와_면제_적용을_경고한다():
    out, _ = await run(state_for("REQ-D07"), [draft(explanation="면제 적용")])
    rec = out["recommendation"]
    hours = load_rules().get_rule("CHG-07").params["post_report_hours"]
    assert f"유효한 긴급 변경이라 실행 후 {hours}시간 안에 사후 보고가 필요하다." in rec.warnings
    assert any(w.startswith("면제 적용: 유효한 긴급 변경이라 CHG-") for w in rec.warnings)


async def test_영향도가_확인_불가면_경고하고_판정은_그대로다():
    impact = SubagentResult(status="unavailable", summary="영향도: 확인 불가")
    out, _ = await run(state_for("REQ-D06", impact=impact), [draft()])
    rec = out["recommendation"]
    assert "영향도: 확인 불가 (조회에 실패했다)." in rec.warnings and rec.verdict == "승인" and rec.risk == "중간"


async def test_표준_경로는_영향도가_없어도_경고하지_않는다():
    out, _ = await run(state_for("REQ-D04"), [draft()])
    assert out["recommendation"].warnings == []


async def test_분류_경고는_그대로_이어진다_중복은_한_번만():
    out, _ = await run(state_for("REQ-D06", warnings=["분류 불일치: 사람 확인 필요", "분류 불일치: 사람 확인 필요"]), [draft()])
    assert out["recommendation"].warnings == ["분류 불일치: 사람 확인 필요"]


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------


async def test_프롬프트에_엔진_결과와_조항별_설명_영향도가_들어가고_판정은_바꾸지_말라고_한다(recorder):
    notes = [RuleNote(rule_id="CHG-01", note="금요일 저녁은 금지 시간대다")]
    policy = SubagentResult(tool_output=state_for("REQ-D01")["policy"].tool_output, notes=notes)
    await run(state_for("REQ-D01", policy=policy), [draft(), draft()])
    system, user = recorder[0].calls[0]
    assert "당신은 그것을 바꾸지 않고" in system.content and "CHG-01 배포 금지 시간대" in system.content  # 규정 원문
    assert "FRZ-01" in system.content  # 변경 캘린더 (대안 일정이 피해야 할 동결 기간)
    assert "{policy}" not in system.content and "{calendar}" not in system.content
    assert "- 판정: 반려" in user.content and "- 위험도: 높음" in user.content
    assert "- CHG-01 (금지): " in user.content and "- CHG-01: 금요일 저녁은 금지 시간대다" in user.content
    assert "영향도 요약\n영향도 요약" in user.content and "일정 대안 필요: 예 (시간 조항 위반: CHG-01, CHG-10)" in user.content
    assert "2026-10-29 16:00 (목) Asia/Seoul" in user.content


def test_설명은_규정_번호와_영어_약어_없는_해요체_두세_문장으로_쓰게_한다():  # K5
    """화면이 explanation을 승인자에게 그대로 보여 주고, 규정 번호는 citations로 따로 보여 준다."""
    line = next(row for row in load_prompt("decide.md").splitlines() if row.startswith("- explanation:"))
    assert '"~해요"체로 두세 문장' in line
    assert "규정 번호(CHG-01, STD-01 같은 것)와 영어 약어" in line and "쉬운 우리말로 풀어 쓴다" in line
    assert "세 문장 안팎" not in line and '"면제 적용"' not in line  # 이전 지시가 남아 충돌하지 않는다
    assert "대안 일정의 시각은 쓰지 않는다" in line  # 대안 시각은 코드가 조립한다 (기존 규칙 유지)


def test_입력_구성_표준_변경과_긴급_변경():
    state = state_for("REQ-D04")
    check = PolicyCheckResult.model_validate(state["policy"].tool_output)
    text = render_decide_input(
        request=state["structured"], submitted_at=state["submitted_at"], effective_type="standard", verdict="자동 승인", risk=None,
        check=check, standard_id="STD-01", emergency_is_valid=None, policy=state["policy"], impact=None,
        time_violation_ids=[], needs_schedule=False,
    )  # fmt: skip
    assert "- 위험도: 없음 (표준 변경은 매기지 않는다)" in text and "- 표준 변경: STD-01" in text
    assert "해당 없음 (표준 변경은 영향도 검사를 생략한다)" in text and "일정 대안 필요: 아니오" in text


def test_코드가_만드는_설명은_엔진_근거_문장을_잇는다():
    check = PolicyCheckResult(
        violations=[Violation(rule_id="CHG-04", severity="fixable", evidence="롤백이 없다.")],
        exempted=[Violation(rule_id="CHG-01", severity="forbidden", evidence="x")],
    )
    assert fallback_explanation("조건부 승인", check) == "규칙 엔진 판정은 조건부 승인이다. 롤백이 없다. 유효한 긴급 변경으로 면제된 조항: CHG-01."
    assert fallback_explanation("승인", PolicyCheckResult()) == "규칙 엔진 판정은 승인이다. 규정 위반이 없다."


def test_재작성_요청은_엔진_위반_근거와_앞선_출력을_담는다():
    previous = draft(explanation="앞선 설명")
    text = retry_message(previous, ["CHG-01: 금요일 저녁이다"]).content
    assert "- CHG-01: 금요일 저녁이다" in text and previous.model_dump_json() in text
