"""CHG-02 전체 대역 오픈 금지 (금지, 긴급 변경도 적용).

방화벽 출발지에 0.0.0.0/0(또는 ::/0)을 허용하지 않는다. 출발지를 적지 않았거나 범위를 특정하지
않은 요청은 0.0.0.0/0으로 본다 (정규화는 context.resolve_sources). 금지 대역은 rules.json params에서 읽는다.
"""

import ipaddress

from app.schemas import Violation

from ._format import violation
from .context import RequestContext, rule_spec

RULE_ID = "CHG-02"


def check(ctx: RequestContext) -> Violation | None:
    banned = {ipaddress.ip_network(s) for s in rule_spec(ctx.data, RULE_ID).params["banned_sources"]}
    hits = [n for n in ctx.sources.networks if n in banned]
    if not hits:
        return None
    full = ", ".join(str(n) for n in hits)
    if ctx.sources.assumed and ctx.sources.raw and ctx.sources.raw.strip():
        why = f"출발지('{ctx.sources.raw}')가 범위를 특정하지 않아 {full}으로 보며"
    elif ctx.sources.assumed:
        why = f"출발지가 적혀 있지 않아 {full}으로 보며"
    else:
        why = f"출발지({full})가 전체 대역이라"
    return violation(ctx.data, RULE_ID, f"{why} 방화벽 전체 대역 오픈 금지에 걸린다.")
