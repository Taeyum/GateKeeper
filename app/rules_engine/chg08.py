"""CHG-08 테스트 증적 (보완).

운영 반영 전 스테이징 테스트 결과를 첨부하거나 테스트 완료를 적는다. "사후 첨부", "테스트 예정"은
증적 없음이다 (구조화 단계에서 has_test_evidence = False). True가 아니면 위반이다.
"""

from app.schemas import Violation

from ._format import CHANGE_TYPE_KO, violation
from .context import RequestContext

RULE_ID = "CHG-08"


def check(ctx: RequestContext) -> Violation | None:
    if ctx.request.has_test_evidence is True:
        return None
    return violation(
        ctx.data,
        RULE_ID,
        f"대상({ctx.target})의 {CHANGE_TYPE_KO[ctx.change_type]}인데 운영 반영 전 스테이징 테스트 증적이 없다.",
    )
