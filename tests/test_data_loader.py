import json
from pathlib import Path

import pytest

from app import data_loader
from app.config import get_settings
from app.data_loader import (
    clear_caches,
    load_change_calendar,
    load_cmdb,
    load_gk_data,
    load_history,
    load_open_incidents,
    load_policy_text,
    load_requests,
    load_rules,
)
from app.schemas import KST


def test_CMDB():
    cmdb = load_cmdb()
    assert len(cmdb.services) == 16 and len(cmdb.partners) == 3
    assert cmdb.get_service("payments-db").ports == [5432]
    assert cmdb.get_service("없는-서비스") is None
    assert cmdb.internal_cidr == "10.0.0.0/8"


def test_변경_캘린더():
    cal = load_change_calendar()
    assert len(cal.freeze_periods) == 2 and len(cal.scheduled_changes) == 10
    assert cal.freeze_periods[1].scope == ["*"]
    assert cal.period.start < cal.period.end


def test_장애_티켓():
    incidents = load_open_incidents()
    assert len(incidents.incidents) == 3
    assert incidents.get("INC-2026-0412").status == "open"
    assert incidents.get("INC-2026-0398").status == "closed"
    assert incidents.get("INC-0000-0000") is None


def test_이력_100건과_장애_상세():
    history = load_history()
    assert isinstance(history, tuple) and len(history) == 100
    with_incident = [h for h in history if h.outcome == "장애"]
    assert len(with_incident) == 15
    assert all(h.incident is not None for h in with_incident)


def test_규정_메타데이터():
    rules = load_rules()
    assert [r.rule_id for r in rules.rules] == [f"CHG-{i:02d}" for i in range(1, 11)]
    assert [s.std_id for s in rules.standard_changes] == ["STD-01", "STD-02"]
    assert rules.get_rule("CHG-07").severity == "procedure"
    assert rules.verdict_by_severity["forbidden"] == "반려"
    # schemas.KST는 rules.json의 시간대와 같은 값이어야 한다.
    assert rules.defaults.timezone == KST.key


def test_규정_원문():
    text = load_policy_text()
    assert "### CHG-01" in text and "### CHG-10" in text


def test_GK_Data는_로더_결과를_묶고_캐시된다():
    data = load_gk_data()
    assert data is load_gk_data()
    assert data.cmdb is load_cmdb() and data.rules is load_rules()
    assert len(data.history) == 100 and data.history == list(load_history())


def test_dev_정답셋():
    requests = load_requests("dev")
    assert [r.request_id for r in requests] == [f"REQ-D{i:02d}" for i in range(1, 11)]
    assert requests[0].expected.verdict == "반려"
    assert requests[0].expected.violations == ["CHG-01", "CHG-02", "CHG-03", "CHG-09", "CHG-10"]


def test_알_수_없는_split은_거부한다():
    with pytest.raises(ValueError):
        load_requests("test")


def test_eval은_허용하지_않으면_파일을_열기_전에_막는다(monkeypatch):
    """eval 파일은 1단계 코드와 테스트가 열지 않는다. 경로 접근을 감시해서 확인한다."""
    opened: list[str] = []
    real_read_text = Path.read_text

    def spy(self, *args, **kwargs):
        opened.append(self.name)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    with pytest.raises(PermissionError):
        load_requests("eval")
    assert opened == []  # 어떤 파일도 열지 않았다
    load_requests("dev")
    assert opened == ["requests_dev.json"]


def test_allow_eval이면_eval_split을_읽는다(monkeypatch, tmp_path):
    """실제 eval 파일 대신 dev 내용을 eval로 바꾼 임시 파일로 허용 경로만 확인한다."""
    dev = json.loads((get_settings().data_dir / "eval/requests_dev.json").read_text(encoding="utf-8"))
    for item in dev:
        item["split"] = "eval"
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval/requests_eval.json").write_text(json.dumps(dev, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GK_DATA_DIR", str(tmp_path))
    clear_caches()
    assert len(load_requests("eval", allow_eval=True)) == 10


def test_파일의_split_값이_다르면_거부한다(monkeypatch, tmp_path):
    dev = json.loads((get_settings().data_dir / "eval/requests_dev.json").read_text(encoding="utf-8"))
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval/requests_eval.json").write_text(json.dumps(dev, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GK_DATA_DIR", str(tmp_path))
    clear_caches()
    with pytest.raises(ValueError, match="다른 split"):
        load_requests("eval", allow_eval=True)


def test_GK_DATA_DIR로_데이터_위치를_바꾼다(monkeypatch, tmp_path):
    monkeypatch.setenv("GK_DATA_DIR", str(tmp_path))
    clear_caches()
    with pytest.raises(FileNotFoundError):
        data_loader.load_cmdb()
