"""규칙 엔진 단위 테스트 (2단계).

조항마다 통과, 위반, 경계값을 직접 만든 케이스로 확인하고, 마지막에 dev 10건 라벨과 맞춰 본다.
eval split은 쓰지 않는다. 데이터 생성기는 tests/rules_fixtures.py에 있다.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app import rules_engine as engine
from app.data_loader import load_gk_data, load_requests
from app.rules_engine.registry import ALL_RULES, BODY_RULES, WINDOW_RULES
from app.schemas import Violation
from tests.rules_fixtures import (
    SUBMITTED,
    TUE_14,
    at,
    exempted_ids,
    freeze,
    ids,
    incident,
    make_data,
    quiet_data,
    req,
    scheduled,
    std02,
)

H = timedelta(hours=1)
M = timedelta(minutes=1)


def check(request, submitted_at=SUBMITTED, data=None):
    return engine.check_all(request, submitted_at, data or quiet_data())


def window(start, end=None):
    return {"start": start, "end": end}


# ---------------------------------------------------------------------------
# 기본 요청은 모든 조항을 통과한다 (이후 테스트는 값 하나만 바꾼다)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("change_type", ["firewall", "deploy", "db_change", "standard"])
def test_기본_요청은_위반이_없다(change_type):
    result = check(req(change_type))
    assert result.violations == [] and result.exempted == []


# ---------------------------------------------------------------------------
# CHG-01 배포 금지 시간대 (금 18:00 ~ 월 09:00)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start, end",
    [
        (at(10, 27, 14), at(10, 27, 15)),  # 화요일
        (at(10, 30, 16), at(10, 30, 17)),  # 규정 예시: 금요일 16:00~17:00 통과
        (at(10, 30, 17), at(10, 30, 18)),  # SPEC: 금요일 17:00~18:00 통과 (반열린 구간)
        (at(10, 30, 17, 59), at(10, 30, 18)),  # 경계: 금 17:59~18:00 통과
        (at(11, 2, 9), at(11, 2, 10)),  # 월요일 09:00 시작 통과
    ],
)
def test_CHG01_통과(start, end):
    assert "CHG-01" not in ids(check(req("deploy", **window(start, end))))


@pytest.mark.parametrize(
    "start, end",
    [
        (at(10, 30, 17, 59), at(10, 30, 18, 1)),  # 경계: 18:00을 1분 넘김
        (at(10, 30, 18), None),  # 금 18:00 시작, 종료 없음 → 1시간
        (at(10, 30, 20), None),  # 규정 예시: 금요일 20:00 시작은 위반
        (at(10, 31, 10), at(10, 31, 11)),  # 토요일
        (at(11, 2, 8, 59), at(11, 2, 9)),  # 경계: 월 08:59~09:00
        (at(10, 29, 23), at(10, 30, 19)),  # 목요일 밤부터 금요일 저녁까지 이어지는 작업
    ],
)
def test_CHG01_위반(start, end):
    result = check(req("deploy", **window(start, end)))
    assert ids(result) == ["CHG-01"]
    assert "금요일 18:00~월요일 09:00" in result.violations[0].evidence


def test_CHG01_주말_range는_범위_전체를_작업_구간으로_본다():
    """규정 예시: "주말에"처럼 날짜만 있고 시각이 없어도 주말 전체가 금지 시간대라 위반이다."""
    weekend = req("deploy", start=at(10, 31), end=at(11, 2), time_precision="range")
    assert ids(check(weekend)) == ["CHG-01"]


def test_CHG01_종료_시각이_없으면_근거에_1시간_가정을_밝힌다():
    result = check(req("deploy", start=at(10, 30, 20), end=None))
    assert "종료 시각이 없어 1시간으로 봄" in result.violations[0].evidence


def test_CHG01_표준_변경에는_적용하지_않는다():
    assert ids(check(req("standard", start=at(10, 31, 10), end=at(10, 31, 11)))) == []


def test_CHG01_금지_시간대는_rules_json에서_읽는다():
    """금지 시간대를 토 00:00 시작으로 바꾸면 금요일 20:00이 통과한다 (하드코딩 아님)."""
    data = quiet_data(rule_params={"CHG-01": {"blocked_from": "SAT 00:00"}})
    assert "CHG-01" not in ids(check(req("deploy", start=at(10, 30, 20)), data=data))
    assert "CHG-01" in ids(check(req("deploy", start=at(10, 31, 1)), data=data))


def test_시작_시각이_없으면_시간_조항을_검사하지_않는다():
    result = check(req("db_change", start=None, end=None, has_rollback=False))
    assert ids(result) == ["CHG-04"]


def test_종료가_시작보다_이르면_ValueError():
    with pytest.raises(ValueError, match="종료"):
        check(req("deploy", start=TUE_14, end=TUE_14))


# ---------------------------------------------------------------------------
# CHG-02 전체 대역 오픈 금지
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", [None, "", "0.0.0.0/0", "::/0", "외부 전체", "10.1.2.3/32, 0.0.0.0/0"])
def test_CHG02_위반(source):
    result = check(req("firewall", port=8443, target="admin-portal", source=source))
    assert "CHG-02" in ids(result)
    assert result.violations[0].severity == "forbidden"


def test_CHG02_출발지가_없으면_0_0_0_0_0으로_본다고_근거에_쓴다():
    result = check(req("firewall", source=None))
    evidence = next(v.evidence for v in result.violations if v.rule_id == "CHG-02")
    assert evidence.startswith("출발지가 적혀 있지 않아 0.0.0.0/0으로 보며")


@pytest.mark.parametrize("source", ["10.30.2.15/32", "203.0.113.10/32", "198.51.100.0/24"])
def test_CHG02_통과(source):
    assert "CHG-02" not in ids(check(req("firewall", port=8443, target="admin-portal", source=source)))


def test_CHG02_방화벽이_아니면_적용하지_않는다():
    assert ids(check(req("deploy", source=None))) == []


def test_CHG02_긴급_변경도_면제되지_않는다():
    data = quiet_data(incidents=[incident("INC-2026-9001", ["payments-db"])])
    request = req("firewall", source="0.0.0.0/0", is_emergency=True, ticket="INC-2026-9001")
    result = check(request, data=data)
    assert result.emergency_valid is True
    assert "CHG-02" in ids(result) and "CHG-03" in ids(result)


@pytest.mark.parametrize("target", ["web-01", "web-02"])
@pytest.mark.parametrize("port", [443, None])
def test_CHG02_범위_밖_공개_웹_443은_방화벽_전용_조항을_제외한다(target, port):
    """change_policy.md CHG-02 "범위 밖: 외부 공개 웹(web-01, web-02)의 443 포트는 로드밸런서에서 관리하며
    이 규정의 방화벽 변경 대상이 아니다."

    그래서 web-01/web-02의 443 방화벽 요청은 CHG-02, 03, 09를 건너뛴다(사용자 결정). 포트가 없으면 CMDB
    기본 포트(443)로 보므로 같다. 시간 조항은 그대로 적용해서 금요일 20:00이면 CHG-01이 남는다.
    """
    request = req("firewall", target=target, port=port, source="0.0.0.0/0", expires_days=None)
    assert ids(check(request)) == []
    assert ids(check(request.model_copy(update={"start": at(10, 30, 20), "end": None}))) == ["CHG-01"]


@pytest.mark.parametrize("target, port", [("web-01", 8443), ("admin-portal", 443)])
def test_CHG02_범위_밖은_공개_웹의_443만이다(target, port):
    request = req("firewall", target=target, port=port, source="0.0.0.0/0", expires_days=None)
    assert ids(check(request)) == ["CHG-02", "CHG-09"]


# ---------------------------------------------------------------------------
# CHG-03 DB 포트 외부 노출 금지
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "203.0.113.10/32",  # 규정 예시: B페이먼츠 등록 IP 통과
        "203.0.113.10",  # 단일 IP는 /32
        "10.30.2.15",  # 규정 예시: 사내망이라 무관
        "10.0.0.0/8",
    ],
)
def test_CHG03_통과(source):
    assert "CHG-03" not in ids(check(req("firewall", source=source)))


@pytest.mark.parametrize(
    "source",
    [
        "203.0.113.0/24",  # 규정 예시: 등록 IP의 /24는 위반
        "203.0.113.11/32",  # 등록되지 않은 외부 /32
        "0.0.0.0/0",
        "10.30.2.15/32, 203.0.113.0/24",  # 여러 출발지 중 하나라도 걸리면 위반
    ],
)
def test_CHG03_위반(source):
    result = check(req("firewall", source=source))
    assert "CHG-03" in ids(result)


def test_CHG03_포트가_없으면_CMDB_기본_포트로_본다():
    """규정 CHG-03 참고: 요청에 포트가 없으면 대상 DB의 CMDB 기본 포트로 본다 (payments-db 5432)."""
    result = check(req("firewall", port=None, source="203.0.113.0/24"))
    chg03 = next(v for v in result.violations if v.rule_id == "CHG-03")
    assert "5432, CMDB 기본 포트" in chg03.evidence


def test_CHG03_MySQL_포트도_DB_포트다():
    assert "CHG-03" in ids(check(req("firewall", target="billing-db", port=3306, source="192.0.2.0/24")))


def test_CHG03_DB_포트가_아니면_적용하지_않는다():
    assert ids(check(req("firewall", target="admin-portal", port=8443, source="198.51.100.77/32"))) == []


def test_CHG03_DB_포트_목록은_rules_json에서_읽는다():
    data = quiet_data(rule_params={"CHG-03": {"db_ports": [3306]}})
    assert "CHG-03" not in ids(check(req("firewall", source="203.0.113.0/24"), data=data))


# ---------------------------------------------------------------------------
# CHG-04 롤백 계획 / CHG-08 테스트 증적
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("change_type", ["deploy", "db_change"])
@pytest.mark.parametrize("field, rule_id", [("has_rollback", "CHG-04"), ("has_test_evidence", "CHG-08")])
@pytest.mark.parametrize("value", [False, None])
def test_CHG04_CHG08_적혀_있지_않으면_위반(change_type, field, rule_id, value):
    result = check(req(change_type, **{field: value}))
    assert ids(result) == [rule_id]
    assert result.violations[0].severity == "fixable"


@pytest.mark.parametrize("change_type", ["deploy", "db_change"])
def test_CHG04_CHG08_적혀_있으면_통과(change_type):
    assert ids(check(req(change_type, has_rollback=True, has_test_evidence=True))) == []


def test_CHG04_CHG08_방화벽에는_적용하지_않는다():
    assert ids(check(req("firewall", has_rollback=None, has_test_evidence=None))) == []


# ---------------------------------------------------------------------------
# CHG-05 Tier-1 사전 공지 (24시간 전)
# ---------------------------------------------------------------------------


def test_CHG05_정확히_24시간_전은_통과():
    assert ids(check(req("db_change"), submitted_at=TUE_14 - 24 * H)) == []


def test_CHG05_24시간보다_1분_늦으면_위반():
    result = check(req("db_change"), submitted_at=TUE_14 - 24 * H + M)
    assert ids(result) == ["CHG-05"]
    assert "23시간 59분 전에" in result.violations[0].evidence


def test_CHG05_작업_시작_이후_요청도_위반():
    result = check(req("db_change"), submitted_at=TUE_14 + 2 * H)
    assert "이후에 요청해" in result.violations[0].evidence


@pytest.mark.parametrize("target", ["order-service", "admin-portal"])
def test_CHG05_Tier1이_아니면_적용하지_않는다(target):
    assert ids(check(req("deploy", target=target), submitted_at=TUE_14 - H)) == []


def test_CHG05_표준_변경에는_적용하지_않는다():
    assert ids(check(req("standard"), submitted_at=TUE_14 - H)) == []


def test_CHG05_기준_시간은_rules_json에서_읽는다():
    data = quiet_data(rule_params={"CHG-05": {"notice_hours": 48}})
    assert ids(check(req("db_change"), submitted_at=TUE_14 - 30 * H, data=data)) == ["CHG-05"]


# ---------------------------------------------------------------------------
# CHG-06 동시간 변경 금지
# ---------------------------------------------------------------------------


def test_CHG06_같은_대상의_예정_변경과_겹치면_위반():
    data = quiet_data(schedules=[scheduled("order-service", TUE_14 + 30 * M, TUE_14 + 90 * M)])
    result = check(req("deploy"), data=data)
    assert ids(result) == ["CHG-06"]
    assert "SCH-T1" in result.violations[0].evidence


@pytest.mark.parametrize(
    "s_start, s_end",
    [
        (TUE_14 + H, TUE_14 + 2 * H),  # 경계: 내 작업이 끝나는 시각에 시작
        (TUE_14 - H, TUE_14),  # 경계: 내 작업이 시작하는 시각에 끝남
    ],
)
def test_CHG06_끝과_시작이_맞닿으면_통과(s_start, s_end):
    assert ids(check(req("deploy"), data=quiet_data(schedules=[scheduled("order-service", s_start, s_end)]))) == []


def test_CHG06_다른_대상이면_통과():
    data = quiet_data(schedules=[scheduled("payments-service", TUE_14, TUE_14 + H)])
    assert ids(check(req("deploy"), data=data)) == []


def test_CHG06_표준_변경에도_적용한다():
    data = quiet_data(schedules=[scheduled("auth-service", TUE_14, TUE_14 + H)])
    assert ids(check(req("standard"), data=data)) == ["CHG-06"]


def test_CHG06_실제_캘린더_SCH101과_겹친다():
    request = req("deploy", target="payments-service", start=at(10, 27, 14, 30), end=None)
    assert ids(engine.check_all(request, SUBMITTED)) == ["CHG-06"]


# ---------------------------------------------------------------------------
# CHG-09 임시 오픈 만료일 (최대 30일)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("days, violated", [(1, False), (30, False), (31, True), (60, True)])
def test_CHG09_기한_30일은_통과_31일은_위반(days, violated):
    assert ("CHG-09" in ids(check(req("firewall", expires_days=days)))) is violated


def test_CHG09_외부_출발지인데_기한이_없으면_위반():
    result = check(req("firewall", expires_days=None))
    assert ids(result) == ["CHG-09"]
    assert "허용 기한이 없다" in result.violations[0].evidence


def test_CHG09_사내망_출발지는_기한이_없어도_통과():
    assert ids(check(req("firewall", source="10.30.2.15/32", expires_days=None))) == []


@pytest.mark.parametrize(
    "expires_at, violated",
    [
        (TUE_14 + timedelta(days=30), False),
        (TUE_14 + timedelta(days=30) + M, True),
        (TUE_14 + timedelta(days=31), True),
        (TUE_14 + H, False),  # 규정 예시: "작업 끝나면 바로 닫음"은 그 시각이 기한
    ],
)
def test_CHG09_expires_at만_있으면_작업_시작부터_계산한다(expires_at, violated):
    request = req("firewall", expires_days=None, expires_at=expires_at)
    assert ("CHG-09" in ids(check(request))) is violated


def test_CHG09_시작_시각도_없으면_요청_시각부터_계산한다():
    request = req("firewall", start=None, end=None, expires_days=None, expires_at=SUBMITTED + timedelta(days=31))
    assert ids(check(request)) == ["CHG-09"]


def test_CHG09_기한이_둘_다_있으면_더_긴_쪽으로_본다():
    request = req("firewall", expires_days=10, expires_at=TUE_14 + timedelta(days=40))
    assert ids(check(request)) == ["CHG-09"]


def test_CHG09_최대_기한은_rules_json에서_읽는다():
    data = quiet_data(rule_params={"CHG-09": {"max_days": 7}})
    assert ids(check(req("firewall", expires_days=14), data=data)) == ["CHG-09"]


# ---------------------------------------------------------------------------
# CHG-10 배포 동결 기간
# ---------------------------------------------------------------------------

FREEZE_START = at(10, 28, 0)
FREEZE_END = at(10, 29, 0)


@pytest.mark.parametrize(
    "start, end",
    [
        (FREEZE_START - H, FREEZE_START),  # 경계: 동결 시작 시각에 끝남
        (FREEZE_END, FREEZE_END + H),  # 경계: 동결 끝나는 시각에 시작
        (FREEZE_START - 2 * H, FREEZE_START - H),
    ],
)
def test_CHG10_통과(start, end):
    data = quiet_data(freezes=[freeze(FREEZE_START, FREEZE_END)])
    assert ids(check(req("deploy", start=start, end=end), data=data)) == []


@pytest.mark.parametrize(
    "start, end",
    [
        (FREEZE_START, FREEZE_START + H),  # 경계: 동결 시작 시각에 시작
        (FREEZE_START - M, FREEZE_START + 59 * M),  # 1분 겹침
        (FREEZE_END - M, FREEZE_END + H),
    ],
)
def test_CHG10_위반(start, end):
    data = quiet_data(freezes=[freeze(FREEZE_START, FREEZE_END)])
    result = check(req("deploy", start=start, end=end), data=data)
    assert ids(result) == ["CHG-10"]
    assert "FRZ-T1" in result.violations[0].evidence


def test_CHG10_범위_밖_서비스는_통과():
    data = quiet_data(freezes=[freeze(FREEZE_START, FREEZE_END, scope=["payments-db", "billing-db"])])
    assert ids(check(req("deploy", start=FREEZE_START), data=data)) == []
    assert ids(check(req("deploy", target="billing-db", start=FREEZE_START), data=data)) == ["CHG-10"]


def test_CHG10_scope_별표는_모든_서비스():
    data = quiet_data(freezes=[freeze(FREEZE_START, FREEZE_END, scope=["*"])])
    for target in ["order-service", "admin-portal", "search-service"]:
        assert ids(check(req("deploy", target=target, start=FREEZE_START), data=data)) == ["CHG-10"]


def test_CHG10_표준_변경에도_적용한다():
    data = quiet_data(freezes=[freeze(FREEZE_START, FREEZE_END)])
    assert ids(check(req("standard", start=FREEZE_START, end=None), data=data)) == ["CHG-10"]


def test_CHG10_실제_FRZ01_하루_전은_통과():
    """FRZ-01(10/30 00:00 시작) 하루 전 payments-db 작업은 동결과 무관하다."""
    request = req("db_change", target="payments-db", start=at(10, 29, 10), end=at(10, 29, 11))
    assert ids(engine.check_all(request, SUBMITTED)) == []


# ---------------------------------------------------------------------------
# CHG-07 긴급 변경 (emergency_valid와 면제)
# ---------------------------------------------------------------------------


def _emergency(ticket="INC-2026-0412", target="payments-service", **kw):
    return req("deploy", target=target, is_emergency=True, ticket=ticket, **kw)


def test_긴급_유효_열린_티켓이고_대상이_장애_범위에_있다():
    result = engine.emergency_valid(_emergency())
    assert result.valid is True and result.ticket == "INC-2026-0412"


@pytest.mark.parametrize(
    "request_kwargs, reason",
    [
        ({"ticket": "INC-2026-0398", "target": "search-service"}, "종료"),  # 종료된 티켓
        ({"target": "order-service"}, "대상 서비스"),  # 장애 범위 밖 대상
        ({"ticket": None}, "티켓 번호가 없다"),
        ({"ticket": "INC-2026-9999"}, "장애 목록에 없다"),
        ({"ticket": "장애-412"}, "형식"),
    ],
)
def test_긴급_무효(request_kwargs, reason):
    result = engine.emergency_valid(_emergency(**request_kwargs))
    assert result.valid is False and reason in result.reason


def test_긴급_표시가_없으면_무효():
    assert engine.emergency_valid(req("deploy", ticket="INC-2026-0412")).valid is False


def test_긴급_티켓_번호는_대소문자와_공백을_정리한다():
    assert engine.emergency_valid(_emergency(ticket=" inc-2026-0412 ")).valid is True


def test_유효한_긴급이면_CHG01_05_10을_면제하고_exempted에_남긴다():
    """D07과 같은 상황: 토요일 밤, 동결 기간, 20분 전 요청."""
    request = _emergency(start=at(10, 31, 21, 30), end=None)
    result = engine.check_all(request, at(10, 31, 21, 10))
    assert result.violations == []
    assert exempted_ids(result) == ["CHG-01", "CHG-05", "CHG-10"]
    assert result.emergency_valid is True


def test_유효한_긴급도_나머지_조항은_검사한다():
    request = _emergency(start=at(10, 31, 21, 30), has_rollback=False)
    result = engine.check_all(request, at(10, 31, 21, 10))
    assert ids(result) == ["CHG-04"]


def test_무효한_긴급은_면제하지_않고_위반이_아니다():
    """change_policy.md CHG-07 "티켓이 없거나, 종료됐거나, 대상이 다르면 일반 변경으로 처리한다. 이것 자체는 위반이 아니다." """
    request = _emergency(target="search-service", ticket="INC-2026-0398", start=at(10, 31, 21, 30))
    result = engine.check_all(request, at(10, 31, 21, 10))
    assert result.emergency_valid is False
    assert ids(result) == ["CHG-01"] and result.exempted == []


def test_긴급_표시가_없으면_check_all의_emergency_valid는_None():
    assert check(req("deploy")).emergency_valid is None


def test_면제_조항은_CHG07_exempts와_같다():
    rules = load_gk_data().rules
    flagged = [r.rule_id for r in rules.rules if r.emergency_exempt]
    assert flagged == rules.get_rule("CHG-07").params["exempts"] == ["CHG-01", "CHG-05", "CHG-10"]


# ---------------------------------------------------------------------------
# 표준 변경 (is_standard)
# ---------------------------------------------------------------------------


def test_STD01_재시작_없이_로그_레벨만():
    result = engine.is_standard(req("standard"))
    assert result.is_standard is True and result.standard_change_id == "STD-01"


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"restart_required": True}, "재시작이 필요"),
        ({"restart_required": None}, "재시작 여부"),
        ({"extra_changes": ["커넥션 타임아웃"]}, "커넥션 타임아웃"),
        ({"extra_changes": None}, "다른 설정"),
        ({"start": None, "end": None}, "작업 시각이 없어"),
    ],
)
def test_STD01_조건을_못_맞추면_표준이_아니다(overrides, reason):
    result = engine.is_standard(req("standard", **overrides))
    assert result.is_standard is False and result.standard_change_id is None
    assert reason in result.reason


@pytest.mark.parametrize("days_left, ok", [(0, True), (14, True), (30, True), (31, False), (-1, False)])
def test_STD02_작업일_기준_만료_30일_이내(days_left, ok):
    result = engine.is_standard(std02(cert_expires_at=TUE_14.date() + timedelta(days=days_left)))
    assert result.is_standard is ok
    assert result.standard_change_id == ("STD-02" if ok else None)


def test_STD02_만료일이_없으면_표준이_아니다():
    assert engine.is_standard(std02(cert_expires_at=None)).is_standard is False


def test_STD02_기한은_rules_json_params에서_읽는다():
    data = make_data(std_params={"STD-02": {"max_days_before_expiry": 10}})
    assert engine.is_standard(std02(), data).is_standard is False
    assert load_gk_data().rules.standard_changes[1].params == {"max_days_before_expiry": 30}


def test_후보_표시가_없으면_사실값이_맞아도_표준이_아니다():
    request = req("deploy", restart_required=False, extra_changes=[])
    assert engine.is_standard(request).is_standard is False


def test_유형만_standard면_사실값으로_STD_id를_찾는다():
    assert engine.is_standard(req("standard", standard_change_id=None)).standard_change_id == "STD-01"
    assert engine.is_standard(std02(standard_change_id=None)).standard_change_id == "STD-02"


def test_표준_후보였는데_조건을_못_맞추면_배포_조항으로_검사한다():
    """change_policy.md 3장 STD-01 "다른 설정을 함께 바꾸거나 재시작이 필요하면 일반 변경(배포)이다".

    배포가 되면 CHG-04, 08이 적용되고 Tier-1(auth-service) 4시간 전 요청이라 CHG-05도 걸린다.
    """
    result = check(req("standard", restart_required=True), submitted_at=TUE_14 - 4 * H)
    assert result.standard_change_id is None
    assert ids(result) == ["CHG-04", "CHG-05", "CHG-08"]
    assert engine.verdict_from(result.violations, is_standard=False) == "조건부 승인"


def test_표준_변경에는_CHG06_10만_적용한다():
    standard_rules = [r.rule_id for r in load_gk_data().rules.rules if "standard" in r.applies_to]
    assert standard_rules == ["CHG-06", "CHG-10"]


# ---------------------------------------------------------------------------
# 판정 (verdict_from)
# ---------------------------------------------------------------------------


def _v(rule_id, severity):
    return Violation(rule_id=rule_id, severity=severity, evidence="테스트 근거다.")


@pytest.mark.parametrize(
    "violations, is_standard, expected",
    [
        ([_v("CHG-01", "forbidden")], False, "반려"),
        ([_v("CHG-04", "fixable"), _v("CHG-02", "forbidden")], False, "반려"),
        ([_v("CHG-04", "fixable"), _v("CHG-08", "fixable")], False, "조건부 승인"),
        ([], False, "승인"),
        ([], True, "자동 승인"),
        ([_v("CHG-10", "forbidden")], True, "반려"),  # D10: 표준 변경인데 동결
        ([_v("CHG-06", "fixable")], True, "조건부 승인"),
    ],
)
def test_판정(violations, is_standard, expected):
    assert engine.verdict_from(violations, is_standard) == expected


def test_판정_대응은_rules_json_verdict_by_severity와_같다():
    table = load_gk_data().rules.verdict_by_severity
    assert engine.verdict_from([_v("X", "forbidden")], False) == table["forbidden"]
    assert engine.verdict_from([_v("X", "fixable")], False) == table["fixable"]
    assert engine.verdict_from([], False) == table["none_general"]
    assert engine.verdict_from([], True) == table["none_standard"]


# ---------------------------------------------------------------------------
# 위험도 (risk_from)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target, similar, expected",
    [
        ("payments-db", False, "높음"),  # Tier-1
        ("payments-db", True, "높음"),  # 최대는 높음
        ("order-service", False, "중간"),  # Tier-2
        ("billing-db", True, "높음"),  # Tier-2 + 유사 장애 (D03)
        ("admin-portal", False, "낮음"),  # Tier-3
        ("admin-portal", True, "중간"),
    ],
)
def test_위험도(target, similar, expected):
    assert engine.risk_from(target, "deploy", similar) == expected


def test_표준_변경은_위험도를_매기지_않는다():
    assert engine.risk_from("auth-service", "standard", True) is None


def test_CMDB에_없는_대상은_ValueError():
    with pytest.raises(ValueError, match="CMDB"):
        engine.risk_from("없는-서비스", "deploy", False)
    with pytest.raises(ValueError, match="CMDB"):
        engine.check_all(req("deploy", target="없는-서비스"), SUBMITTED)


# ---------------------------------------------------------------------------
# 대안 일정 (check_window, next_valid_window)
# ---------------------------------------------------------------------------


def test_check_window는_시간_조항만_본다():
    """D01 구간(금 20:00, FRZ-01): 본문 조항(02, 03, 09)은 없고 시간 조항만 나온다."""
    found = engine.check_window("payments-db", "firewall", at(10, 30, 20), at(10, 30, 21), at(10, 29, 16))
    assert ids(found) == ["CHG-01", "CHG-10"]


def test_check_window는_요청_시각으로_CHG05를_본다():
    found = engine.check_window("auth-db", "db_change", at(11, 5, 10), at(11, 5, 11), at(11, 4, 17))
    assert ids(found) == ["CHG-05"]


def test_check_window_표준_변경은_CHG06_10만_본다():
    sat = engine.check_window("web-02", "standard", at(10, 31, 9), at(10, 31, 10), SUBMITTED)
    in_freeze = engine.check_window("web-02", "standard", at(11, 7, 9), at(11, 7, 10), SUBMITTED)
    assert ids(sat) == [] and ids(in_freeze) == ["CHG-10"]


def test_check_window_잘못된_구간은_ValueError():
    with pytest.raises(ValueError):
        engine.check_window("order-service", "deploy", TUE_14, TUE_14 - H, SUBMITTED)


@pytest.mark.parametrize(
    "after, first",
    [
        (at(10, 27, 10), at(10, 27, 10)),  # 이미 30분 경계면 그대로
        (at(10, 27, 10, 30), at(10, 27, 10, 30)),
        (at(10, 27, 10, 10), at(10, 27, 10, 30)),  # 다음 30분 경계로 올림
        (at(10, 27, 10, 40), at(10, 27, 11)),
        (at(10, 27, 10) + timedelta(seconds=1), at(10, 27, 10, 30)),
    ],
)
def test_next_valid_window_첫_후보는_30분_경계로_올림(after, first):
    found = engine.next_valid_window("order-service", "deploy", H, after, SUBMITTED, quiet_data())
    assert found == (first, first + H)


def test_next_valid_window_금지_시간대를_건너뛴다():
    found = engine.next_valid_window("order-service", "deploy", H, at(10, 30, 17, 30), SUBMITTED, quiet_data())
    assert found == (at(11, 2, 9), at(11, 2, 10))


def test_next_valid_window_D08_동결_전_24시간_뒤를_찾는다():
    """D08 대안 방향 "11/05 17시 이후 동결 시작(11/06 00시) 전"과 같다. 정확히 24시간 뒤는 통과."""
    found = engine.next_valid_window("auth-db", "db_change", H, at(11, 5, 10), at(11, 4, 17))
    assert found == (at(11, 5, 17), at(11, 5, 18))


def test_next_valid_window_D01_동결과_주말이_끝난_뒤():
    found = engine.next_valid_window("payments-db", "firewall", H, at(10, 30, 20), at(10, 29, 16))
    assert found == (at(11, 2, 9), at(11, 2, 10))


def test_next_valid_window_D10_표준_변경은_동결만_피한다():
    found = engine.next_valid_window("web-02", "standard", H, at(11, 7, 9), at(11, 5, 10))
    assert found == (at(11, 9, 0), at(11, 9, 1))


def test_next_valid_window_14일_안에_없으면_None():
    data = quiet_data(freezes=[freeze(at(10, 1), at(12, 1))])
    assert engine.next_valid_window("order-service", "deploy", H, TUE_14, SUBMITTED, data) is None


def test_next_valid_window_결과는_check_window를_통과한다():
    start, end = engine.next_valid_window("payments-service", "deploy", 2 * H, at(10, 27, 13), SUBMITTED)
    assert engine.check_window("payments-service", "deploy", start, end, SUBMITTED) == []
    assert start == at(10, 27, 15)  # SCH-101(14:00~15:00)이 끝난 직후


def test_next_valid_window_작업_시간은_양수여야_한다():
    with pytest.raises(ValueError):
        engine.next_valid_window("order-service", "deploy", timedelta(0), TUE_14, SUBMITTED)


# ---------------------------------------------------------------------------
# 근거 문장과 데이터 정합성
# ---------------------------------------------------------------------------


def test_금지_보완_조항마다_검사_함수가_있다():
    rules = load_gk_data().rules.rules
    checkable = {r.rule_id for r in rules if r.severity in ("forbidden", "fixable")}
    assert set(ALL_RULES) == checkable
    assert set(WINDOW_RULES) == {"CHG-01", "CHG-05", "CHG-06", "CHG-10"}
    assert set(BODY_RULES) == {"CHG-02", "CHG-03", "CHG-04", "CHG-08", "CHG-09"}


def test_위반의_severity는_rules_json_조항_값이다():
    result = engine.check_all(load_requests("dev")[0].expected.structured, at(10, 29, 16))
    severities = {r.rule_id: r.severity for r in load_gk_data().rules.rules}
    assert all(v.severity == severities[v.rule_id] for v in result.violations)


def test_근거는_한국어_한_문장이고_가운뎃점을_쓰지_않는다():
    for item in load_requests("dev"):
        result = engine.check_all(item.expected.structured, item.submitted_at)
        for v in result.violations + result.exempted:
            assert v.evidence.endswith("다.") and v.evidence.count(". ") == 0
            assert "\u00b7" not in v.evidence  # 가운뎃점 (CLAUDE.md 절대 규칙 7)


def test_입력_시각에_시간대가_없으면_규정_시간대로_본다():
    naive = datetime(2026, 10, 30, 20, 0)
    assert ids(engine.check_window("order-service", "deploy", naive, naive + H, SUBMITTED)) == ["CHG-01"]


def test_UTC_입력도_규정_시간대로_바꿔_판단한다():
    """금 11:00 UTC는 금 20:00 KST라 위반이다."""
    utc = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
    assert ids(engine.check_window("order-service", "deploy", utc, utc + H, SUBMITTED)) == ["CHG-01"]


# ---------------------------------------------------------------------------
# dev 10건 라벨 일치 (expected.structured를 입력으로)
# ---------------------------------------------------------------------------

# 수집 시점에 파일을 읽지 않으려고 id만 두고 본문은 테스트 안에서 읽는다.
DEV_IDS = [f"REQ-D{i:02d}" for i in range(1, 11)]


def _dev(request_id):
    return next(r for r in load_requests("dev") if r.request_id == request_id)


@pytest.mark.parametrize("request_id", DEV_IDS)
def test_dev_라벨_위반과_판정이_같다(request_id):
    item = _dev(request_id)
    result = engine.check_all(item.expected.structured, item.submitted_at)
    verdict = engine.verdict_from(result.violations, result.standard_change_id is not None)
    assert ids(result) == item.expected.violations
    assert verdict == item.expected.verdict


@pytest.mark.parametrize("request_id", DEV_IDS)
def test_dev_라벨_긴급_표준_위험도도_같다(request_id):
    item = _dev(request_id)
    structured = item.expected.structured
    result = engine.check_all(structured, item.submitted_at)
    assert result.emergency_valid == item.expected.emergency_valid
    assert result.standard_change_id == structured.standard_change_id
    # 위험도: 유사 장애 여부는 라벨의 similar_history(5장 정의로 계산된 값)를 입력으로 쓴다.
    has_similar = bool(item.expected.similar_history)
    assert engine.risk_from(structured.target, structured.change_type, has_similar) == item.expected.risk


def test_dev_10건_라벨_일치_수():
    dev = load_requests("dev")
    assert [r.request_id for r in dev] == DEV_IDS
    matched = 0
    for item in dev:
        result = engine.check_all(item.expected.structured, item.submitted_at)
        verdict = engine.verdict_from(result.violations, result.standard_change_id is not None)
        matched += ids(result) == item.expected.violations and verdict == item.expected.verdict
    assert matched == 10


def test_dev_D07_면제_목록():
    item = _dev("REQ-D07")
    result = engine.check_all(item.expected.structured, item.submitted_at)
    assert exempted_ids(result) == ["CHG-01", "CHG-05", "CHG-10"]


def test_dev_라벨에_쓰인_인증서_만료일은_date다():
    """D10 cert_expires_at은 date로 읽혀 STD-02 계산(작업일과의 날짜 차)에 바로 쓰인다."""
    d10 = _dev("REQ-D10").expected.structured
    assert d10.cert_expires_at == date(2026, 11, 20)
