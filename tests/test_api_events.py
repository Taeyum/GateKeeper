"""요청별 SSE 이벤트 기록 (docs/API_CONTRACT.md 4장): replay, 라이브, 종료 규칙, ping."""

import asyncio

from app.api.events import PING, EventLog, format_event


async def collect(log: EventLog, **kwargs) -> list[str]:
    return await asyncio.wait_for(_drain(log, **kwargs), timeout=5)


async def _drain(log: EventLog, **kwargs) -> list[str]:
    return [chunk async for chunk in log.stream(**kwargs)]


def names(chunks: list[str]) -> list[str]:
    return [chunk.splitlines()[0].removeprefix("event: ") for chunk in chunks]


def test_이벤트는_event_줄과_JSON_한_줄의_data_줄_그리고_빈_줄이다():
    assert format_event("node_started", {"node": "policy_agent", "at": "2026-10-29T16:00:05+09:00"}) == (
        'event: node_started\ndata: {"node": "policy_agent", "at": "2026-10-29T16:00:05+09:00"}\n\n'
    )
    assert "한글" in format_event("done", {"message": "한글"})  # 이스케이프하지 않는다


async def test_연결하면_지금까지의_이벤트를_순서대로_다시_보낸다():
    log = EventLog()
    for name in ("node_started", "node_completed", "done"):
        await log.append(name, {"n": name})
    chunks = await collect(log)
    assert names(chunks) == ["node_started", "node_completed", "done"]
    assert await collect(log) == chunks  # 다시 연결해도 같다


async def test_replay_뒤에_이어서_새_이벤트를_받고_done에서_닫는다():
    log = EventLog()
    await log.append("node_started", {"i": 1})
    consumer = asyncio.create_task(_drain(log))
    await asyncio.sleep(0.02)
    await log.append("node_completed", {"i": 2})
    await asyncio.sleep(0.02)
    assert not consumer.done()
    await log.append("done", {"i": 3})
    assert names(await asyncio.wait_for(consumer, 5)) == ["node_started", "node_completed", "done"]


async def test_replay와_라이브_사이에_끼어든_이벤트도_놓치지_않는다():
    log = EventLog()
    await log.append("node_started", {"i": 0})

    async def producer():
        for i in range(1, 30):
            await log.append("node_completed", {"i": i})
            await asyncio.sleep(0)
        await log.append("done", {"i": 30})

    consumer = asyncio.create_task(_drain(log))
    await producer()
    got = await asyncio.wait_for(consumer, 5)
    assert len(got) == 31 and names(got)[-1] == "done"


async def test_error도_스트림을_닫는다():
    log = EventLog()
    await log.append("error", {"code": "internal", "message": "m"})
    await log.append("node_started", {})  # error 뒤의 것은 보내지 않는다
    assert names(await collect(log)) == ["error"]


async def test_승인_대기는_기록의_마지막일_때만_닫는다():
    waiting = EventLog()
    await waiting.append("node_started", {})
    await waiting.append("awaiting_approval", {})
    assert names(await collect(waiting)) == ["node_started", "awaiting_approval"]  # 아직 결정 전: 여기서 닫는다

    decided = EventLog()
    for name in ("awaiting_approval", "node_completed", "node_started", "node_completed", "done"):
        await decided.append(name, {})
    # 결정이 난 뒤에 연결하면 awaiting_approval에서 멈추지 않고 done까지 받는다
    assert names(await collect(decided)) == ["awaiting_approval", "node_completed", "node_started", "node_completed", "done"]


async def test_이벤트가_없으면_ping_주석_줄을_보낸다():
    log = EventLog()
    stream = log.stream(ping_seconds=0.01)
    assert await asyncio.wait_for(anext(stream), 5) == PING == ": ping\n\n"
    assert await asyncio.wait_for(anext(stream), 5) == PING  # 계속 보낸다
    await log.append("done", {})
    assert (await asyncio.wait_for(anext(stream), 5)).startswith("event: done")
    await stream.aclose()


async def test_구독자가_여럿이어도_각자_처음부터_받는다():
    log = EventLog()
    first, second = asyncio.create_task(_drain(log)), asyncio.create_task(_drain(log))
    await asyncio.sleep(0.01)
    await log.append("node_started", {})
    await log.append("done", {})
    assert names(await asyncio.wait_for(first, 5)) == names(await asyncio.wait_for(second, 5)) == ["node_started", "done"]
