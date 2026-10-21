"""CHG-10 배포 동결 기간 (금지, 표준 변경 포함).

change_calendar.freeze_periods의 동결 기간과 작업 구간이 조금이라도 겹치고, 대상이 그 범위(scope)에
속하면 위반이다. scope가 "*"이면 모든 서비스가 대상이다. 동결 기간도 반열린 구간으로 본다.
"""

from app.schemas import Violation

from ._format import fmt_range, fmt_window, violation
from .context import WindowContext, overlaps

RULE_ID = "CHG-10"
_ALL = "*"


def check(ctx: WindowContext) -> Violation | None:
    if ctx.window is None:
        return None
    w = ctx.window
    hits = [
        f
        for f in ctx.data.calendar.freeze_periods
        if (_ALL in f.scope or ctx.target in f.scope) and overlaps(w.start, w.end, f.start, f.end)
    ]
    if not hits:
        return None
    listed = ", ".join(f"{f.freeze_id} {f.title}, {fmt_range(f.start, f.end)}" for f in hits)
    return violation(
        ctx.data,
        RULE_ID,
        f"작업 구간({fmt_window(w)})이 동결 기간({listed})과 겹치고 대상({ctx.target})이 그 동결 범위에 속한다.",
    )
