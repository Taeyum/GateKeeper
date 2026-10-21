"""② classify 노드 테스트 (fake LLM). 엔진 확정값은 작은 모델의 의견과 무관하게 유지된다 (리뷰 K3)."""

import pytest

from app import data_loader, rules_engine
from app.agent.nodes import classify as node
from app.agent.nodes.classify import MISMATCH_WARNING, RouteGuess, build_messages, decide_route, disagrees
from app.data_loader import load_requests, load_rules
from app.llm.fake import set_fake_responses
from app.llm.usage import UsageCollector
from app.schemas import ChangeRequest


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


def label_of(request_id: str, **overrides) -> ChangeRequest:
    gold = next(r for r in load_requests("dev") if r.request_id == request_id)
    data = gold.expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


def state_of(request: ChangeRequest, **extra) -> dict:
    return {"structured": request, "masked_text": "요청 본문", **extra}


def guess(change_type: str, is_emergency: bool = False) -> RouteGuess:
    return RouteGuess(change_type=change_type, is_emergency=is_emergency)


async def run(request: ChangeRequest, responses: list, **extra) -> tuple[dict, UsageCollector]:
    set_fake_responses("small", responses)
    collector = UsageCollector()
    return await node.classify(state_of(request, **extra), {"callbacks": [collector]}), collector


# ---------------------------------------------------------------------------
# route와 엔진 확정값
# ---------------------------------------------------------------------------


async def test_표준_변경은_엔진이_확정하면_route가_standard다():
    result, _ = await run(label_of("REQ-D04"), [guess("standard")])  # STD-01
    assert result["route"] == "standard" and result["standard_change_id"] == "STD-01"
    assert result["emergency_valid"] is None and result["warnings"] == [] and result["errors"] == []
    assert result["metrics"] == {"route_disagreements": 0}


async def test_인증서_갱신도_표준이고_동결은_판정_단계에서_걸린다():
    result, _ = await run(label_of("REQ-D10"), [guess("standard")])  # STD-02, 정답 판정은 CHG-10 때문에 반려
    assert result["route"] == "standard" and result["standard_change_id"] == "STD-02"


async def test_유효한_긴급_변경은_route가_emergency다():
    result, _ = await run(label_of("REQ-D07"), [guess("deploy", True)])
    assert result["route"] == "emergency" and result["emergency_valid"] is True
    assert result["standard_change_id"] is None and result["warnings"] == []


async def test_티켓이_무효인_긴급_표시는_일반_변경이고_emergency_valid는_false다():
    request = label_of("REQ-D07", ticket="INC-2026-9999")
    result, _ = await run(request, [guess("deploy", True)])
    assert result["route"] == "general" and result["emergency_valid"] is False
    assert result["warnings"] == []  # 두 분류가 같으니 불일치는 아니다


async def test_일반_변경은_general이고_표준과_긴급_값은_비어_있다():
    result, _ = await run(label_of("REQ-D06"), [guess("deploy")])
    assert (result["route"], result["standard_change_id"], result["emergency_valid"]) == ("general", None, None)


async def test_표준_후보인데_작업_시각이_없으면_엔진이_확정하지_않아_general이다():
    request = label_of("REQ-D04", start=None, end=None)
    result, _ = await run(request, [guess("standard")])
    assert result["route"] == "general" and result["standard_change_id"] is None


async def test_유형만_다르고_표준과_긴급_여부가_같으면_불일치가_아니다():
    result, _ = await run(label_of("REQ-D06"), [guess("firewall")])  # 배포를 방화벽으로 봤지만 경로에는 영향이 없다
    assert result["route"] == "general" and result["warnings"] == []
    assert result["metrics"]["route_disagreements"] == 0


# ---------------------------------------------------------------------------
# 불일치와 실패: route만 general, 엔진 확정값은 그대로 (K3)
# ---------------------------------------------------------------------------


async def test_엔진은_표준_작은_모델은_배포면_route는_general이지만_표준_확정과_판정은_유지된다():
    result, _ = await run(label_of("REQ-D04"), [guess("deploy")])
    assert result["route"] == "general"
    assert result["standard_change_id"] == "STD-01"  # 엔진 확정값을 지우지 않는다
    assert rules_engine.verdict_from([], result["standard_change_id"] is not None) == "자동 승인"
    assert result["warnings"] == [MISMATCH_WARNING] and result["metrics"]["route_disagreements"] == 1


async def test_작은_모델이_긴급이_아니라고_해도_유효한_긴급_확정은_유지된다():
    result, _ = await run(label_of("REQ-D07"), [guess("deploy", False)])
    assert result["route"] == "general" and result["emergency_valid"] is True
    assert result["warnings"] == [MISMATCH_WARNING]


async def test_작은_모델이_표준이라고_했는데_요청은_일반이면_불일치다():
    result, _ = await run(label_of("REQ-D06"), [guess("standard")])
    assert result["route"] == "general" and result["warnings"] == [MISMATCH_WARNING]


async def test_작은_모델_호출이_실패하면_general로_돌리고_엔진_확정값은_그대로다():
    result, _ = await run(label_of("REQ-D04"), [])  # small 응답 큐가 비어 있어 호출이 예외를 낸다
    assert result["route"] == "general" and result["standard_change_id"] == "STD-01"
    assert result["warnings"] == []  # 실패는 불일치가 아니다
    assert result["errors"] == ["classify: 작은 모델 1차 분류 실패 (FakeResponsesExhausted)"]
    assert result["metrics"] == {"route_disagreements": 0, "partial_failures": 1}


async def test_작은_모델_응답이_검증에_실패해도_general로_돌리고_진행한다():
    result, _ = await run(label_of("REQ-D06"), [{"change_type": "배포"}])
    assert result["route"] == "general" and result["errors"][0].startswith("classify:")


# ---------------------------------------------------------------------------
# 순수 함수와 상태
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("is_standard", "emergency_is_valid", "flagged", "route"),
    [
        (True, False, False, "standard"),
        (True, True, False, "standard"),  # 표준이 먼저다 (정책 검사만 돈다)
        (False, True, False, "emergency"),
        (False, False, False, "general"),
        (True, False, True, "general"),
        (False, True, True, "general"),
    ],
)
def test_route_결정(is_standard, emergency_is_valid, flagged, route):
    assert decide_route(is_standard, emergency_is_valid, flagged) == route


def test_불일치_판단은_표준과_긴급_여부만_본다():
    deploy, standard = label_of("REQ-D06"), label_of("REQ-D04")
    assert not disagrees(guess("db_change"), deploy)
    assert disagrees(guess("standard"), deploy) and disagrees(guess("deploy"), standard)
    assert disagrees(guess("deploy", True), deploy)


async def test_작은_모델만_부르고_main은_부르지_않는다():
    set_fake_responses("main", [])  # main 큐가 비어 있다: 불렀다면 예외가 났을 것이다
    result, collector = await run(label_of("REQ-D06"), [guess("deploy")])
    assert collector.snapshot().llm_calls == 1 and result["route"] == "general"


def test_프롬프트는_표준_변경_목록을_rules_json에서_읽고_마스킹_본문만_쓴다():
    messages = build_messages(state_of(label_of("REQ-D06"), masked_text="마스킹된 본문", raw_text="password=hunter2"))
    system, user = messages[0].content, messages[1].content
    for spec in load_rules().standard_changes:
        assert f"{spec.std_id} {spec.title}" in system and spec.condition in system
    assert "{standard_changes}" not in system and "CHG-01" not in system  # 규정 조항은 주지 않는다
    assert "마스킹된 본문" in user and all("hunter2" not in str(m.content) for m in messages)

