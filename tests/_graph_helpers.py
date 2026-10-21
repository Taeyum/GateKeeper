"""승인 그래프와 API 테스트가 함께 쓰는 fake LLM 응답 준비 (test_graph_v2.py와 같은 방식).

병렬로 도는 서브에이전트가 같은 main 큐를 함께 쓰므로 응답은 구조화 출력 스키마별 큐(set_fake_responses_for)로 넣는다.
dev 라벨만 쓴다(CLAUDE.md 절대 규칙 1).
"""

from langgraph.checkpoint.serde import jsonplus

from app import rules_engine
from app.agent.nodes.classify import RouteGuess
from app.agent.nodes.decide import DecideDraft
from app.agent.nodes.history_agent import HistoryNote
from app.agent.nodes.impact_agent import ImpactNote
from app.agent.nodes.policy_agent import PolicyNotes, RuleNoteItem, rule_ids_of
from app.agent.nodes.validate import ValidationCheck
from app.data_loader import load_requests
from app.llm.fake import set_fake_responses_for
from app.schemas import ChangeRequest, LabeledRequest

# 체크포인트의 직렬화 허용 목록에 빠진 모델이 있으면 LangGraph가 이 문구 중 하나로 경고한다(타입마다 프로세스에서 한 번만).
SERDE_WARNING_MARKERS = ("unregistered type", "Blocked deserialization", "Could not reconstruct")


def reset_serde_warnings(monkeypatch) -> None:
    """다른 테스트가 먼저 경고를 냈어도 이 테스트가 볼 수 있게 한 번만 내는 기록을 비운다."""
    for name in ("_warned_unregistered_types", "_warned_blocked_types", "_warned_unreconstructable_types"):
        monkeypatch.setattr(jsonplus, name, set())


def serde_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if any(marker in r.getMessage() for marker in SERDE_WARNING_MARKERS)]


OK = ValidationCheck(consistent=True, issues=[])
BAD = ValidationCheck(consistent=False, issues=["설명이 판정과 다르다"])


def gold(request_id: str) -> LabeledRequest:
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def label(request_id: str, **overrides) -> ChangeRequest:
    """dev 정답 구조화. LLM이 정답을 낸다고 가정하고 엔진과 그래프의 배선만 본다."""
    data = gold(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


def draft(explanation: str = "설명") -> DecideDraft:
    return DecideDraft(explanation=explanation, conditions=["조건을 충족한다"], alternative_start=None, alternative_end=None)


def queue(
    request_id: str,
    *,
    drafts: list | None = None,
    checks: list | None = None,
    history: list | None = None,
    small_change_type: str | None = None,
) -> None:
    """한 요청이 거치는 LLM 호출에 줄 응답을 스키마별로 넣는다. 기본값은 라벨 구조화와 일관된 응답이다.

    small_change_type을 주면 작은 모델(classify)이 그 유형이라고 답한다. 표준 변경인데 "deploy"라고 답하게 하면
    분류 불일치(리뷰 K3)를 만들 수 있다.
    """
    request = label(request_id)
    check = rules_engine.check_all(request, gold(request_id).submitted_at)
    standard = rules_engine.is_standard(request).is_standard
    guess = small_change_type or ("standard" if standard else request.change_type)
    set_fake_responses_for("main", ChangeRequest, [request])
    set_fake_responses_for("small", RouteGuess, [RouteGuess(change_type=guess, is_emergency=request.is_emergency)])
    set_fake_responses_for(
        "main", PolicyNotes,
        [PolicyNotes(items=[RuleNoteItem(rule_id=i, note=f"{i} 설명") for i in rule_ids_of(check)], summary="정책 요약")],
    )  # fmt: skip
    set_fake_responses_for("main", ImpactNote, [ImpactNote(summary="영향도 요약")])
    set_fake_responses_for("main", HistoryNote, history if history is not None else [HistoryNote(summary="이력 요약")])
    set_fake_responses_for("main", DecideDraft, drafts or [draft()] * 4)
    set_fake_responses_for("main", ValidationCheck, checks or [OK] * 4)
