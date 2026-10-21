"""CHG-05 Tier-1 사전 공지 (보완).

Tier-1 서비스 변경은 작업 시작 24시간 전까지 요청한다. 정확히 24시간 전은 통과다.
등급과 시간은 rules.json params(tier, notice_hours)에서 읽는다.
"""

from datetime import timedelta

from app.schemas import Violation

from ._format import fmt_dt, fmt_duration, violation
from .context import WindowContext, rule_spec

RULE_ID = "CHG-05"


def check(ctx: WindowContext) -> Violation | None:
    if ctx.window is None:
        return None
    params = rule_spec(ctx.data, RULE_ID).params
    if ctx.service.tier != params["tier"]:
        return None
    notice = timedelta(hours=params["notice_hours"])
    lead = ctx.window.start - ctx.submitted_at
    if lead >= notice:
        return None
    when = f"작업 시작({fmt_dt(ctx.window.start)})"
    timing = f"{when} {fmt_duration(lead)} 전에" if lead > timedelta(0) else f"{when} 이후에"
    return violation(
        ctx.data,
        RULE_ID,
        f"{params['tier']} 서비스({ctx.target}) 변경인데 {timing} 요청해 "
        f"{params['notice_hours']}시간 전 요청 기준보다 늦다.",
    )
