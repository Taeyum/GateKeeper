"""⑦ finalize: 감사 로그 한 줄 (docs/SPEC.md 3장)."""

import json
import logging

from app.agent.nodes.finalize import make_finalize
from app.agent.results import ApprovalRecord
from app.audit import AuditLog
from app.schemas import Recommendation, Violation

REJECT = Recommendation(
    violations=[Violation(rule_id="CHG-01", severity="forbidden", evidence="금요일 저녁이라 걸린다")],
    verdict="반려",
    risk="높음",
    citations=["CHG-01"],
)
APPROVAL = ApprovalRecord(decision="reject", approver="박CAB", comment="반려합니다", at="2026-10-29T16:20:00+09:00")


def lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def base_state(**overrides) -> dict:
    return {"request_id": "REQ-0001", "requester": "김운영", "route": "general", "recommendation": REJECT, "trace_id": "abc", **overrides}


async def test_사람의_결정은_decision_이벤트로_남는다(tmp_path):
    path = tmp_path / "audit.jsonl"
    update = await make_finalize(AuditLog(path))(base_state(approval=APPROVAL, outcome="rejected"))
    assert update == {}
    (record,) = lines(path)
    assert record["event"] == "decision"
    assert (record["request_id"], record["trace_id"], record["outcome"]) == ("REQ-0001", "abc", "rejected")
    assert (record["approver"], record["decision"], record["verdict"], record["violations"]) == ("박CAB", "reject", "반려", ["CHG-01"])


async def test_표준_변경의_자동_승인은_auto_approved_이벤트다(tmp_path):
    path = tmp_path / "audit.jsonl"
    auto = Recommendation(verdict="자동 승인", citations=["STD-01"])
    state = base_state(route="standard", standard_change_id="STD-01", recommendation=auto, approval=None, outcome="auto_approved")
    await make_finalize(AuditLog(path))(state)
    (record,) = lines(path)
    assert record["event"] == "auto_approved"
    assert (record["approver"], record["decision"]) == (None, None)
    assert record["standard_change_id"] == "STD-01"


async def test_긴급_유효_변경은_사후_보고_대상으로_표시한다(tmp_path):
    path = tmp_path / "audit.jsonl"
    urgent = REJECT.model_copy(update={"post_report_required": True})
    await make_finalize(AuditLog(path))(base_state(recommendation=urgent, approval=APPROVAL, outcome="approved"))
    assert lines(path)[0]["post_report_required"] is True


async def test_감사_로그를_못_써도_결정을_되돌리지_않고_errors에_남긴다(tmp_path, caplog):
    caplog.set_level(logging.ERROR)
    broken = AuditLog(tmp_path)  # 폴더를 파일처럼 열려 하므로 OSError가 난다
    update = await make_finalize(broken)(base_state(approval=APPROVAL, outcome="rejected"))
    assert len(update["errors"]) == 1 and update["errors"][0].startswith("finalize: 감사 로그 기록 실패")
    assert "outcome" not in update  # 결정은 건드리지 않는다
    assert any("감사 로그를 쓰지 못했다" in r.getMessage() for r in caplog.records)
