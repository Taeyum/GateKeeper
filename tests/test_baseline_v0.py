"""v0 기준선 노드와 build_graph 테스트 (fake LLM)."""

import json

import pytest

from app import data_loader
from app.agent.graph import build_graph
from app.agent.nodes.baseline_v0 import V0Output, build_messages
from app.agent.prompting import format_submitted_at, prompt_hashes
from app.agent.state import add_counters
from app.data_loader import load_history, load_policy_text, load_requests
from app.llm.fake import set_fake_responses
from app.llm.usage import UsageCollector
from app.schemas import ChangeRequest, Recommendation


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


def _state(index: int = 0) -> dict:
    item = load_requests("dev")[index]
    return {
        "request_id": item.request_id,
        "submitted_at": item.submitted_at.isoformat(),
        "requester": item.requester,
        "masked_text": item.text,
    }


def _output(verdict: str = "반려") -> V0Output:
    return V0Output(
        structured=ChangeRequest(target="payments-db", change_type="firewall"),
        recommendation=Recommendation(verdict=verdict, explanation="테스트"),
    )


def test_시스템_프롬프트에_규정과_세_데이터가_모두_들어간다():
    system = build_messages(_state())[0].content
    assert load_policy_text().strip() in system
    assert "payments-db" in system and "Tier-1" in system  # CMDB
    assert "FRZ-01" in system  # 변경 캘린더 동결 기간
    assert "INC-2026-0412" in system  # 장애 티켓
    # 자리표시자가 남지 않고, 종료 시각이 없을 때의 기본 작업 시간이 rules.json 값으로 채워진다
    assert "{work_duration_minutes}" not in system and "60분 뒤" in system


def test_이력은_프롬프트에_넣지_않는다():
    """이력은 v2의 RAG가 더하는 정보다 (SPEC 4장)."""
    system = build_messages(_state())[0].content
    assert "HIS-" not in system
    for item in load_history()[:20]:
        assert item.title not in system


def test_사용자_메시지에_요일_붙은_요청_시각과_마스킹_본문이_들어간다():
    state = _state(0)
    user = build_messages(state)[1].content
    assert "2026-10-29 16:00 (목) Asia/Seoul" in user
    assert state["requester"] in user and state["masked_text"] in user


def test_시스템_메시지는_요청과_무관하게_같다():
    """같은 접두사를 모델 쪽 캐시가 재사용하려면 요청마다 달라지면 안 된다."""
    assert build_messages(_state(0))[0].content == build_messages(_state(5))[0].content


def test_요청_시각_표기():
    assert format_submitted_at("2026-10-31T21:10:00+09:00") == "2026-10-31 21:10 (토) Asia/Seoul"
    assert format_submitted_at("2026-10-31T12:10:00+00:00") == "2026-10-31 21:10 (토) Asia/Seoul"


async def test_v0_그래프는_한_번의_호출로_구조화와_권고를_낸다(fake_env):
    set_fake_responses("main", [_output()])
    collector = UsageCollector()
    result = await build_graph("v0").ainvoke(_state(), config={"callbacks": [collector]})
    assert result["structured"].target == "payments-db"
    assert result["recommendation"].verdict == "반려"
    assert result["metrics"] == {"parse_retries": 0}
    assert collector.snapshot().llm_calls == 1


async def test_구조화_출력이_틀리면_1회_재시도한다(fake_env):
    set_fake_responses("main", [{"structured": {}}, _output("승인")])
    collector = UsageCollector()
    result = await build_graph("v0").ainvoke(_state(), config={"callbacks": [collector]})
    assert result["recommendation"].verdict == "승인"
    assert result["metrics"] == {"parse_retries": 1}
    assert collector.snapshot().llm_calls == 2


async def test_두_번_틀리면_예외를_올린다(fake_env):
    set_fake_responses("main", [{"structured": {}}, {"structured": {}}])
    with pytest.raises(ValueError):
        await build_graph("v0").ainvoke(_state())


def test_v1과_v2는_MCP_클라이언트가_필요하고_모르는_버전은_거절한다():
    for version in ("v1", "v2"):
        with pytest.raises(ValueError, match="MCP 클라이언트"):
            build_graph(version)
    with pytest.raises(ValueError, match="알 수 없는 버전"):
        build_graph("v9")  # type: ignore[arg-type]


def test_버전을_안_주면_GK_VERSION을_따른다(monkeypatch):
    monkeypatch.setenv("GK_VERSION", "v2")
    data_loader.clear_caches()
    with pytest.raises(ValueError, match="v2 그래프는 MCP 클라이언트"):
        build_graph()
    monkeypatch.setenv("GK_VERSION", "v0")
    data_loader.clear_caches()
    assert build_graph() is not None


def test_프롬프트_해시는_버전이_쓰는_파일마다_나온다():
    hashes = prompt_hashes("v0")
    assert set(hashes) == {"baseline_v0.md", "structure_rules.md"}
    assert all(len(value) == 64 for value in hashes.values())
    v1 = prompt_hashes("v1")
    assert set(v1) == {"parse_request.md", "structure_rules.md", "classify.md", "policy_agent.md", "impact_agent.md", "decide.md"}
    assert v1["structure_rules.md"] == hashes["structure_rules.md"]  # v0과 v1이 구조화에 같은 지침을 받는다
    with pytest.raises(NotImplementedError):
        prompt_hashes("v2")


def test_metrics_reducer는_키별로_더한다():
    assert add_counters({"a": 1}, {"a": 2, "b": 1}) == {"a": 3, "b": 1}
    assert add_counters(None, {"a": 1}) == {"a": 1}
    assert add_counters({"a": 1}, None) == {"a": 1}


def test_dev_요청_10건이_모두_프롬프트로_만들어진다():
    for index in range(len(load_requests("dev"))):
        messages = build_messages(_state(index))
        assert len(messages) == 2 and json.dumps(messages[1].content)
