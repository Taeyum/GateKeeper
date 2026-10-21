"""감사 로그 (JSON Lines). 기록이 한 줄씩 남고, 줄이 섞이지 않고, 비밀값과 본문이 새지 않는지 본다."""

import asyncio
import json
from types import SimpleNamespace

from app.audit import AuditLog, forbidden_record, record_of
from app.clock import now_iso
from app.schemas import Recommendation, Violation

RECOMMENDATION = Recommendation(
    violations=[
        Violation(rule_id="CHG-01", severity="forbidden", evidence="금요일 저녁이라 걸린다"),
        Violation(rule_id="CHG-09", severity="fixable", evidence="허용 기한이 없다"),
    ],
    verdict="반려",
    risk="높음",
    explanation="이 설명 문장은 감사 로그에 넣지 않는다",
    citations=["CHG-01", "CHG-09"],
)


def state(**overrides) -> dict:
    base = {
        "trace_id": "0f2b6c0e",
        "request_id": "REQ-0001",
        "parent_request_id": "REQ-0000",
        "requester": "김운영",
        "masked_text": "본문 원문은 감사 로그에 넣지 않는다",
        "route": "general",
        "standard_change_id": None,
        "recommendation": RECOMMENDATION,
        "outcome": "rejected",
        "approval": SimpleNamespace(decision="reject", approver="박CAB", comment="동결 기간이라 반려합니다.", at="2026-10-29T16:20:00+09:00"),
    }
    return {**base, **overrides}


def read_lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_시각은_KST_ISO_초_단위다():
    assert now_iso().endswith("+09:00") and "." not in now_iso()


def test_사람_결정_기록에는_필요한_값만_있고_본문과_설명은_없다():
    record = record_of(state())
    assert record == {
        "trace_id": "0f2b6c0e",
        "request_id": "REQ-0001",
        "parent_request_id": "REQ-0000",
        "requester": "김운영",
        "route": "general",
        "standard_change_id": None,
        "verdict": "반려",
        "risk": "높음",
        "violations": ["CHG-01", "CHG-09"],  # 조항 번호만, 근거 문장은 넣지 않는다
        "citations": ["CHG-01", "CHG-09"],
        "outcome": "rejected",
        "decision": "reject",
        "approver": "박CAB",
        "comment": "동결 기간이라 반려합니다.",
        "post_report_required": False,
    }
    dumped = json.dumps(record, ensure_ascii=False)
    assert "원문" not in dumped and "설명 문장" not in dumped


def test_승인자_의견의_비밀값은_기록_직전에도_가린다():
    approval = SimpleNamespace(decision="approve", approver="박CAB", comment="임시로 password=hunter2 로 열었음", at="x")
    record = record_of(state(approval=approval, outcome="approved"))
    assert "hunter2" not in json.dumps(record, ensure_ascii=False)
    assert record["comment"] == "임시로 password=**** 로 열었음"


def test_자동_승인과_권고_전_중단은_빈_값을_None이나_빈_목록으로_둔다():
    auto = record_of(state(approval=None, outcome="auto_approved", route="standard", standard_change_id="STD-01"))
    assert (auto["decision"], auto["approver"], auto["comment"]) == (None, None, None)
    assert (auto["outcome"], auto["standard_change_id"]) == ("auto_approved", "STD-01")

    early = record_of({"request_id": "REQ-0002", "outcome": "manual_review"})  # decide 전에 멈춘 요청
    assert early["verdict"] is None and early["risk"] is None
    assert early["violations"] == [] and early["citations"] == [] and early["post_report_required"] is False
    assert early["outcome"] == "manual_review"


def test_긴급_유효_변경은_사후_보고_대상으로_표시한다():
    urgent = RECOMMENDATION.model_copy(update={"post_report_required": True})
    assert record_of(state(recommendation=urgent))["post_report_required"] is True


def test_권한_없는_호출_기록에는_쿼리와_본문이_없다():
    assert forbidden_record(role="requester", name="김운영", method="POST", path="/api/requests/REQ-0001/decision") == {
        "role": "requester",
        "name": "김운영",
        "method": "POST",
        "path": "/api/requests/REQ-0001/decision",
    }


async def test_한_줄씩_덧붙이고_한글을_그대로_쓴다(tmp_path):
    path = tmp_path / "아직 없는 폴더" / "audit.jsonl"  # 폴더가 없으면 만든다
    log = AuditLog(path)
    first = await log.append("decision", record_of(state()))
    await log.append("forbidden", forbidden_record(role="requester", name="김운영", method="POST", path="/api/x"))

    assert first["event"] == "decision" and first["ts"].endswith("+09:00")
    lines = read_lines(path)
    assert [line["event"] for line in lines] == ["decision", "forbidden"]
    assert lines[0]["approver"] == "박CAB" and lines[0]["request_id"] == "REQ-0001"
    assert "박CAB" in path.read_text(encoding="utf-8")  # \uXXXX 이스케이프가 아니다


async def test_다시_열어도_이어서_쓴다(tmp_path):
    path = tmp_path / "audit.jsonl"
    await AuditLog(path).append("auto_approved", {"request_id": "REQ-0001"})
    await AuditLog(path).append("manual_review", {"request_id": "REQ-0002"})  # 서버 재시작과 같다
    assert [line["request_id"] for line in read_lines(path)] == ["REQ-0001", "REQ-0002"]


async def test_동시에_써도_줄이_섞이지_않는다(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    await asyncio.gather(*(log.append("decision", {"request_id": f"REQ-{i:04d}", "pad": "가" * 2000}) for i in range(60)))
    lines = read_lines(path)  # 한 줄이라도 섞이면 JSON 파싱이 실패한다
    assert len(lines) == 60
    assert {line["request_id"] for line in lines} == {f"REQ-{i:04d}" for i in range(60)}
