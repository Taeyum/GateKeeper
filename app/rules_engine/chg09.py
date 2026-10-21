"""CHG-09 임시 오픈 만료일 필수 (보완).

출발지가 외부인 방화벽 요청은 허용 기한을 적고, 기한은 최대 30일(rules.json params max_days)이다.
기한이 없거나 30일을 넘으면 위반이다. 30일은 통과, 31일은 위반.
expires_at만 있으면 작업 시작 시각부터 기한을 계산한다(시작도 없으면 요청 시각부터).
expires_days와 expires_at이 둘 다 있으면 더 긴 쪽으로 본다.
"""

from datetime import timedelta

from app.schemas import Violation

from ._format import fmt_duration, violation
from .context import RequestContext, is_internal, rule_spec, rules_tz, to_tz

RULE_ID = "CHG-09"


def allowed_period(ctx: RequestContext) -> timedelta | None:
    """요청이 허용을 원하는 기간. 기한이 없으면 None."""
    periods = []
    if ctx.request.expires_days is not None:
        periods.append(timedelta(days=ctx.request.expires_days))
    if ctx.request.expires_at is not None:
        base = ctx.window.start if ctx.window else ctx.submitted_at
        periods.append(to_tz(ctx.request.expires_at, rules_tz(ctx.data)) - base)
    return max(periods) if periods else None


def check(ctx: RequestContext) -> Violation | None:
    external = [n for n in ctx.sources.networks if not is_internal(n, ctx.data)]
    if not external:
        return None
    max_days = rule_spec(ctx.data, RULE_ID).params["max_days"]
    period = allowed_period(ctx)
    sources = ", ".join(str(n) for n in external)
    if period is None:
        return violation(ctx.data, RULE_ID, f"외부 출발지({sources}) 허용인데 허용 기한이 없다.")
    if period <= timedelta(days=max_days):
        return None
    rest = period - timedelta(days=period.days)
    shown = f"{period.days}일" + (f" {fmt_duration(rest)}" if rest else "")
    return violation(
        ctx.data,
        RULE_ID,
        f"외부 출발지({sources}) 허용 기한({shown})이 최대 {max_days}일을 넘는다.",
    )
