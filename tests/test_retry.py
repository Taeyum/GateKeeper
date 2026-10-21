import asyncio

import pytest

from app.agent.retry import with_retry


@pytest.fixture
def sleeps(monkeypatch):
    """실제로 기다리지 않고 대기 시간만 기록한다."""
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


def _flaky(failures: int, error: type[BaseException] = asyncio.TimeoutError):
    """앞의 failures번은 error를 던지고 그 뒤로는 성공하는 코루틴 함수와 호출 기록."""
    calls: list[tuple] = []

    async def fn(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) <= failures:
            raise error("일시 오류")
        return "ok"

    return fn, calls


async def test_첫_시도에_성공하면_재시도_0회(sleeps):
    fn, calls = _flaky(0)
    assert await with_retry(fn) == ("ok", 0)
    assert len(calls) == 1 and sleeps == []


async def test_일시_오류는_백오프로_재시도한다(sleeps):
    fn, calls = _flaky(2)
    assert await with_retry(fn, retries=2, base_delay=0.3) == ("ok", 2)
    assert len(calls) == 3
    assert sleeps == pytest.approx([0.3, 0.6])  # 지수 백오프


async def test_횟수를_다_쓰면_마지막_오류를_올린다(sleeps):
    fn, calls = _flaky(10)
    with pytest.raises(asyncio.TimeoutError):
        await with_retry(fn, retries=2, base_delay=0.3)
    assert len(calls) == 3  # 최초 1번 + 재시도 2번
    assert sleeps == pytest.approx([0.3, 0.6])


async def test_영구_오류는_재시도하지_않는다(sleeps):
    fn, calls = _flaky(10, error=ValueError)
    with pytest.raises(ValueError):
        await with_retry(fn)
    assert len(calls) == 1 and sleeps == []


async def test_연결_오류도_일시_오류로_본다(sleeps):
    fn, _ = _flaky(1, error=ConnectionError)
    assert await with_retry(fn) == ("ok", 1)


async def test_retry_on으로_대상을_바꿀_수_있다(sleeps):
    fn, calls = _flaky(1, error=KeyError)
    assert await with_retry(fn, retry_on=(KeyError,)) == ("ok", 1)
    fn2, _ = _flaky(1, error=asyncio.TimeoutError)
    with pytest.raises(asyncio.TimeoutError):
        await with_retry(fn2, retry_on=(KeyError,))


async def test_retries_0이면_재시도하지_않는다(sleeps):
    fn, calls = _flaky(1)
    with pytest.raises(asyncio.TimeoutError):
        await with_retry(fn, retries=0)
    assert len(calls) == 1


async def test_인자를_그대로_넘긴다(sleeps):
    fn, calls = _flaky(1)
    await with_retry(fn, "a", 1, retries=2, key="v")
    assert calls == [(("a", 1), {"key": "v"})] * 2