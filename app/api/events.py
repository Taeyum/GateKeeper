"""요청별 SSE 이벤트 기록과 구독 (docs/API_CONTRACT.md 4장).

그래프 실행은 SSE 연결과 분리돼 있다. 러너가 이벤트를 이 기록에 쌓고, stream 엔드포인트는 기록을 처음부터 읽기만 한다.
연결하면 지금까지의 이벤트를 순서대로 다시 보내고(replay) 이어서 새 이벤트를 보내는데, 두 일을 같은 루프(인덱스를 따라 읽기)가
하기 때문에 POST 응답과 스트림 연결 사이에 생긴 이벤트를 놓치지 않는다. 연결이 끊겨도 기록과 실행은 그대로다.

기록은 서버 메모리다. 서버를 다시 띄우면 사라지고, 그때는 체크포인터 상태로 종료 이벤트 하나만 보낸다(views.terminal_event).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

PING_SECONDS = 15.0
PING = ": ping\n\n"
END_EVENTS = frozenset({"done", "error"})


def format_event(name: str, data: dict[str, Any]) -> str:
    """event: 줄과 data: 줄(JSON 한 줄), 빈 줄로 끝난다."""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class EventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._changed = asyncio.Condition()

    async def append(self, name: str, data: dict[str, Any]) -> None:
        async with self._changed:
            self.events.append((name, data))
            self._changed.notify_all()

    def _closes_after(self, position: int) -> bool:
        """이 이벤트를 보낸 뒤 스트림을 닫을지. done, error는 항상 닫고, awaiting_approval은 기록의 마지막일 때만 닫는다.

        승인 대기 뒤에 결정이 나서 기록이 더 쌓였다면, 그 뒤에 연결한 구독자는 awaiting_approval에서 멈추지 않고 done까지 받는다.
        """
        name = self.events[position][0]
        return name in END_EVENTS or (name == "awaiting_approval" and position == len(self.events) - 1)

    async def stream(self, ping_seconds: float | None = None) -> AsyncIterator[str]:
        """replay한 뒤 새 이벤트를 기다리며 보낸다. ping_seconds 동안 이벤트가 없으면 `: ping` 주석 줄을 보낸다."""
        interval = PING_SECONDS if ping_seconds is None else ping_seconds
        position = 0
        while True:
            if position < len(self.events):
                name, data = self.events[position]
                closing = self._closes_after(position)
                position += 1
                yield format_event(name, data)
                if closing:
                    return
                continue
            idle = False
            async with self._changed:
                if position >= len(self.events):
                    try:
                        await asyncio.wait_for(self._changed.wait(), interval)
                    except TimeoutError:
                        idle = True
            if idle:
                yield PING
