"""CHG-03 DB 포트 외부 노출 금지 (금지, 긴급 변경도 적용).

목적지 포트가 DB 포트(rules.json params db_ports)인 방화벽 요청은 사내망 출발지만 허용한다.
외부 출발지는 CMDB partners에 등록된 파트너 IP를 /32로 지정한 경우만 예외다.
포트가 없으면 대상의 CMDB 기본 포트로 본다 (정규화는 context.resolve_port).
"""

import ipaddress

from app.schemas import Violation

from ._format import violation
from .context import RequestContext, is_internal, rule_spec

RULE_ID = "CHG-03"


def check(ctx: RequestContext) -> Violation | None:
    db_ports = rule_spec(ctx.data, RULE_ID).params["db_ports"]
    if ctx.port is None or ctx.port not in db_ports:
        return None
    partners = {ipaddress.ip_network(p.ip) for p in ctx.data.cmdb.partners}
    # 등록 IP와 같은 /32만 예외다. 같은 대역이라도 /24처럼 넓히면 예외가 아니다.
    exposed = [n for n in ctx.sources.networks if not is_internal(n, ctx.data) and n not in partners]
    if not exposed:
        return None
    port = f"{ctx.port}, CMDB 기본 포트" if ctx.port_from_cmdb else str(ctx.port)
    sources = ", ".join(str(n) for n in exposed)
    return violation(
        ctx.data,
        RULE_ID,
        f"DB 포트({port})를 등록 파트너 /32가 아닌 외부 출발지({sources})에 열려고 한다.",
    )
