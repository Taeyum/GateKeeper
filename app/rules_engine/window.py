"""대안 일정 검증(check_window)과 탐색(next_valid_window).

decide 노드가 LLM이 낸 대안 일정을 다시 검사하고, 계속 위반이면 엔진이 찾은 가장 빠른 통과 구간으로
대체한다. 요청 본문에 의존하지 않는 시간 조항(CHG-01, 05, 06, 10)만 본다.
"""

from datetime import datetime, timedelta

from app.schemas import ChangeType, GKData, Violation

from .context import build_window_context, rules_tz, to_tz
from .registry import run_window_rules

# 탐색 설정이다 (규정 값이 아니다). 1단계 docstring과 리뷰 D1에서 정했다.
STEP = timedelta(minutes=30)
HORIZON = timedelta(days=14)


def check_window(
    target: str,
    change_type: ChangeType,
    start: datetime,
    end: datetime,
    submitted_at: datetime,
    data: GKData,
) -> list[Violation]:
    return run_window_rules(build_window_context(target, change_type, start, end, submitted_at, data))


def ceil_to_step(value: datetime) -> datetime:
    """다음 30분 경계로 올림. 이미 경계(분이 0 또는 30이고 초가 0)면 그대로."""
    floor = value.replace(minute=value.minute - value.minute % 30, second=0, microsecond=0)
    return value if floor == value else floor + STEP


def next_valid_window(
    target: str,
    change_type: ChangeType,
    duration: timedelta,
    after: datetime,
    submitted_at: datetime,
    data: GKData,
) -> tuple[datetime, datetime] | None:
    if duration <= timedelta(0):
        raise ValueError("작업 시간(duration)은 0보다 커야 한다")
    after = to_tz(after, rules_tz(data))
    candidate = ceil_to_step(after)
    while candidate < after + HORIZON:
        end = candidate + duration
        if not check_window(target, change_type, candidate, end, submitted_at, data):
            return candidate, end
        candidate += STEP
    return None
