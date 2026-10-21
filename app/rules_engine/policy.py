"""check_all: 요청 하나를 모든 조항과 대조한다.

순서: 표준 확정 → 긴급 유효성 → 정규화(context) → 적용 조항 실행 → 면제 분리.
유효한 긴급 변경이면 emergency_exempt 조항(CHG-01, 05, 10)의 위반을 violations가 아니라 exempted로
옮긴다. 근거 문장은 그대로 두어 설명이 "면제 적용"을 근거와 함께 쓸 수 있게 한다.
"""

from datetime import datetime

from app.schemas import ChangeRequest, GKData, PolicyCheckResult, Violation

from .context import build_request_context
from .emergency import emergency_valid
from .registry import ALL_RULES, applicable, out_of_scope_rules
from .standard import is_standard


def check_all(request: ChangeRequest, submitted_at: datetime, data: GKData) -> PolicyCheckResult:
    standard = is_standard(request, data)
    emergency = emergency_valid(request, data) if request.is_emergency else None
    ctx = build_request_context(request, submitted_at, data, standard_confirmed=standard.is_standard)
    exempt = emergency is not None and emergency.valid
    skipped = out_of_scope_rules(ctx)

    violations: list[Violation] = []
    exempted: list[Violation] = []
    for spec in data.rules.rules:
        check = ALL_RULES.get(spec.rule_id)
        if check is None or spec.rule_id in skipped or not applicable(spec.rule_id, ctx):
            continue
        found = check(ctx)
        if found is None:
            continue
        (exempted if exempt and spec.emergency_exempt else violations).append(found)

    return PolicyCheckResult(
        violations=violations,
        exempted=exempted,
        emergency_valid=emergency.valid if emergency is not None else None,
        standard_change_id=standard.standard_change_id,
    )
