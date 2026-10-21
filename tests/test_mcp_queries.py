from datetime import datetime

import pytest

from app.data_loader import load_gk_data
from app.mcp_server.queries import NotFound, calendar_overlap, incident_info, overlaps, service_info
from app.schemas import FreezePeriod


def kst(text: str) -> datetime:
    return datetime.fromisoformat(f"{text}+09:00")


@pytest.fixture
def data():
    return load_gk_data()


def test_반열린_구간_겹침():
    a, b, c = kst("2026-11-02T09:00"), kst("2026-11-02T10:00"), kst("2026-11-02T11:00")
    assert overlaps(a, c, b, c) and overlaps(b, c, a, c)
    assert not overlaps(a, b, b, c)  # 끝 시각에 딱 맞춰 시작하면 겹치지 않는다
    assert not overlaps(b, c, a, b)


def test_서비스_정보(data):
    info = service_info(data, "payments-db")
    assert (info.tier, info.owner_team, info.ports, info.default_port) == ("Tier-1", "DBA팀", [5432], 5432)
    assert info.dependents == ["batch-settlement", "payments-service"] and info.depends_on == []


def test_포트가_없는_서비스는_기본_포트가_null(data):
    info = service_info(data, "batch-settlement")
    assert info.ports == [] and info.default_port is None


def test_포트가_여러_개면_첫_번째가_기본_포트(data):
    assert service_info(data, "web-01").default_port == 443


def test_없는_서비스는_NotFound이고_등록된_서비스를_알려_준다(data):
    with pytest.raises(NotFound, match="payments-db"):
        service_info(data, "payment-db")


def test_동결_기간_겹침(data):
    result = calendar_overlap(data, "payments-db", kst("2026-10-30T20:00"), kst("2026-10-30T21:00"))
    assert [f.freeze_id for f in result.freeze_periods] == ["FRZ-01"]
    assert result.scheduled_changes == []


def test_동결_범위에_없는_서비스는_걸리지_않는다(data):
    result = calendar_overlap(data, "order-service", kst("2026-10-31T10:00"), kst("2026-10-31T11:00"))
    assert result.freeze_periods == []


def test_범위가_와일드카드인_동결은_모든_서비스에_적용된다(data):
    result = calendar_overlap(data, "order-service", kst("2026-11-07T10:00"), kst("2026-11-07T11:00"))
    assert [f.freeze_id for f in result.freeze_periods] == ["FRZ-02"]


def test_동결_경계는_반열린_구간이다(data):
    # FRZ-01은 10/30 00:00 ~ 11/02 09:00
    ends_at_start = calendar_overlap(data, "payments-db", kst("2026-10-29T23:00"), kst("2026-10-30T00:00"))
    starts_at_end = calendar_overlap(data, "payments-db", kst("2026-11-02T09:00"), kst("2026-11-02T10:00"))
    one_minute_in = calendar_overlap(data, "payments-db", kst("2026-10-29T23:00"), kst("2026-10-30T00:01"))
    assert ends_at_start.freeze_periods == [] and starts_at_end.freeze_periods == []
    assert [f.freeze_id for f in one_minute_in.freeze_periods] == ["FRZ-01"]


def test_예정_변경은_같은_대상만_겹침으로_본다(data):
    # SCH-101: payments-service 10/27 14:00~15:00
    hit = calendar_overlap(data, "payments-service", kst("2026-10-27T14:59"), kst("2026-10-27T15:30"))
    after = calendar_overlap(data, "payments-service", kst("2026-10-27T15:00"), kst("2026-10-27T16:00"))
    other = calendar_overlap(data, "payments-db", kst("2026-10-27T14:00"), kst("2026-10-27T15:00"))
    assert [s.schedule_id for s in hit.scheduled_changes] == ["SCH-101"]
    assert after.scheduled_changes == [] and other.scheduled_changes == []


def test_없는_대상은_겹침_없음이_아니라_오류다(data):
    with pytest.raises(NotFound):
        calendar_overlap(data, "payment-db", kst("2026-10-30T20:00"), kst("2026-10-30T21:00"))


def test_끝이_시작보다_늦지_않으면_거부한다(data):
    with pytest.raises(ValueError, match="end"):
        calendar_overlap(data, "payments-db", kst("2026-10-30T21:00"), kst("2026-10-30T21:00"))


def test_주입한_데이터를_쓴다(data):
    fake_freeze = FreezePeriod(
        freeze_id="FRZ-TEST", title="가짜", start=kst("2030-01-01T00:00"), end=kst("2030-01-02T00:00"), scope=["web-01"]
    )
    fake = data.model_copy(update={"calendar": data.calendar.model_copy(update={"freeze_periods": [fake_freeze]})})
    result = calendar_overlap(fake, "web-01", kst("2030-01-01T10:00"), kst("2030-01-01T11:00"))
    assert [f.freeze_id for f in result.freeze_periods] == ["FRZ-TEST"]
    # 원본 캐시는 그대로다
    assert calendar_overlap(data, "web-01", kst("2030-01-01T10:00"), kst("2030-01-01T11:00")).freeze_periods == []


def test_장애_티켓(data):
    open_ticket = incident_info(data, "INC-2026-0412")
    assert open_ticket.status == "open" and open_ticket.services == ["payments-service", "payments-db"]
    closed = incident_info(data, "INC-2026-0398")
    assert closed.status == "closed" and closed.closed_at is not None


def test_없는_티켓은_NotFound(data):
    with pytest.raises(NotFound, match="INC-0000-0000"):
        incident_info(data, "INC-0000-0000")
