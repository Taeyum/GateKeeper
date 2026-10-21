"""대안 품질 Judge 테스트 (fake LLM)."""

import pytest

from app import data_loader
from app.data_loader import load_requests
from app.llm.fake import FakeResponsesExhausted, set_fake_responses
from app.llm.usage import UsageCollector
from eval.judge import (
    JudgeScore,
    _system_prompt,
    _user_prompt,
    judge_alternative,
    judge_independent,
    judge_prompt_hash,
    needs_judging,
)


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


def gold(request_id: str):
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def test_반려와_조건부_승인만_채점_대상이다():
    assert needs_judging(gold("REQ-D01")) and needs_judging(gold("REQ-D03"))  # 반려, 조건부 승인
    assert not needs_judging(gold("REQ-D02")) and not needs_judging(gold("REQ-D04"))  # 승인, 자동 승인


async def test_대안이_필요_없는_요청은_호출하지_않는다(fake_env):
    collector = UsageCollector()
    result = await judge_alternative(gold("REQ-D06"), "아무 대안", "본문", config={"callbacks": [collector]})
    assert result is None and collector.snapshot().llm_calls == 0


@pytest.mark.parametrize("alternative", [None, "", "   "])
async def test_대안이_비면_호출_없이_1점이다(fake_env, alternative):
    collector = UsageCollector()
    result = await judge_alternative(gold("REQ-D01"), alternative, "본문", config={"callbacks": [collector]})
    assert result is not None and result.score == 1
    assert collector.snapshot().llm_calls == 0


async def test_Judge_점수를_그대로_돌려주고_judge_역할_모델을_쓴다(fake_env):
    set_fake_responses("judge", [{"score": 4, "reason": "위반을 해소한다."}])
    collector = UsageCollector()
    result = await judge_alternative(gold("REQ-D01"), "11/03 14시에 /32로 7일", "본문", config={"callbacks": [collector]})
    assert result == JudgeScore(score=4, reason="위반을 해소한다.")
    summary = collector.snapshot()
    assert summary.llm_calls == 1 and set(summary.by_model) == {"fake-judge"}


async def test_구조화_출력이_틀리면_1회_재시도한다(fake_env):
    set_fake_responses("judge", [{"score": 9, "reason": "범위 밖"}, {"score": 3, "reason": "일부 해소"}])
    result = await judge_alternative(gold("REQ-D01"), "대안", "본문")
    assert result is not None and result.score == 3


async def test_두_번_틀리면_예외를_올린다(fake_env):
    set_fake_responses("judge", [{"score": 0, "reason": "x"}, {"score": 6, "reason": "x"}])
    with pytest.raises(ValueError):
        await judge_alternative(gold("REQ-D01"), "대안", "본문")


async def test_다른_종류의_실패는_재시도하지_않는다(fake_env):
    with pytest.raises(FakeResponsesExhausted):
        await judge_alternative(gold("REQ-D01"), "대안", "본문")


def test_프롬프트에_정적_컨텍스트와_정답_정보가_들어간다():
    d01 = gold("REQ-D01")
    system = _system_prompt()
    assert "CHG-01 배포 금지 시간대" in system and "payments-db" in system and "FRZ-01" in system
    assert "INC-2026-0412" in system and "{policy}" not in system
    user = _user_prompt(d01, "마스킹된 본문", "시스템 대안")
    assert "2026-10-29 16:00 (목) Asia/Seoul" in user and "마스킹된 본문" in user
    assert "CHG-01, CHG-02, CHG-03, CHG-09, CHG-10" in user and "시스템 대안" in user
    assert d01.expected.conditional_alternative in user


def test_Judge_모델이_main과_같으면_비독립이다(make_settings):
    assert judge_independent(make_settings(llm_provider="ollama")) is False  # 둘 다 qwen3:8b
    assert judge_independent(make_settings(llm_provider="anthropic")) is True  # sonnet 대 opus
    assert judge_independent(make_settings(llm_provider="ollama", model_judge="qwen3:0.6b")) is True


def test_프롬프트_해시는_sha256이다():
    assert len(judge_prompt_hash()) == 64
