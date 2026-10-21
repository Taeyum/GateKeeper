"""v1 그래프 통합 테스트 (fake LLM, 메모리 내 MCP 서버, 실제 규칙 엔진).

병렬로 도는 두 서브에이전트가 같은 main 큐를 함께 쓰므로, 응답은 구조화 출력 스키마 이름별 큐
(set_fake_responses_for)로 넣어 꺼내는 순서에 기대지 않게 한다.
"""

import asyncio
import time

import pytest

from app import data_loader, rules_engine
from app.agent.errors import ReviewIncomplete
from app.agent.graph import build_graph, route_after_classify
from app.agent.mcp_client import MCPClient, MCPTimeoutError
from app.agent.nodes.classify import MISMATCH_WARNING, RouteGuess
from app.agent.nodes.decide import DecideDraft
from app.agent.nodes.impact_agent import ImpactNote
from app.agent.nodes.policy_agent import PolicyNotes, RuleNoteItem, rule_ids_of
from app.data_loader import load_requests, load_rules
from app.llm.fake import set_fake_responses, set_fake_responses_for
from app.llm.usage import UsageCollector
from app.mcp_server.server import build_server
from app.schemas import ChangeRequest
from eval.evaluators import self_contradiction
from eval.harness import initial_state


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


@pytest.fixture
async def mcp():
    async with MCPClient(build_server(), base_delay=0.01) as client:
        yield client


def gold(request_id: str):
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def label(request_id: str, **overrides) -> ChangeRequest:
    data = gold(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


def queue(
    request_id: str,
    *,
    parse: list | None = None,
    guess: RouteGuess | None = None,
    notes: PolicyNotes | None = None,
    impact: ImpactNote | None = None,
    drafts: list | None = None,
) -> None:
    """한 요청이 거치는 LLM 호출에 줄 응답을 스키마별로 넣는다. 기본값은 라벨 구조화와 일관된 응답이다."""
    request = label(request_id)
    structured = parse if parse is not None else [request]
    check = rules_engine.check_all(request, gold(request_id).submitted_at)
    standard = rules_engine.is_standard(request).is_standard
    set_fake_responses_for("main", ChangeRequest, structured)
    set_fake_responses_for(
        "small", RouteGuess, [guess or RouteGuess(change_type="standard" if standard else request.change_type, is_emergency=request.is_emergency)]
    )
    set_fake_responses_for(
        "main", PolicyNotes,
        [notes or PolicyNotes(items=[RuleNoteItem(rule_id=i, note=f"{i} 설명") for i in rule_ids_of(check)], summary="정책 요약")],
    )  # fmt: skip
    set_fake_responses_for("main", ImpactNote, [impact or ImpactNote(summary="영향도 요약")])
    set_fake_responses_for(
        "main", DecideDraft, drafts or [DecideDraft(explanation="설명", conditions=["조건을 충족한다"], alternative_start=None, alternative_end=None)] * 3
    )  # fmt: skip


async def run(graph, request_id: str):
    collector = UsageCollector()
    item = gold(request_id)
    result = await graph.ainvoke(
        initial_state(item, item.text), {"callbacks": [collector], "configurable": {"thread_id": f"test-{request_id}"}}
    )
    return result, collector.snapshot()


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------


def test_v1은_MCP_클라이언트가_필요하고_v2는_아직_없다(mcp):
    with pytest.raises(ValueError, match="MCP 클라이언트"):
        build_graph("v1")
    with pytest.raises(NotImplementedError):
        build_graph("v2", mcp=mcp)
    assert build_graph("v1", mcp=mcp) is not None


def test_버전을_안_주면_GK_VERSION을_따른다(mcp, monkeypatch):
    monkeypatch.setenv("GK_VERSION", "v1")
    data_loader.clear_caches()
    assert "decide" in build_graph(mcp=mcp).get_graph().nodes


def test_그래프_구성과_서브그래프_구조도(mcp):
    graph = build_graph("v1", mcp=mcp)
    assert set(graph.get_graph().nodes) == {"__start__", "parse_request", "classify", "policy_agent", "impact_agent", "decide", "__end__"}
    expanded = graph.get_graph(xray=True)
    assert {"policy_agent:check_policy", "policy_agent:read_rules", "policy_agent:write_notes"} <= set(expanded.nodes)
    assert {"impact_agent:lookup", "impact_agent:summarize"} <= set(expanded.nodes)
    mermaid = expanded.draw_mermaid()
    assert "subgraph policy_agent" in mermaid and "subgraph impact_agent" in mermaid


def test_표준_변경만_정책_검사로_가고_나머지는_두_서브에이전트가_병렬이다():
    assert route_after_classify({"route": "standard"}) == ["policy_agent"]
    for route in ("general", "emergency"):
        assert route_after_classify({"route": route}) == ["policy_agent", "impact_agent"]


# ---------------------------------------------------------------------------
# 전체 실행
# ---------------------------------------------------------------------------


async def test_dev_10건의_판정_위반_긴급_여부가_라벨과_모두_같다(mcp):
    graph, rules = build_graph("v1", mcp=mcp), load_rules()
    for item in load_requests("dev"):
        queue(item.request_id)
        result, _ = await run(graph, item.request_id)
        rec = result["recommendation"]
        assert rec.verdict == item.expected.verdict, item.request_id
        assert sorted(v.rule_id for v in rec.violations) == sorted(item.expected.violations), item.request_id
        assert rec.emergency_valid == item.expected.emergency_valid
        assert rec.post_report_required == item.expected.post_report_required
        assert result["structured"] == label(item.request_id)
        # 판정을 코드가 위반 목록에서 계산하므로 자기모순은 구조적으로 0이다
        assert self_contradiction(rec.verdict, sorted(v.rule_id for v in rec.violations), rules) is False


async def test_상태에_모든_단계의_결과가_남는다(mcp):
    queue("REQ-D01")
    result, _ = await run(build_graph("v1", mcp=mcp), "REQ-D01")
    assert result["route"] == "general" and result["standard_change_id"] is None and result["emergency_valid"] is None
    assert result["policy"].status == "ok" and result["policy"].notes
    assert result["impact"].status == "ok" and result["impact"].summary == "영향도 요약"
    assert result["alternative"].fallback is True and result["alternative"].start is not None  # 일정 대안을 못 냈다
    assert result["recommendation"].conditional_alternative.startswith("작업 시각을 다음으로 옮긴다")


async def test_LLM_호출은_노드마다_한_번씩이고_서브그래프_안의_호출도_집계된다(mcp):
    queue("REQ-D01", drafts=[DecideDraft(explanation="설명", conditions=[], alternative_start=None, alternative_end=None)] * 2)
    result, usage = await run(build_graph("v1", mcp=mcp), "REQ-D01")
    # parse 1 + classify(small) 1 + policy notes 1 + impact 1 + decide 2(일정을 못 내 재작성 1회)
    assert usage.llm_calls == 6
    assert result["metrics"]["alternative_regenerations"] == 1 and result["metrics"]["alternative_fallbacks"] == 1


async def test_표준_경로는_영향도_검사를_돌지_않고_LLM_호출도_세_번뿐이다(mcp, monkeypatch):
    calls = []
    original = mcp.get_service_info

    async def spy(*args, **kwargs):
        calls.append(args)
        return await original(*args, **kwargs)

    monkeypatch.setattr(mcp, "get_service_info", spy)
    queue("REQ-D04")
    result, usage = await run(build_graph("v1", mcp=mcp), "REQ-D04")
    assert result["route"] == "standard" and "impact" not in result and calls == []
    assert result["recommendation"].verdict == "자동 승인" and result["recommendation"].risk is None
    assert usage.llm_calls == 3  # parse, classify, decide (위반이 없어 policy 설명은 생략)


async def test_정책과_영향도_서브에이전트는_동시에_돈다(mcp, monkeypatch):
    """둘이 서로의 시작을 기다리게 한다. 순서대로 돌면 기다림이 시간 초과로 끝나 부분 실패로 드러난다."""
    policy_started, impact_started = asyncio.Event(), asyncio.Event()
    check_policy, service_info = mcp.check_policy, mcp.get_service_info

    async def slow_check(*args, **kwargs):
        policy_started.set()
        await asyncio.wait_for(impact_started.wait(), 2)
        return await check_policy(*args, **kwargs)

    async def slow_info(*args, **kwargs):
        impact_started.set()
        await asyncio.wait_for(policy_started.wait(), 2)
        return await service_info(*args, **kwargs)

    monkeypatch.setattr(mcp, "check_policy", slow_check)
    monkeypatch.setattr(mcp, "get_service_info", slow_info)
    queue("REQ-D06")
    started = time.perf_counter()
    result, _ = await run(build_graph("v1", mcp=mcp), "REQ-D06")
    assert time.perf_counter() - started < 1.5
    assert result["policy"].status == "ok" and result["impact"].status == "ok" and result["errors"] == []


async def test_병렬_서브에이전트의_metrics와_errors가_충돌_없이_합쳐진다(mcp):
    # 정책 설명과 영향도 요약이 모두 실패하는 상황. 두 가지가 같은 키(metrics, errors)를 동시에 쓴다.
    queue("REQ-D01")
    set_fake_responses_for("main", PolicyNotes, [])
    set_fake_responses_for("main", ImpactNote, [])
    result, _ = await run(build_graph("v1", mcp=mcp), "REQ-D01")
    assert result["metrics"]["partial_failures"] == 2
    assert sorted(e.split(":")[0] for e in result["errors"]) == ["impact_agent", "policy_agent"]
    assert result["recommendation"].verdict == "반려"  # 설명이 없어도 판정은 그대로다


# ---------------------------------------------------------------------------
# 리뷰 항목 end to end
# ---------------------------------------------------------------------------


async def test_작은_모델과_불일치해도_엔진_확정값과_판정은_유지되고_영향도는_돈다(mcp):  # 리뷰 K3
    queue("REQ-D04", guess=RouteGuess(change_type="deploy", is_emergency=False))
    result, _ = await run(build_graph("v1", mcp=mcp), "REQ-D04")
    rec = result["recommendation"]
    assert result["route"] == "general" and result["standard_change_id"] == "STD-01"
    assert rec.verdict == "자동 승인" and rec.citations == ["STD-01"]
    assert rec.warnings == [MISMATCH_WARNING] and result["impact"].status == "ok"
    assert result["metrics"]["route_disagreements"] == 1


async def test_작업_시각을_끝내_못_뽑으면_경고를_붙이고_자동_승인으로_끝내지_않는다(mcp):  # 리뷰 H1
    empty = label("REQ-D04", start=None, end=None)
    queue("REQ-D04", parse=[empty, empty])
    result, _ = await run(build_graph("v1", mcp=mcp), "REQ-D04")
    rec = result["recommendation"]
    assert result["metrics"]["required_field_retries"] == 1 and result["standard_change_id"] is None
    assert rec.verdict != "자동 승인" and any("작업 시각이 없어 시간 조항" in w for w in rec.warnings)
    assert "승인 전에 작업 시각을 확인해야 한다" in rec.explanation


async def test_재시도로_작업_시각을_찾으면_정상_판정이다(mcp):
    queue("REQ-D06", parse=[label("REQ-D06", start=None, end=None), label("REQ-D06")])
    result, _ = await run(build_graph("v1", mcp=mcp), "REQ-D06")
    assert result["metrics"]["required_field_retries"] == 1 and result["structured"].start is not None
    assert not any("시간 조항" in w for w in result["recommendation"].warnings)


async def test_대상이_CMDB에_없으면_판정을_만들지_않는다(mcp):
    wrong = label("REQ-D06", target="payment-service")
    queue("REQ-D06", parse=[wrong, wrong])
    with pytest.raises(ReviewIncomplete):
        await run(build_graph("v1", mcp=mcp), "REQ-D06")


async def test_규정_검사를_못_얻으면_판정을_만들지_않는다(mcp, monkeypatch):
    async def down(*args, **kwargs):
        raise MCPTimeoutError("시간 초과")

    monkeypatch.setattr(mcp, "check_policy", down)
    queue("REQ-D01")
    with pytest.raises(ReviewIncomplete):
        await run(build_graph("v1", mcp=mcp), "REQ-D01")


async def test_영향도를_못_얻어도_판정은_나오고_경고만_붙는다(mcp, monkeypatch):
    async def down(*args, **kwargs):
        raise MCPTimeoutError("시간 초과")

    monkeypatch.setattr(mcp, "get_service_info", down)
    queue("REQ-D06")
    result, _ = await run(build_graph("v1", mcp=mcp), "REQ-D06")
    rec = result["recommendation"]
    assert result["impact"].status == "unavailable" and rec.verdict == "승인"
    assert "영향도: 확인 불가 (조회에 실패했다)." in rec.warnings
    assert result["metrics"]["partial_failures"] == 1


async def test_같은_입력을_반복해도_같은_결과다_판정은_LLM이_아니라_코드가_정한다(mcp):
    graph = build_graph("v1", mcp=mcp)
    verdicts = set()
    for _ in range(3):
        queue("REQ-D08")
        result, _ = await run(graph, "REQ-D08")
        verdicts.add((result["recommendation"].verdict, tuple(v.rule_id for v in result["recommendation"].violations)))
    assert verdicts == {("조건부 승인", ("CHG-05", "CHG-08"))}


async def test_main_큐의_호출_순서와_무관하다():
    """스키마별 큐라서 policy와 impact가 어떤 순서로 main을 불러도 자기 응답을 받는다 (fake의 전제 확인)."""
    set_fake_responses("main", [])
    set_fake_responses_for("main", ImpactNote, [ImpactNote(summary="a")])
    set_fake_responses_for("main", PolicyNotes, [PolicyNotes(items=[], summary="b")])
    from app.llm.factory import get_llm, structured_llm

    llm = get_llm("main")
    assert (await structured_llm(llm, PolicyNotes).ainvoke("x")).summary == "b"
    assert (await structured_llm(llm, ImpactNote).ainvoke("x")).summary == "a"
