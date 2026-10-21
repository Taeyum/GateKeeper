import json
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.schemas import (
    KST,
    ChangeRequest,
    EmergencyCheck,
    ExpectedLabel,
    GKData,
    LabeledRequest,
    LabelStructured,
    PolicyCheckResult,
    Recommendation,
    StandardCheck,
    Violation,
)


def _dev_raw() -> list[dict]:
    return json.loads((get_settings().data_dir / "eval/requests_dev.json").read_text(encoding="utf-8"))


def test_dev_라벨_10건이_모두_검증된다():
    raw = _dev_raw()
    assert len(raw) == 10
    for item in raw:
        label = LabeledRequest.model_validate(item)
        assert label.split == "dev"
        assert label.expected.structured.change_type in label.expected.structured.acceptable_change_types


def test_dev_라벨의_키가_하나도_버려지지_않는다():
    """라벨 모델은 extra를 무시하므로, 모델이 dev 라벨 키를 모두 가지고 있는지 따로 확인한다."""
    for item in _dev_raw():
        assert set(item) <= set(LabeledRequest.model_fields)
        assert set(item["expected"]) <= set(ExpectedLabel.model_fields)
        assert set(item["expected"]["structured"]) <= set(LabelStructured.model_fields)


def test_ChangeRequest는_라벨_전용_필드를_받지_않는다():
    """LLM 출력 스키마가 라벨 메타데이터로 오염되지 않는다."""
    assert "acceptable_change_types" not in ChangeRequest.model_fields
    assert "time_note" not in ChangeRequest.model_fields
    with pytest.raises(ValidationError):
        ChangeRequest(target="payments-db", change_type="firewall", acceptable_change_types=["firewall"])


def test_target과_change_type은_필수다():
    with pytest.raises(ValidationError):
        ChangeRequest(change_type="firewall")
    with pytest.raises(ValidationError):
        ChangeRequest(target="payments-db")
    with pytest.raises(ValidationError):
        ChangeRequest(target="payments-db", change_type="network")


def test_본문에_없는_값은_None이다():
    r = ChangeRequest(target="payments-db", change_type="firewall")
    assert r.is_emergency is False and r.time_precision == "exact"
    unset = [n for n, v in r.model_dump().items() if v is None]
    assert {"start", "end", "source", "port", "expires_days", "expires_at", "has_rollback",
            "has_test_evidence", "ticket", "restart_required", "extra_changes",
            "cert_expires_at", "standard_change_id", "reason"} == set(unset)  # fmt: skip


def test_시각은_KST로_정규화한다():
    utc = ChangeRequest(target="a", change_type="deploy", start="2026-10-30T11:00:00Z")
    assert utc.start == datetime.fromisoformat("2026-10-30T20:00:00+09:00")
    assert utc.start.tzinfo == KST
    naive = ChangeRequest(target="a", change_type="deploy", start="2026-10-30T20:00:00")
    assert naive.start.utcoffset().total_seconds() == 9 * 3600
    date_only = ChangeRequest(target="a", change_type="firewall", expires_at="2026-11-14")
    assert date_only.expires_at == datetime(2026, 11, 14, tzinfo=KST)


def test_인증서_만료일은_날짜다():
    r = ChangeRequest(target="web-02", change_type="standard", cert_expires_at="2026-11-20")
    assert r.cert_expires_at == date(2026, 11, 20)


def test_Recommendation_왕복():
    rec = Recommendation(
        violations=[Violation(rule_id="CHG-01", severity="forbidden", evidence="금요일 20시는 금지 시간대다.")],
        verdict="반려",
        risk="높음",
        similar_history=["HIS-049"],
        citations=["CHG-01"],
    )
    assert Recommendation.model_validate(rec.model_dump()) == rec
    assert Recommendation.model_validate_json(rec.model_dump_json()) == rec


@pytest.mark.parametrize("bad", [{"verdict": "보류"}, {"verdict": "승인", "risk": "매우 높음"}])
def test_Recommendation은_정해진_값만_받는다(bad):
    with pytest.raises(ValidationError):
        Recommendation(**bad)


def test_Violation_severity는_두_가지다():
    with pytest.raises(ValidationError):
        Violation(rule_id="CHG-01", severity="procedure", evidence="x")


def test_규칙_엔진_결과_모델의_기본값():
    assert PolicyCheckResult().violations == [] and PolicyCheckResult().emergency_valid is None
    assert StandardCheck(is_standard=False).standard_change_id is None
    assert EmergencyCheck(valid=False).ticket is None


def test_GKData는_바꿀_수_없고_복사로_교체한다():
    from app.data_loader import load_gk_data

    data = load_gk_data()
    assert isinstance(data, GKData)
    with pytest.raises(ValidationError):
        data.cmdb = None  # frozen
    # 가짜 티켓 목록을 넣은 복사본. 원본 캐시는 그대로여야 한다.
    fake = data.model_copy(update={"incidents": data.incidents.model_copy(update={"incidents": []})})
    assert fake.incidents.incidents == []
    assert len(data.incidents.incidents) == 3
