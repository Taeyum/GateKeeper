"""표준 변경(STD-01/02) 확정 (change_policy.md 3장).

LLM이 붙인 standard_change_id는 "어느 표준 항목을 주장하는지"를 가리키는 후보로만 쓰고, 확정은
사실값으로 한다. 후보 표시(standard_change_id 또는 change_type=standard)와 사실값 조건이 모두 맞아야
표준 변경이다. 값이 null이면 표준 변경으로 보지 않는다 (fail-safe: 일반 검토로 보낸다).

- "로그 레벨만", "같은 도메인"은 ChangeRequest에 필드가 없어 후보 표시로 갈음한다.
- 시작 시각이 없으면 동결(CHG-10)과 동시간 변경(CHG-06)을 확인할 수 없어 표준으로 확정하지 않는다.
"""

from collections.abc import Callable
from datetime import date

from app.schemas import ChangeRequest, GKData, StandardChangeSpec, StandardCheck

from ._format import fmt_dt
from .context import rules_tz, to_tz

StdChecker = Callable[[ChangeRequest, StandardChangeSpec, GKData], tuple[bool, str]]


def _std01(request: ChangeRequest, spec: StandardChangeSpec, data: GKData) -> tuple[bool, str]:
    """STD-01 로그 레벨 변경: 재시작이 없고(False) 함께 바꾸는 다른 설정이 없다([])."""
    if request.restart_required is None:
        return False, "재시작 여부가 적혀 있지 않아 STD-01(재시작 없는 로그 레벨 변경)로 확정하지 않는다."
    if request.restart_required:
        return False, "재시작이 필요해 STD-01이 아니라 일반 변경(배포)이다."
    if request.extra_changes is None:
        return False, "다른 설정을 함께 바꾸는지 적혀 있지 않아 STD-01(로그 레벨만 변경)로 확정하지 않는다."
    if request.extra_changes:
        extra = ", ".join(request.extra_changes)
        return False, f"로그 레벨 외 다른 설정({extra})도 함께 바꿔 STD-01이 아니라 일반 변경(배포)이다."
    return True, f"재시작 없이 로그 레벨만 바꿔 {spec.std_id}({spec.title})에 해당한다."


def _std02(request: ChangeRequest, spec: StandardChangeSpec, data: GKData) -> tuple[bool, str]:
    """STD-02 인증서 갱신: 작업일 기준 만료까지 남은 날이 0일 이상 max_days_before_expiry일 이하."""
    if request.cert_expires_at is None:
        return False, "현재 인증서 만료일이 없어 STD-02(만료 30일 이내 교체)로 확정하지 않는다."
    max_days = spec.params["max_days_before_expiry"]
    work_day: date = to_tz(request.start, rules_tz(data)).date()
    left = (request.cert_expires_at - work_day).days
    expires = f"{request.cert_expires_at.month}/{request.cert_expires_at.day}"
    if left < 0:
        return False, f"인증서가 작업일보다 먼저 만료돼(만료일 {expires}) 만료 전 교체인 STD-02가 아니다."
    if left > max_days:
        return False, f"작업일 기준 만료(만료일 {expires})까지 {left}일 남아 STD-02의 {max_days}일 이내 조건을 넘는다."
    return True, (
        f"작업일({fmt_dt(request.start)}) 기준 만료(만료일 {expires})까지 {left}일 남아 "
        f"{spec.std_id}({spec.title}, 만료 {max_days}일 이내 교체)에 해당한다."
    )


# rules.json standard_changes의 id마다 조건을 확인하는 함수. 목록에 있는데 함수가 없으면 표준이 아니다.
CHECKERS: dict[str, StdChecker] = {"STD-01": _std01, "STD-02": _std02}


def is_standard(request: ChangeRequest, data: GKData) -> StandardCheck:
    listed = {s.std_id: s for s in data.rules.standard_changes}
    if request.standard_change_id is not None:
        candidates = [request.standard_change_id]
    elif request.change_type == "standard":
        candidates = list(listed)
    else:
        return StandardCheck(is_standard=False, reason="표준 변경 후보 표시(standard_change_id 또는 유형 standard)가 없다.")
    if request.start is None:
        return StandardCheck(
            is_standard=False,
            reason="작업 시각이 없어 동결 기간과 동시간 변경을 확인할 수 없으므로 표준 변경으로 확정하지 않는다.",
        )
    reasons = []
    for std_id in candidates:
        spec, checker = listed.get(std_id), CHECKERS.get(std_id)
        if spec is None or checker is None:
            reasons.append(f"표준 변경 목록에 없는 항목({std_id})이다.")
            continue
        ok, reason = checker(request, spec, data)
        if ok:
            return StandardCheck(is_standard=True, standard_change_id=spec.std_id, reason=reason)
        reasons.append(reason)
    return StandardCheck(is_standard=False, reason=" ".join(reasons))
