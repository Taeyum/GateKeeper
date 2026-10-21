"""일시 오류만 지수 백오프로 재시도한다.

잘못된 인자나 없는 대상 같은 영구 오류는 다시 해도 같은 결과라서 재시도하지 않고 바로 올린다.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")

# 일시적이라고 보는 오류: 호출 시간 초과와 연결 끊김
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (asyncio.TimeoutError, ConnectionError)


async def with_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    retries: int = 2,
    base_delay: float = 0.3,
    factor: float = 2.0,
    retry_on: tuple[type[BaseException], ...] = TRANSIENT_ERRORS,
    **kwargs: Any,
) -> tuple[T, int]:
    """fn(*args, **kwargs)를 호출한다. retry_on에 해당하는 오류만 최대 retries번 다시 시도한다.

    시도 사이에는 base_delay, base_delay * factor, ... 만큼 기다린다(지수 백오프).
    횟수를 다 써도 실패하면 마지막 오류를 그대로 올린다.

    반환: (결과, 재시도 횟수). 재시도 횟수는 state.metrics의 Tool 재시도 수에 더한다.
    """
    attempt = 0
    while True:
        try:
            return await fn(*args, **kwargs), attempt
        except retry_on:
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * factor**attempt)
            attempt += 1