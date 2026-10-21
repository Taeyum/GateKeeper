"""CHG-04 롤백 계획 필수 (보완).

배포와 DB 변경은 되돌리는 절차를 한 줄 이상 적는다. has_rollback이 True가 아니면(False 또는 null)
적혀 있지 않은 것으로 본다.
"""

from app.schemas import Violation

from ._format import CHANGE_TYPE_KO, violation
from .context import RequestContext

RULE_ID = "CHG-04"


def check(ctx: RequestContext) -> Violation | None:
    if ctx.request.has_rollback is True:
        return None
    kind = CHANGE_TYPE_KO[ctx.change_type]
    return violation(ctx.data, RULE_ID, f"대상({ctx.target})의 {kind}인데 롤백 계획이 적혀 있지 않다.")
