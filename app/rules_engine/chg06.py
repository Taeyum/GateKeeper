"""CHG-06 동시간 변경 금지 (금지, 표준 변경 포함).

같은 대상에 이미 승인된 변경(change_calendar.scheduled_changes)과 작업 구간이 겹치면 위반이다.
"""

from app.schemas import Violation

from ._format import fmt_range, fmt_window, violation
from .context import WindowContext, overlaps

RULE_ID = "CHG-06"


def check(ctx: WindowContext) -> Violation | None:
    if ctx.window is None:
        return None
    w = ctx.window
    hits = [
        s
        for s in ctx.data.calendar.scheduled_changes
        if s.target == ctx.target and overlaps(w.start, w.end, s.start, s.end)
    ]
    if not hits:
        return None
    listed = ", ".join(f"{s.schedule_id} {s.title}, {fmt_range(s.start, s.end)}" for s in hits)
    return violation(
        ctx.data,
        RULE_ID,
        f"작업 구간({fmt_window(w)})이 같은 대상({ctx.target})에 이미 승인된 변경({listed})과 겹친다.",
    )
