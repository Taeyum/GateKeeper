"""⑤ validate 테스트 (fake LLM, 실제 규칙 엔진). 코드 검사는 decide가 만든 권고를 일부러 어긋나게 바꿔 확인한다."""

import pytest
from langgraph.graph import END

from app import data_loader, rules_engine
from app.agent.nodes import decide as decide_node
from app.agent.nodes import validate as node
from app.agent.nodes.decide import DecideDraft, decide
from app.agent.nodes.validate import (
    INCOMPLETE_WARNING,
    MAX_REWRITES,
    ValidationCheck,
    code_checks,
    render_validation_input,
    route_after_validate,
    validate,
)
from app.agent.results import SubagentResult, ValidationState
from app.data_loader import load_requests, load_rules
from app.llm.fake import set_fake_responses
from app.llm.usage import UsageCollector
from app.schemas import ChangeRequest, PolicyCheckResult, Recommendation


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


def gold(request_id: str):
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def request_of(request_id: str) -> ChangeRequest:
    data = gold(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate(data)


def state_for(request_id: str, **extra) -> dict:
    """classify, policy_agent, impact_agent가 하는 일을 실제 엔진으로 채운 부모 상태."""
    item, request = gold(request_id), request_of(request_id)
    check = rules_engine.check_all(request, item.submitted_at)
    standard = rules_engine.is_standard(request)
    emergency = rules_engine.emergency_valid(request) if request.is_emergency else None
    state = {
        "request_id": request_id,
        "submitted_at": item.submitted_at.isoformat(),
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


async def decided(request_id: str, explanation: str = "위반이 있어 반려예요.", **extra) -> dict:
    """decide를 fake LLM으로 돌려 recommendation이 들어 있는 상태를 만든다."""
    state = state_for(request_id, **extra)
    set_fake_responses("main", [DecideDraft(explanation=explanation, conditions=[], alternative_start=None, alternative_end=None)] * 2)
    out = await decide(state, {})
    return {**state, **out}


def checks(state: dict, rec: Recommendation | None = None) -> list[str]:
    check = PolicyCheckResult.model_validate(state["policy"].tool_output)
    return code_checks(
        rec or state["recommendation"],
        check,
        emergency_is_valid=state.get("emergency_valid"),
        standard_id=state.get("standard_change_id"),
        rules=load_rules(),
    )


# ---------------------------------------------------------------------------
# 코드 검사 (순수 함수)
# ---------------------------------------------------------------------------


async def test_dev_10건의_decide_결과는_코드_검사를_모두_통과한다():
    for item in load_requests("dev"):
        assert checks(await decided(item.request_id)) == [], item.request_id


async def test_없는_rule_id를_인용하면_잡는다():
    state = await decided("REQ-D01")
    rec = state["recommendation"]
    bad = rec.model_copy(update={"citations": [*rec.citations, "CHG-99"]})
    assert checks(state, bad) == ["존재하지 않는 조항을 인용했다: CHG-99."]


async def test_위반_목록에_없는_조항을_인용하거나_위반_조항을_빠뜨리면_잡는다():
    state = await decided("REQ-D01")  # 위반 CHG-01, 02, 03, 09, 10
    rec = state["recommendation"]
    assert checks(state, rec.model_copy(update={"citations": [*rec.citations, "CHG-04"]})) == [
        "위반 목록에 없는 조항을 인용했다: CHG-04."
    ]
    assert checks(state, rec.model_copy(update={"citations": rec.citations[:-1]})) == ["위반 조항이 인용에서 빠졌다: CHG-10."]


async def test_설명이_위반_목록에_없는_조항이나_없는_조항을_언급하면_잡고_위반_조항_언급은_괜찮다():
    state = await decided("REQ-D01")
    rec = state["recommendation"]
    assert checks(state, rec.model_copy(update={"explanation": "CHG-01 때문에 반려예요."})) == []
    assert checks(state, rec.model_copy(update={"explanation": "CHG-04 롤백이 없어서 반려예요."})) == [
        "설명이 위반 목록에 없는 조항을 언급했다: CHG-04."
    ]
    assert checks(state, rec.model_copy(update={"explanation": "CHG-99 때문이에요."})) == [
        "설명이 존재하지 않는 조항을 언급했다: CHG-99."
    ]
    # 조건부 대안 문장도 같은 기준으로 본다
    assert checks(state, rec.model_copy(update={"conditional_alternative": "CHG-07 요건을 맞춘다"})) == [
        "설명이 위반 목록에 없는 조항을 언급했다: CHG-07."
    ]


async def test_판정이_위반_목록으로_계산한_값과_다르거나_위반_목록이_바뀌면_잡는다():
    state = await decided("REQ-D01")
    rec = state["recommendation"]
    assert checks(state, rec.model_copy(update={"verdict": "승인"})) == ["판정(승인)이 위반 목록으로 계산한 값(반려)과 다르다."]
    assert "권고의 위반 목록이 규정 검사 결과와 다르다." in checks(state, rec.model_copy(update={"violations": rec.violations[:2]}))


async def test_유효한_긴급_변경은_CHG_07_인용과_면제_조항_언급이_괜찮고_표준_변경은_STD_인용이_괜찮다():
    emergency = await decided("REQ-D07")
    assert "CHG-07" in emergency["recommendation"].citations
    assert checks(emergency, emergency["recommendation"].model_copy(update={"explanation": "CHG-01은 면제했어요."})) == []
    standard = await decided("REQ-D04")
    assert standard["recommendation"].citations == ["STD-01"] and checks(standard) == []
    no_std = standard["recommendation"].model_copy(update={"citations": []})
    assert checks(standard, no_std) == ["위반 조항이 인용에서 빠졌다: STD-01."]


# ---------------------------------------------------------------------------
# 노드: 통과, 재작성, 미완료
# ---------------------------------------------------------------------------


async def run(state: dict, responses: list):
    set_fake_responses("main", responses)
    collector = UsageCollector()
    return await validate(state, {"callbacks": [collector]}), collector


OK = ValidationCheck(consistent=True, issues=[])


async def test_통과하면_권고를_그대로_두고_LLM을_한_번만_부른다():
    state = await decided("REQ-D01")
    out, collector = await run(state, [OK])
    assert out["validation"] == ValidationState(passed=True) and "recommendation" not in out
    assert out["metrics"] == {} and out["errors"] == [] and collector.snapshot().llm_calls == 1
    assert route_after_validate({**state, **out}) == END


async def test_LLM_검사는_main_모델로_하고_judge_모델은_쓰지_않는다(monkeypatch):
    roles = []
    real = node.get_llm
    monkeypatch.setattr(node, "get_llm", lambda role, settings=None: roles.append(role) or real(role, settings))
    await run(await decided("REQ-D01"), [OK])
    assert roles == ["main"]


async def test_LLM이_모순을_찾으면_사유를_들고_decide로_1회_돌아간다():
    state = await decided("REQ-D01")
    out, _ = await run(state, [ValidationCheck(consistent=False, issues=["  설명은 승인 가능이라는데 판정은 반려다  ", " "])])
    assert out["validation"] == ValidationState(passed=False, reasons=["설명 검증: 설명은 승인 가능이라는데 판정은 반려다"], rewrites=1)
    assert out["metrics"] == {"validator_rewrites": 1} and "recommendation" not in out
    assert route_after_validate({**state, **out}) == "decide"


async def test_모순이라면서_사유를_안_적어도_실패로_본다():
    out, _ = await run(await decided("REQ-D01"), [ValidationCheck(consistent=False, issues=[])])
    assert out["validation"].passed is False and out["validation"].reasons[0].startswith("설명 검증: ")


async def test_코드_검사에서_실패하면_LLM을_부르지_않고_재작성을_요청한다():
    state = await decided("REQ-D01")
    state["recommendation"] = state["recommendation"].model_copy(update={"citations": ["CHG-99"]})
    out, collector = await run(state, [OK])
    assert collector.snapshot().llm_calls == 0
    assert out["validation"].passed is False and out["validation"].rewrites == 1
    assert out["validation"].reasons[0] == "존재하지 않는 조항을 인용했다: CHG-99."


async def test_재작성_뒤에도_실패하면_근거_검증_미완료_경고를_붙이고_끝낸다():
    state = await decided("REQ-D01", validation=ValidationState(passed=False, reasons=["이전 사유"], rewrites=MAX_REWRITES))
    out, _ = await run(state, [ValidationCheck(consistent=False, issues=["여전히 모순이다"])])
    assert out["validation"] == ValidationState(passed=False, reasons=["설명 검증: 여전히 모순이다"], rewrites=1, incomplete=True)
    rec = out["recommendation"]
    assert any(w.startswith(f"{INCOMPLETE_WARNING}: ") and "여전히 모순이다" in w for w in rec.warnings)
    assert (rec.verdict, rec.violations) == (state["recommendation"].verdict, state["recommendation"].violations)  # 판정은 그대로
    assert out["metrics"] == {"validator_incomplete": 1}
    assert route_after_validate({**state, **out}) == END  # 더 돌지 않는다


async def test_LLM_호출이_실패하면_막지_않고_경고와_오류를_남긴다():
    out, _ = await run(await decided("REQ-D01"), [])  # fake 응답 큐가 비어 호출이 실패한다
    assert out["validation"].passed is True
    assert out["errors"] == ["validate: LLM 검사 실패 (FakeResponsesExhausted)"] and out["metrics"] == {"partial_failures": 1}
    assert "근거 검증: LLM 검사를 하지 못했다. 코드 검사만 통과했다." in out["recommendation"].warnings


def test_분기_함수는_실패하고_끝내지_않았을_때만_decide로_돌린다():
    assert route_after_validate({}) == END  # 검증 결과가 없으면(v1 경로 등) 끝낸다
    assert route_after_validate({"validation": ValidationState(passed=True)}) == END
    assert route_after_validate({"validation": ValidationState(passed=False, rewrites=1)}) == "decide"
    assert route_after_validate({"validation": ValidationState(passed=False, rewrites=1, incomplete=True)}) == END


def test_LLM에_보내는_입력은_코드_계산_결과와_설명을_담고_본문_값은_없다():
    state = {"policy": SubagentResult(tool_output=PolicyCheckResult().model_dump(mode="json"))}
    rec = Recommendation(verdict="승인", risk="낮음", explanation="위반이 없어요.")
    text = render_validation_input(rec, PolicyCheckResult.model_validate(state["policy"].tool_output))
    assert "- 판정: 승인" in text and "## 검증할 설명\n위반이 없어요." in text and "## 검증할 조건부 대안\n없음" in text


# ---------------------------------------------------------------------------
# decide의 재작성
# ---------------------------------------------------------------------------


class Recorder:
    def __init__(self, chain):
        self.chain, self.calls = chain, []

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return await self.chain.ainvoke(messages, config)


@pytest.fixture
def decide_recorder(monkeypatch):
    holder: list[Recorder] = []
    real = decide_node.structured_llm

    def wrapped(llm, schema, settings=None):
        holder.append(Recorder(real(llm, schema, settings)))
        return holder[-1]

    monkeypatch.setattr(decide_node, "structured_llm", wrapped)
    return holder


async def test_validate가_되돌리면_decide는_사유와_앞선_설명을_붙여_다시_쓴다(decide_recorder):
    first = await decided("REQ-D01", explanation="처음 쓴 설명이에요.")
    decide_recorder.clear()
    redo = {**first, "validation": ValidationState(passed=False, reasons=["설명 검증: 판정과 결론이 다르다"], rewrites=1)}
    set_fake_responses("main", [DecideDraft(explanation="고친 설명이에요.", conditions=[], alternative_start=None, alternative_end=None)] * 2)
    out = await decide(redo, {})
    assert out["recommendation"].explanation == "고친 설명이에요."
    messages = decide_recorder[0].calls[0]
    assert len(messages) == 3  # 시스템, 입력, 재작성 요청
    feedback = messages[2].content
    assert "근거 검증을 통과하지 못했다" in feedback and "- 설명 검증: 판정과 결론이 다르다" in feedback
    assert "처음 쓴 설명이에요." in feedback and "위반 목록, 판정, 위험도는 바꾸지 않는다" in feedback
    # 판정과 위반은 재작성에서도 같다
    assert (out["recommendation"].verdict, out["recommendation"].violations) == (first["recommendation"].verdict, first["recommendation"].violations)


async def test_검증을_통과했거나_미완료로_끝났으면_decide에_재작성_메시지를_붙이지_않는다(decide_recorder):
    base = state_for("REQ-D01")
    for validation in (None, ValidationState(passed=True), ValidationState(passed=False, reasons=["x"], rewrites=1, incomplete=True)):
        decide_recorder.clear()
        set_fake_responses("main", [DecideDraft(explanation="설명", conditions=[], alternative_start=None, alternative_end=None)] * 2)
        await decide({**base, **({"validation": validation} if validation else {})}, {})
        assert len(decide_recorder[0].calls[0]) == 2
