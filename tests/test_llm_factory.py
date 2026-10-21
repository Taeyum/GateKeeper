import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.llm.factory import LLMConfigError, check_structured_output, get_llm, structured_llm
from app.llm.fake import FakeChatModel, FakeResponsesExhausted, set_fake_responses, set_fake_responses_for
from app.schemas import Recommendation


class Answer(BaseModel):
    city: str
    country: str


def test_fake는_넣어_둔_응답을_순서대로_돌려준다(make_settings):
    s = make_settings(llm_provider="fake")
    llm = get_llm("main", s)
    assert isinstance(llm, FakeChatModel) and llm.role == "main"
    set_fake_responses("main", ["첫째", "둘째"])
    assert llm.invoke("a").content == "첫째"
    assert llm.invoke("b").content == "둘째"
    with pytest.raises(FakeResponsesExhausted):
        llm.invoke("c")


def test_fake_응답은_role별로_분리된다(make_settings):
    s = make_settings(llm_provider="fake")
    set_fake_responses("main", ["m"])
    set_fake_responses("small", ["s"])
    assert get_llm("small", s).invoke("x").content == "s"
    assert get_llm("main", s).invoke("x").content == "m"


def test_fake_구조화_출력은_스키마로_돌려준다(make_settings):
    s = make_settings(llm_provider="fake")
    llm = get_llm("main", s)
    set_fake_responses("main", [Answer(city="서울", country="대한민국"), {"city": "부산", "country": "대한민국"},
                                '{"city": "대구", "country": "대한민국"}'])  # fmt: skip
    chain = structured_llm(llm, Answer, s)
    assert [chain.invoke("x").city for _ in range(3)] == ["서울", "부산", "대구"]


def test_fake는_스키마_이름으로_지정한_큐를_먼저_보고_없으면_role_큐로_넘어간다(make_settings):
    s = make_settings(llm_provider="fake")
    llm = get_llm("main", s)
    set_fake_responses_for("main", Answer, [{"city": "서울", "country": "대한민국"}])
    set_fake_responses("main", [Recommendation(verdict="승인")])
    # Recommendation을 요청하는 호출은 Answer 전용 큐를 건드리지 않는다 (병렬 노드의 순서 문제를 없앤다)
    assert structured_llm(llm, Recommendation, s).invoke("x").verdict == "승인"
    assert structured_llm(llm, Answer, s).invoke("x").city == "서울"
    set_fake_responses("main", [{"city": "부산", "country": "대한민국"}])
    assert structured_llm(llm, Answer, s).invoke("x").city == "부산"  # 전용 큐가 비면 role 큐
    with pytest.raises(FakeResponsesExhausted):
        structured_llm(llm, Answer, s).invoke("x")


def test_fake_구조화_출력은_스키마_위반을_드러낸다(make_settings):
    s = make_settings(llm_provider="fake")
    set_fake_responses("main", [{"verdict": "보류"}])
    with pytest.raises(ValueError):
        structured_llm(get_llm("main", s), Recommendation, s).invoke("x")


def test_ollama는_temperature_0과_컨텍스트를_명시한다(make_settings):
    llm = get_llm("main", make_settings(llm_provider="ollama"))
    assert isinstance(llm, ChatOllama)
    assert (llm.model, llm.temperature, llm.num_ctx, llm.reasoning) == ("qwen3:8b", 0, 16384, False)
    assert get_llm("small", make_settings(llm_provider="ollama")).model == "qwen3:0.6b"


@pytest.mark.parametrize(
    ("role", "model"),
    [("main", "claude-sonnet-5-5"), ("small", "claude-haiku-5-5"), ("judge", "claude-opus-5-5")],
)
def test_anthropic은_temperature를_보내지_않는다(make_settings, role, model):
    llm = get_llm(role, make_settings(llm_provider="anthropic", anthropic_api_key="sk-ant-dummy"))
    assert isinstance(llm, ChatAnthropic) and llm.model == model
    assert llm.temperature is None
    # 실제 요청 본문에도 temperature가 없고 max_tokens가 명시된다. 5.5 모델은 temperature 0을 400으로 거부한다.
    payload = llm._get_request_payload([HumanMessage("hi")])
    assert "temperature" not in payload
    assert payload["max_tokens"] == 16000


def test_anthropic에_temperature_0을_주면_라이브러리가_막는다():
    """factory가 temperature를 보내지 않는 이유. docs/PROGRESS.md 결정 기록의 근거."""
    llm = ChatAnthropic(model="claude-sonnet-5-5", api_key="sk-ant-dummy", max_tokens=100, temperature=0)
    with pytest.raises(ValueError, match="temperature"):
        llm._get_request_payload([HumanMessage("hi")])


def test_openai는_temperature_0이다(make_settings):
    llm = get_llm("main", make_settings(llm_provider="openai", openai_api_key="sk-dummy"))
    assert isinstance(llm, ChatOpenAI)
    assert (llm.model_name, llm.temperature, llm.max_tokens) == ("gpt-4o", 0, 16000)


@pytest.mark.parametrize(("provider", "env"), [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")])
def test_키가_없으면_이름만_알려_주고_멈춘다(make_settings, provider, env):
    with pytest.raises(LLMConfigError, match=env):
        get_llm("main", make_settings(llm_provider=provider))


class _Recorder:
    """with_structured_output 호출 인자를 기록하는 최소 가짜."""

    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema, **kwargs):
        self.calls.append((schema, kwargs))
        return "chain"


@pytest.mark.parametrize("provider", ["ollama", "anthropic", "openai"])
def test_실제_provider는_json_schema_방식을_명시한다(make_settings, provider):
    recorder = _Recorder()
    assert structured_llm(recorder, Answer, make_settings(llm_provider=provider)) == "chain"
    assert recorder.calls == [(Answer, {"method": "json_schema"})]


def test_check_structured_output_성공(make_settings):
    s = make_settings(llm_provider="fake")
    set_fake_responses("main", [{"city": "서울", "country": "대한민국"}])
    result = check_structured_output("main", s)
    assert result.ok and result.parsed == {"city": "서울", "country": "대한민국"}
    assert (result.provider, result.model) == ("fake", "fake-main")


def test_check_structured_output_실패는_예외가_아니라_결과다(make_settings):
    result = check_structured_output("main", make_settings(llm_provider="fake"))  # 응답 큐가 비어 있다
    assert not result.ok and "FakeResponsesExhausted" in result.error
