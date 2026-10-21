"""UsageCollector와 fake 모델 콜백 테스트."""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from pydantic import BaseModel

from app.llm.factory import get_llm, structured_llm
from app.llm.fake import FakeResponsesExhausted, set_fake_responses
from app.llm.usage import UNKNOWN_MODEL, ModelUsage, UsageCollector, UsageSummary


class Answer(BaseModel):
    city: str


@pytest.fixture
def fake_settings(make_settings):
    return make_settings(llm_provider="fake")


def test_fake_일반_호출도_호출_수와_토큰이_집계된다(fake_settings):
    set_fake_responses("main", ["첫째 응답", "둘째 응답"])
    collector = UsageCollector()
    llm = get_llm("main", fake_settings)
    llm.invoke("가나다라마바사아", config={"callbacks": [collector]})
    llm.invoke("가나다라마바사아", config={"callbacks": [collector]})
    summary = collector.snapshot()
    assert summary.llm_calls == 2
    assert set(summary.by_model) == {"fake-main"}
    assert summary.by_model["fake-main"].calls == 2
    assert summary.input_tokens > 0 and summary.output_tokens > 0


def test_fake_구조화_출력도_콜백을_거친다(fake_settings):
    set_fake_responses("main", [{"city": "서울"}])
    collector = UsageCollector()
    chain = structured_llm(get_llm("main", fake_settings), Answer, fake_settings)
    assert chain.invoke("x", config={"callbacks": [collector]}).city == "서울"
    assert collector.snapshot().llm_calls == 1


async def test_비동기_호출도_집계된다(fake_settings):
    set_fake_responses("main", [{"city": "서울"}])
    collector = UsageCollector()
    chain = structured_llm(get_llm("main", fake_settings), Answer, fake_settings)
    assert (await chain.ainvoke("x", config={"callbacks": [collector]})).city == "서울"
    assert collector.snapshot().llm_calls == 1


def test_수집기를_나누면_집계도_나뉜다(fake_settings):
    set_fake_responses("main", ["a"])
    set_fake_responses("judge", ["b", "c"])
    system, judge = UsageCollector(), UsageCollector()
    get_llm("main", fake_settings).invoke("x", config={"callbacks": [system]})
    get_llm("judge", fake_settings).invoke("x", config={"callbacks": [judge]})
    get_llm("judge", fake_settings).invoke("x", config={"callbacks": [judge]})
    assert system.snapshot().llm_calls == 1 and set(system.snapshot().by_model) == {"fake-main"}
    assert judge.snapshot().llm_calls == 2 and set(judge.snapshot().by_model) == {"fake-judge"}


def test_실패한_호출은_오류_수로_센다(fake_settings):
    collector = UsageCollector()
    with pytest.raises(FakeResponsesExhausted):
        get_llm("main", fake_settings).invoke("x", config={"callbacks": [collector]})
    summary = collector.snapshot()
    assert (summary.llm_calls, summary.llm_errors, summary.by_model) == (1, 1, {})


def test_모델_이름이_없으면_unknown으로_모으고_reasoning_토큰을_따로_남긴다():
    collector = UsageCollector()
    message = AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": 10, "output_tokens": 30, "total_tokens": 40,
            "output_token_details": {"reasoning": 20},
        },
    )  # fmt: skip
    collector.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
    usage = collector.snapshot().by_model[UNKNOWN_MODEL]
    assert (usage.input_tokens, usage.output_tokens, usage.reasoning_tokens) == (10, 30, 20)


def test_호출_하나의_입력_토큰_최댓값을_따로_센다(fake_settings):
    set_fake_responses("main", ["짧다", "가" * 400])
    collector = UsageCollector()
    llm = get_llm("main", fake_settings)
    llm.invoke("가" * 40, config={"callbacks": [collector]})
    llm.invoke("가" * 800, config={"callbacks": [collector]})
    summary = collector.snapshot()
    assert summary.max_input_tokens == 200  # 800자 / 4
    assert summary.input_tokens == 210  # 합계는 두 호출을 더한다


def test_요약끼리_더하면_모델별로_합쳐진다():
    a = UsageSummary(
        llm_calls=1, max_input_tokens=5, by_model={"m": ModelUsage(calls=1, input_tokens=5, output_tokens=2)}
    )
    b = UsageSummary(
        llm_calls=2, llm_errors=1, max_input_tokens=7,
        by_model={"m": ModelUsage(calls=2, input_tokens=7, output_tokens=3), "n": ModelUsage(calls=1, input_tokens=1)},
    )  # fmt: skip
    a.add(b)
    assert (a.llm_calls, a.llm_errors, a.max_input_tokens) == (3, 1, 7)
    assert a.by_model["m"].input_tokens == 12 and a.by_model["n"].calls == 1
    assert a.input_tokens == 13 and a.output_tokens == 5
