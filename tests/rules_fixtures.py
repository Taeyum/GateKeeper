"""규칙 엔진 경계값 테스트용 데이터와 요청 생성기.

tests/conftest.py는 로컬 3단계 세션과 같이 쓰는 파일이라 건드리지 않고, 규칙 엔진 테스트에서만 쓰는
생성기를 여기에 둔다. 실제 데이터(load_gk_data)를 model_copy로 복사해 캘린더, 장애 티켓, 규정 값을
바꾼 GKData를 만든다. 실제 데이터로는 만들 수 없는 경계(동결 시작 시각, 정확히 24시간 전)를 만들려는 것.

기본 요청은 모든 조항을 통과하게 만들어 두고, 테스트는 값 하나만 바꿔 그 조항만 보게 한다.
기준 주: 2026-10-26(월) ~ 11-01(일). 10/27은 화요일, 10/30은 금요일이다.
"""

from datetime import date, datetime, timedelta
from typing import Any

from app.data_loader import load_gk_data
from app.schemas import (
    KST,
    ChangeRequest,
    FreezePeriod,
    GKData,
    Incident,
    OpenIncidents,
    PolicyCheckResult,
    ScheduledChange,
)


def at(month: int, day: int, hour: int = 0, minute: int = 0, *, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


# 기본 작업 구간은 화요일 14:00~15:00, 요청은 일주일 전이라 CHG-05(24시간 전)를 늘 통과한다.
TUE_14 = at(10, 27, 14)
SUBMITTED = at(10, 20, 9)

_DEFAULTS: dict[str, dict[str, Any]] = {
    # payments-db(Tier-1, 5432) 등록 파트너 /32, 기한 7일
    "firewall": {
        "target": "payments-db",
        "port": 5432,
        "source": "203.0.113.10/32",
        "expires_days": 7,
    },
    # order-service(Tier-2)라 CHG-05와 무관하다
    "deploy": {"target": "order-service", "has_rollback": True, "has_test_evidence": True},
    "db_change": {"target": "auth-db", "has_rollback": True, "has_test_evidence": True},
    # STD-01 로그 레벨 변경 (auth-service는 Tier-1이지만 표준 변경에는 CHG-05가 적용되지 않는다)
    "standard": {
        "target": "auth-service",
        "standard_change_id": "STD-01",
        "restart_required": False,
        "extra_changes": [],
    },
}


def req(change_type: str = "deploy", **overrides: Any) -> ChangeRequest:
    """유형별로 모든 조항을 통과하는 요청에 overrides만 바꿔 넣는다.

    start만 바꾸고 end를 주지 않으면 end는 None이 되어 엔진이 기본 작업 시간(1시간)을 붙인다.
    """
    fields: dict[str, Any] = {"change_type": change_type, "start": TUE_14, "end": TUE_14 + timedelta(hours=1)}
    if "start" in overrides and "end" not in overrides:
        fields["end"] = None
    fields.update(_DEFAULTS[change_type])
    fields.update(overrides)
    return ChangeRequest(**fields)


def std02(**overrides: Any) -> ChangeRequest:
    """STD-02 인증서 갱신. 작업일(10/27) 기준 만료 14일 전."""
    fields: dict[str, Any] = {
        "target": "web-02",
        "standard_change_id": "STD-02",
        "cert_expires_at": date(2026, 11, 10),
        "restart_required": None,
        "extra_changes": None,
    }
    fields.update(overrides)
    return req("standard", **fields)


def freeze(start: datetime, end: datetime, scope: list[str] | None = None, freeze_id: str = "FRZ-T1") -> FreezePeriod:
    return FreezePeriod(freeze_id=freeze_id, title="테스트 동결", start=start, end=end, scope=scope or ["*"])


def scheduled(
    target: str, start: datetime, end: datetime, change_type: str = "deploy", schedule_id: str = "SCH-T1"
) -> ScheduledChange:
    return ScheduledChange(
        schedule_id=schedule_id, target=target, change_type=change_type, title="테스트 예정 변경",
        start=start, end=end, owner="테스트",
    )  # fmt: skip


def incident(ticket: str, services: list[str], status: str = "open") -> Incident:
    return Incident(
        ticket=ticket, title="테스트 장애", status=status, opened_at=at(10, 20),
        closed_at=at(10, 21) if status == "closed" else None, services=services,
    )  # fmt: skip


def make_data(
    *,
    freezes: list[FreezePeriod] | None = None,
    schedules: list[ScheduledChange] | None = None,
    incidents: list[Incident] | None = None,
    rule_params: dict[str, dict[str, Any]] | None = None,
    std_params: dict[str, dict[str, Any]] | None = None,
) -> GKData:
    """실제 데이터에서 지정한 부분만 바꾼 GKData. None이면 실제 값을 그대로 둔다.

    rule_params={"CHG-05": {"notice_hours": 48}}처럼 규정 값을 바꿔, 엔진이 값을 하드코딩하지 않고
    rules.json에서 읽는지 확인할 때도 쓴다.
    """
    base = load_gk_data()
    calendar = base.calendar.model_copy(
        update={
            "freeze_periods": base.calendar.freeze_periods if freezes is None else freezes,
            "scheduled_changes": base.calendar.scheduled_changes if schedules is None else schedules,
        }
    )
    rules = base.rules
    if rule_params:
        rules = rules.model_copy(
            update={
                "rules": [
                    r.model_copy(update={"params": {**r.params, **rule_params[r.rule_id]}})
                    if r.rule_id in rule_params
                    else r
                    for r in rules.rules
                ]
            }
        )
    if std_params:
        rules = rules.model_copy(
            update={
                "standard_changes": [
                    s.model_copy(update={"params": {**s.params, **std_params[s.std_id]}})
                    if s.std_id in std_params
                    else s
                    for s in rules.standard_changes
                ]
            }
        )
    return base.model_copy(
        update={
            "calendar": calendar,
            "incidents": base.incidents if incidents is None else OpenIncidents(incidents=incidents),
            "rules": rules,
        }
    )


def quiet_data(**kwargs: Any) -> GKData:
    """동결 기간, 예정 변경, 장애 티켓이 하나도 없는 데이터. 조항 하나만 보는 테스트의 기본값이다."""
    kwargs.setdefault("freezes", [])
    kwargs.setdefault("schedules", [])
    kwargs.setdefault("incidents", [])
    return make_data(**kwargs)


def ids(result: PolicyCheckResult | list) -> list[str]:
    violations = result.violations if isinstance(result, PolicyCheckResult) else result
    return [v.rule_id for v in violations]


def exempted_ids(result: PolicyCheckResult) -> list[str]:
    return [v.rule_id for v in result.exempted]
