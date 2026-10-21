"""CHG-01 배포 금지 시간대 (금지).

금요일 18:00부터 월요일 09:00까지는 일반 변경을 하지 않는다. 작업 구간이 이 시간대와 조금이라도
겹치면 위반이다. 시간대는 rules.json params(blocked_from, blocked_until)에서 읽는다.
"""

from datetime import datetime, time, timedelta

from app.schemas import Violation

from ._format import WEEKDAY_KO, fmt_window, violation
from .context import WindowContext, overlaps, rule_spec

RULE_ID = "CHG-01"
_DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def parse_weekly(value: str) -> tuple[int, time]:
    """"FRI 18:00" → (4, 18:00)."""
    day, clock = value.split()
    return _DAYS.index(day.upper()), time.fromisoformat(clock)


def blocked_intervals(
    start: datetime, end: datetime, blocked_from: str, blocked_until: str
) -> list[tuple[datetime, datetime]]:
    """[start, end)와 겹칠 수 있는 주간 금지 구간 목록. 시각은 start의 시간대로 만든다."""
    from_day, from_time = parse_weekly(blocked_from)
    until_day, until_time = parse_weekly(blocked_until)
    span_days = (until_day - from_day) % 7 or 7
    intervals = []
    # 작업 시작 일주일 전부터 종료일까지의 금지 시작일을 모두 본다. 앞 주에 시작한 금지 구간도 잡힌다.
    day = start.date() - timedelta(days=7)
    while day <= end.date():
        if day.weekday() == from_day:
            b_start = datetime.combine(day, from_time, tzinfo=start.tzinfo)
            b_end = datetime.combine(day + timedelta(days=span_days), until_time, tzinfo=start.tzinfo)
            intervals.append((b_start, b_end))
        day += timedelta(days=1)
    return intervals


def check(ctx: WindowContext) -> Violation | None:
    if ctx.window is None:
        return None
    w = ctx.window
    params = rule_spec(ctx.data, RULE_ID).params
    intervals = blocked_intervals(w.start, w.end, params["blocked_from"], params["blocked_until"])
    if not any(overlaps(w.start, w.end, b_start, b_end) for b_start, b_end in intervals):
        return None
    from_day, from_time = parse_weekly(params["blocked_from"])
    until_day, until_time = parse_weekly(params["blocked_until"])
    blocked = f"{WEEKDAY_KO[from_day]}요일 {from_time:%H:%M}~{WEEKDAY_KO[until_day]}요일 {until_time:%H:%M}"
    return violation(ctx.data, RULE_ID, f"작업 구간({fmt_window(w)})이 {blocked} 배포 금지 시간대와 겹친다.")
