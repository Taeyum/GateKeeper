"""상태 스냅샷을 API 응답 모양으로 바꾸는 순수 함수 (docs/API_CONTRACT.md)와 alternative_diff."""

from datetime import datetime

import pytest

from app.agent.results import AlternativePlan, ApprovalRecord, SubagentResult
from app.api import views
from app.api.alternative import SOURCE_SUGGESTION, alternative_diff, fields_to_fix
from app.api.store import NodeRun, RequestRecord
from app.data_loader import load_rules
from app.schemas import KST, ChangeRequest, Recommendation, Violation

RULES = load_rules()
START = datetime(2026, 10, 30, 20, 0, tzinfo=KST)
ALT_START = datetime(2026, 11, 3, 14, 0, tzinfo=KST)

REQUEST = ChangeRequest(target="payments-db", change_type="firewall", start=START, end=datetime(2026, 10, 30, 21, 0, tzinfo=KST))
REJECT = Recommendation(
    violations=[
        Violation(rule_id="CHG-01", severity="forbidden", evidence="금요일 저녁"),
        Violation(rule_id="CHG-02", severity="forbidden", evidence="전체 대역"),
        Violation(rule_id="CHG-09", severity="fixable", evidence="기한 없음"),
    ],
    verdict="반려",
    risk="높음",
    explanation="설명",
    citations=["CHG-01", "CHG-02", "CHG-09"],
)
SERVICE = {
    "id": "payments-db", "name": "결제 DB (PostgreSQL)", "tier": "Tier-1", "owner_team": "DBA팀",
    "depends_on": [], "dependents": ["batch-settlement", "payments-service"], "ports": [5432],
}  # fmt: skip
SCHEDULED = {
    "schedule_id": "SCH-01", "target": "payments-db", "change_type": "db_change", "title": "정기 점검",
    "start": "2026-10-30T20:00:00+09:00", "end": "2026-10-30T21:00:00+09:00", "owner": "DBA팀", "extra": "버린다",
}  # fmt: skip


def record(**overrides) -> RequestRecord:
    base = {"request_id": "REQ-0188", "requester": "김운영", "submitted_at": "2026-10-29T16:00:00+09:00", "own_text": "이번 주 금요일에 열어 주세요"}
    return RequestRecord(**{**base, **overrides})


def full_values(**overrides) -> dict:
    base = {
        "structured": REQUEST,
        "route": "general",
        "recommendation": REJECT,
        "alternative": AlternativePlan(start=ALT_START, end=datetime(2026, 11, 3, 15, 0, tzinfo=KST)),
        "impact": SubagentResult(tool_output={"service": SERVICE, "calendar": {"scheduled_changes": [SCHEDULED]}}, summary="요약"),
        "similar_incidents": [{"history_id": "HIS-049"}],
        "has_similar_incident": True,
        "standard_change_id": None,
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# 요약과 상세
# ---------------------------------------------------------------------------


def test_요약은_계약의_RequestSummary_키_그대로다():
    item = views.summary(record(status="awaiting_approval"), full_values())
    assert set(item) == {
        "request_id", "parent_request_id", "requester", "submitted_at", "target", "change_type", "start",
        "status", "verdict", "risk", "has_similar_incident", "standard_change_id", "decided_at",
    }  # fmt: skip
    assert (item["target"], item["change_type"], item["start"]) == ("payments-db", "firewall", "2026-10-30T20:00:00+09:00")
    assert (item["status"], item["verdict"], item["risk"], item["has_similar_incident"]) == ("awaiting_approval", "반려", "높음", True)


def test_분석_전_요약은_target_change_type_verdict가_null이다():
    item = views.summary(record(), {})
    assert (item["target"], item["change_type"], item["start"], item["verdict"], item["risk"], item["decided_at"]) == (None,) * 6
    assert item["status"] == "reviewing" and item["has_similar_incident"] is False


def test_자동_승인_요약은_표준_변경_번호와_결정_시각이_있다():
    values = {"structured": REQUEST, "recommendation": Recommendation(verdict="자동 승인"), "standard_change_id": "STD-01", "decided_at": "2026-10-29T16:00:09+09:00"}
    item = views.summary(record(status="auto_approved"), values)
    assert (item["standard_change_id"], item["decided_at"], item["risk"]) == ("STD-01", "2026-10-29T16:00:09+09:00", None)


def test_상세는_계약의_키를_모두_갖고_text는_이번에_새로_쓴_본문이다():
    parent = record(request_id="REQ-0187", own_text="이전 본문")
    child = record(parent_request_id=parent.request_id, own_text="출발지는 B페이먼츠입니다")
    child.nodes["parse_request"] = NodeRun("parse_request", "done", "2026-10-29T16:00:01+09:00", "2026-10-29T16:00:04+09:00")
    values = full_values(masked_text=views.compose_text(parent.own_text, child.own_text))

    body = views.detail(child, values, version="v2", rules=RULES)
    assert set(body) == {
        "request_id", "parent_request_id", "status", "version", "requester", "submitted_at", "text", "route", "nodes",
        "structured", "recommendation", "alternative_diff", "impact", "similar_incidents", "has_similar_incident", "approval", "errors",
    }  # fmt: skip
    assert body["text"] == "출발지는 B페이먼츠입니다" and "이전 본문" not in body["text"]  # 합친 문장은 그래프 입력에만 쓴다
    assert body["parent_request_id"] == "REQ-0187" and body["version"] == "v2" and body["approval"] is None
    assert body["nodes"] == [{"node": "parse_request", "status": "done", "started_at": "2026-10-29T16:00:01+09:00", "ended_at": "2026-10-29T16:00:04+09:00", "error": None}]
    assert body["structured"]["start"] == "2026-10-30T20:00:00+09:00" and body["recommendation"]["verdict"] == "반려"
    assert body["similar_incidents"] == [{"history_id": "HIS-049"}]


def test_값이_없는_필드는_키를_빼지_않고_null이나_빈_값으로_보낸다():
    body = views.detail(record(), {}, version="v1", rules=RULES)
    assert body["structured"] is None and body["recommendation"] is None and body["alternative_diff"] is None
    assert body["impact"] is None and body["route"] is None and body["approval"] is None
    assert body["similar_incidents"] == [] and body["has_similar_incident"] is False and body["errors"] == [] and body["nodes"] == []


def test_결정_뒤_상세에는_승인_기록이_있다():
    approval = ApprovalRecord(decision="reject", approver="박CAB", comment="반려합니다", at="2026-10-29T16:20:00+09:00")
    body = views.detail(record(status="rejected"), full_values(approval=approval), version="v2", rules=RULES)
    assert body["approval"] == {"decision": "reject", "approver": "박CAB", "comment": "반려합니다", "at": "2026-10-29T16:20:00+09:00"}


def test_재요청_본문은_부모_본문_뒤에_새_내용을_붙인다():
    assert views.compose_text(None, "새 본문") == "새 본문"
    composed = views.compose_text("이전 본문", "새 본문")
    assert composed.index("이전 본문") < composed.index("새 본문") and "이전 요청과 다르면 이번 내용이 맞다" in composed


# ---------------------------------------------------------------------------
# 영향도, 오류, 종료 이벤트, 정렬
# ---------------------------------------------------------------------------


def test_impact는_서비스와_같은_시간대_예정_변경을_계약의_키만_골라_담는다():
    impact = views.impact_view(full_values())
    assert set(impact) == {"id", "name", "tier", "owner_team", "depends_on", "dependents", "same_time_changes"}
    assert impact["dependents"] == ["batch-settlement", "payments-service"]
    assert impact["same_time_changes"] == [{k: v for k, v in SCHEDULED.items() if k != "extra"}]  # 계약에 없는 키는 버린다


def test_impact가_없거나_확인_불가면_null이다():
    assert views.impact_view({}) is None  # 표준 변경 경로
    assert views.impact_view({"impact": SubagentResult(status="unavailable", summary="영향도: 확인 불가")}) is None
    no_calendar = {"impact": SubagentResult(tool_output={"service": SERVICE, "calendar": None})}
    assert views.impact_view(no_calendar)["same_time_changes"] == []  # 작업 시각이 없어 캘린더를 안 본 경우


def test_errors는_예외_타입이_아니라_사용자용_확인_불가_문장이다():
    values = {
        "impact": SubagentResult(status="unavailable", summary="영향도: 확인 불가"),
        "history": SubagentResult(status="unavailable", summary="이력: 확인 불가"),
        "policy": SubagentResult(summary="정상"),
        "errors": ["impact_agent: 조회 실패 (MCPTimeoutError)"],
    }
    assert views.unavailable_errors(values) == ["영향도: 확인 불가", "이력: 확인 불가"]


def test_종료_이벤트는_상태별로_하나다():
    waiting = views.terminal_event(record(status="awaiting_approval"), full_values())
    assert waiting[0] == "awaiting_approval" and set(waiting[1]) == {"request_id", "status", "at"}

    approval = ApprovalRecord(decision="approve", approver="박CAB", comment="", at="2026-10-29T16:20:00+09:00")
    decided = views.terminal_event(record(status="approved"), full_values(approval=approval, decided_at=approval.at))
    assert decided[0] == "done" and decided[1]["status"] == "approved" and decided[1]["approval"]["approver"] == "박CAB"
    assert decided[1]["at"] == approval.at and decided[1]["message"] is None

    review = views.terminal_event(record(status="manual_review", message="수동 검토로 넘겨요"), {})
    assert review[1]["status"] == "manual_review" and review[1]["message"] == "수동 검토로 넘겨요" and review[1]["approval"] is None


def test_결재함_정렬은_위험도_유사_장애_최신_순이다():
    def item(request_id, risk, similar, at):
        return {"request_id": request_id, "risk": risk, "has_similar_incident": similar, "submitted_at": at}

    items = [
        item("a", "낮음", True, "2026-10-29T10:00:00+09:00"),
        item("b", None, False, "2026-10-29T18:00:00+09:00"),
        item("c", "높음", False, "2026-10-29T09:00:00+09:00"),
        item("d", "높음", True, "2026-10-29T08:00:00+09:00"),
        item("e", "중간", False, "2026-10-29T12:00:00+09:00"),
        item("f", "높음", True, "2026-10-29T11:00:00+09:00"),
    ]
    assert [i["request_id"] for i in views.sort_approvals(items)] == ["f", "d", "c", "e", "a", "b"]  # 높음 중 유사 장애가 먼저, 그 안에서 최신순, null은 맨 뒤


# ---------------------------------------------------------------------------
# alternative_diff
# ---------------------------------------------------------------------------


def diff(rule_ids, *, verdict="반려", structured=REQUEST, plan=AlternativePlan(start=ALT_START)):
    return alternative_diff(verdict=verdict, rule_ids=rule_ids, structured=structured, plan=plan, rules=RULES)


def test_위반한_조항이_고칠_필드를_정하고_화면의_묶음_순서로_나온다():
    items = diff(["CHG-09", "CHG-02", "CHG-01", "CHG-10", "CHG-03"])
    assert [i["field"] for i in items] == ["start", "source", "expires_days"]  # 시간 조항 둘과 접속 조항 둘은 각각 한 항목
    assert items[0] == {"field": "start", "label": "작업 시간", "current": "2026-10-30T20:00:00+09:00", "suggested": "2026-11-03T14:00:00+09:00"}
    assert items[1] == {"field": "source", "label": "접속할 곳", "current": None, "suggested": SOURCE_SUGGESTION}


def test_허용_기한은_rules_json의_최대_일수를_제안한다():
    (item,) = diff(["CHG-09"])
    assert item["suggested"] == RULES.get_rule("CHG-09").params["max_days"] and item["current"] is None


def test_롤백과_테스트_증적은_적어_내라는_뜻의_불리언이다():
    items = diff(["CHG-08", "CHG-04"], structured=REQUEST.model_copy(update={"has_rollback": False}))
    assert items == [
        {"field": "has_rollback", "label": "되돌리는 방법", "current": False, "suggested": True},
        {"field": "has_test_evidence", "label": "테스트 결과", "current": None, "suggested": True},
    ]


def test_옮길_시각을_못_찾았으면_작업_시간_항목을_만들지_않는다():
    assert diff(["CHG-01"], plan=AlternativePlan()) is None
    assert diff(["CHG-01"], plan=None) is None
    assert [i["field"] for i in diff(["CHG-01", "CHG-09"], plan=AlternativePlan())] == ["expires_days"]


def test_반려나_조건부_승인이_아니거나_고칠_것이_없으면_null이다():
    assert diff(["CHG-01"], verdict="승인") is None and diff(["CHG-01"], verdict="자동 승인") is None
    assert diff([]) is None and diff(["CHG-07"]) is None  # 긴급 절차는 고칠 필드가 아니다
    assert diff(["CHG-01"], structured=None) is None
    assert diff(["CHG-01"], verdict="조건부 승인") is not None


@pytest.mark.parametrize(("rule_id", "expected"), [("CHG-05", ["start"]), ("CHG-06", ["start"]), ("CHG-03", ["source"]), ("STD-01", [])])
def test_조항_하나가_가리키는_필드(rule_id, expected):
    assert fields_to_fix([rule_id]) == expected
