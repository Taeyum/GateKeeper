"""v2 그래프 통합 테스트 (fake LLM, 메모리 내 MCP 서버와 FakeEmbedder 색인, 실제 규칙 엔진).

v1 그래프 테스트(test_graph_v1.py)와 같은 방식이다. 병렬로 도는 서브에이전트가 같은 main 큐를 함께 쓰므로 응답은
구조화 출력 스키마별 큐(set_fake_responses_for)로 넣는다.
"""

import asyncio
import time

import pytest

from app import data_loader, rules_engine
from app.agent import graph as graph_module
from app.agent.graph import build_graph, route_after_classify_v2
from app.agent.mcp_client import MCPClient, MCPTimeoutError
from app.agent.nodes.classify import RouteGuess
from app.agent.nodes.decide import DecideDraft
from app.agent.nodes.history_agent import HistoryNote
from app.agent.nodes.impact_agent import ImpactNote
from app.agent.nodes.policy_agent import PolicyNotes, RuleNoteItem, rule_ids_of
from app.agent.nodes.validate import INCOMPLETE_WARNING, ValidationCheck
from app.data_loader import load_requests
from app.llm.fake import set_fake_responses_for
from app.llm.usage import UsageCollector
from app.mcp_server.server import build_server
from app.schemas import ChangeRequest
from eval.harness import initial_state
from tests._history import make_index

OK = ValidationCheck(consistent=True, issues=[])
BAD = ValidationCheck(consistent=False, issues=["설명이 판정과 다르다"])


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


@pytest.fixture
async def mcp():
    async with MCPClient(build_server(history_index=make_index()), base_delay=0.01) as client:
        yield client


def gold(request_id: str):
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def label(request_id: str, **overrides) -> ChangeRequest:
    data = gold(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


def draft(explanation: str = "설명") -> DecideDraft:
    return DecideDraft(explanation=explanation, conditions=["조건을 충족한다"], alternative_start=None, alternative_end=None)


def queue(request_id: str, *, drafts: list | None = None, checks: list | None = None, history: list | None = None) -> None:
    """한 요청이 거치는 LLM 호출에 줄 응답을 스키마별로 넣는다. 기본값은 라벨 구조화와 일관된 응답이다."""
    request = label(request_id)
    check = rules_engine.check_all(request, gold(request_id).submitted_at)
    standard = rules_engine.is_standard(request).is_standard
    set_fake_responses_for("main", ChangeRequest, [request])
    set_fake_responses_for(
        "small", RouteGuess, [RouteGuess(change_type="standard" if standard else request.change_type, is_emergency=request.is_emergency)]
    )
    set_fake_responses_for(
        "main", PolicyNotes,
        [PolicyNotes(items=[RuleNoteItem(rule_id=i, note=f"{i} 설명") for i in rule_ids_of(check)], summary="정책 요약")],
    )  # fmt: skip
    set_fake_responses_for("main", ImpactNote, [ImpactNote(summary="영향도 요약")])
    set_fake_responses_for("main", HistoryNote, history if history is not None else [HistoryNote(summary="이력 요약")])
    set_fake_responses_for("main", DecideDraft, drafts or [draft()] * 4)
    set_fake_responses_for("main", ValidationCheck, checks or [OK] * 4)


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


def test_그래프_구성과_세_서브에이전트_구조도(mcp):
    graph = build_graph("v2", mcp=mcp)
    assert set(graph.get_graph().nodes) == {
        "__start__", "parse_request", "classify", "policy_agent", "impact_agent", "history_agent", "decide", "validate", "__end__",
    }  # fmt: skip
    expanded = graph.get_graph(xray=True)
    assert {"history_agent:search", "history_agent:summarize"} <= set(expanded.nodes)
    assert {"policy_agent:check_policy", "impact_agent:lookup"} <= set(expanded.nodes)
    mermaid = expanded.draw_mermaid()
    assert all(f"subgraph {name}" in mermaid for name in ("policy_agent", "impact_agent", "history_agent"))


def test_표준_변경만_정책_검사로_가고_나머지는_세_서브에이전트가_병렬이다():
    assert route_after_classify_v2({"route": "standard"}) == ["policy_agent"]
    for route in ("general", "emergency"):
        assert route_after_classify_v2({"route": route}) == ["policy_agent", "impact_agent", "history_agent"]


# ---------------------------------------------------------------------------
# 전체 실행
# ---------------------------------------------------------------------------


async def test_dev_10건의_판정_위반_긴급은_라벨과_같고_위험도도_모두_라벨과_같다(mcp):
    graph = build_graph("v2", mcp=mcp)
    for item in load_requests("dev"):
        queue(item.request_id)
        result, _ = await run(graph, item.request_id)
        rec = result["recommendation"]
        assert rec.verdict == item.expected.verdict, item.request_id  # 이력은 판정을 바꾸지 않는다
        assert sorted(v.rule_id for v in rec.violations) == sorted(item.expected.violations), item.request_id
        assert rec.emergency_valid == item.expected.emergency_valid
        assert rec.risk == item.expected.risk, item.request_id  # v1이 틀리던 REQ-D03(중간 -> 높음)이 이력 상향으로 맞는다
        assert result["validation"].passed is True and INCOMPLETE_WARNING not in " ".join(rec.warnings)


async def test_이력_상향과_사례는_dev_라벨의_정답과_맞는다(mcp):
    graph = build_graph("v2", mcp=mcp)
    for item in load_requests("dev"):
        queue(item.request_id)
        result, _ = await run(graph, item.request_id)
        gold_ids = item.expected.similar_history
        if item.request_id in ("REQ-D04", "REQ-D10"):  # 표준 변경은 이력 검사를 돌지 않는다
            assert "history" not in result and "similar_incidents" not in result
            assert result["recommendation"].similar_history is None
            continue
        assert result["has_similar_incident"] is bool(gold_ids), item.request_id
        ids = result["recommendation"].similar_history
        assert ids == [i["history_id"] for i in result["similar_incidents"]] and set(gold_ids) <= set(ids), item.request_id


async def test_REQ_D03은_billing_db의_장애_이력_때문에_위험도가_중간에서_높음으로_오른다(mcp):
    queue("REQ-D03")
    result, _ = await run(build_graph("v2", mcp=mcp), "REQ-D03")
    assert result["has_similar_incident"] is True and result["similar_incidents"][0]["history_id"] == "HIS-034"
    assert result["recommendation"].risk == "높음" and result["recommendation"].verdict == "조건부 승인"


async def test_LLM_호출은_노드마다_한_번씩이고_v1보다_이력_요약과_검증이_늘어난다(mcp):
    queue("REQ-D06", drafts=[draft()])  # 위반 없는 승인: 정책 설명과 대안이 없다
    result, usage = await run(build_graph("v2", mcp=mcp), "REQ-D06")
    # parse 1 + classify(small) 1 + impact 1 + history 1 + decide 1 + validate 1 (policy는 위반이 없어 설명을 생략)
    assert usage.llm_calls == 6
    assert result["metrics"].get("validator_rewrites", 0) == 0


async def test_표준_경로는_영향도와_이력을_돌지_않고_validate는_돈다(mcp, monkeypatch):
    searched, looked_up = [], []
    search, info = mcp.search_history, mcp.get_service_info

    async def spy_search(*args, **kwargs):
        searched.append(args)
        return await search(*args, **kwargs)

    async def spy_info(*args, **kwargs):
        looked_up.append(args)
        return await info(*args, **kwargs)

    monkeypatch.setattr(mcp, "search_history", spy_search)
    monkeypatch.setattr(mcp, "get_service_info", spy_info)
    queue("REQ-D04")
    result, usage = await run(build_graph("v2", mcp=mcp), "REQ-D04")
    assert result["route"] == "standard" and searched == [] and looked_up == []
    assert result["recommendation"].verdict == "자동 승인" and result["recommendation"].risk is None
    assert result["validation"].passed is True
    assert usage.llm_calls == 4  # parse, classify, decide, validate


async def test_세_서브에이전트는_동시에_돈다(mcp, monkeypatch):
    """셋이 서로의 시작을 기다리게 한다. 순서대로 돌면 기다림이 시간 초과로 끝나 부분 실패로 드러난다."""
    started = {name: asyncio.Event() for name in ("policy", "impact", "history")}
    originals = {"policy": mcp.check_policy, "impact": mcp.get_service_info, "history": mcp.search_history}

    def gate(name):
        async def slow(*args, **kwargs):
            started[name].set()
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started.values())), 2)
            return await originals[name](*args, **kwargs)

        return slow

    monkeypatch.setattr(mcp, "check_policy", gate("policy"))
    monkeypatch.setattr(mcp, "get_service_info", gate("impact"))
    monkeypatch.setattr(mcp, "search_history", gate("history"))
    queue("REQ-D06")
    begun = time.perf_counter()
    result, _ = await run(build_graph("v2", mcp=mcp), "REQ-D06")
    assert time.perf_counter() - begun < 1.5
    assert all(result[name].status == "ok" for name in ("policy", "impact", "history")) and result["errors"] == []


async def test_병렬_서브에이전트_셋의_metrics와_errors가_충돌_없이_합쳐진다(mcp):
    queue("REQ-D01", history=[])
    set_fake_responses_for("main", PolicyNotes, [])
    set_fake_responses_for("main", ImpactNote, [])
    result, _ = await run(build_graph("v2", mcp=mcp), "REQ-D01")
    assert result["metrics"]["partial_failures"] == 3
    assert sorted(e.split(":")[0] for e in result["errors"]) == ["history_agent", "impact_agent", "policy_agent"]
    assert result["recommendation"].verdict == "반려"  # 설명이 없어도 판정은 그대로다


async def test_이력_검색을_못_해도_판정은_나오고_상향_없이_경고만_붙는다(mcp, monkeypatch):
    async def down(*args, **kwargs):
        raise MCPTimeoutError("시간 초과")

    monkeypatch.setattr(mcp, "search_history", down)
    queue("REQ-D03")
    result, _ = await run(build_graph("v2", mcp=mcp), "REQ-D03")
    rec = result["recommendation"]
    assert result["history"].status == "unavailable" and rec.verdict == "조건부 승인" and rec.risk == "중간"
    assert rec.similar_history is None and any(w.startswith("이력: 확인 불가") for w in rec.warnings)


# ---------------------------------------------------------------------------
# 검증 루프: 재작성은 최대 1회
# ---------------------------------------------------------------------------


@pytest.fixture
def decide_calls(monkeypatch) -> list:
    """decide가 불린 횟수와 그때의 validation 상태."""
    calls: list = []
    real = graph_module.decide

    async def counting(state, config):
        calls.append(state.get("validation"))
        return await real(state, config)

    monkeypatch.setattr(graph_module, "decide", counting)
    return calls


async def test_검증에_한_번_실패하면_decide로_돌아가_다시_쓰고_통과하면_끝난다(mcp, decide_calls):
    queue("REQ-D06", drafts=[draft("처음 설명"), draft("고친 설명")], checks=[BAD, OK])
    result, usage = await run(build_graph("v2", mcp=mcp), "REQ-D06")
    assert len(decide_calls) == 2 and decide_calls[0] is None and decide_calls[1].reasons == ["설명 검증: 설명이 판정과 다르다"]
    assert result["recommendation"].explanation == "고친 설명"
    assert result["validation"].passed is True and result["validation"].rewrites == 1
    assert result["metrics"]["validator_rewrites"] == 1 and "validator_incomplete" not in result["metrics"]
    assert INCOMPLETE_WARNING not in " ".join(result["recommendation"].warnings)
    assert usage.llm_calls == 8  # 위 6회 + 재작성 decide 1 + 재검증 validate 1


async def test_두_번째도_실패하면_더_돌지_않고_근거_검증_미완료_경고로_끝낸다(mcp, decide_calls):
    queue("REQ-D06", drafts=[draft("처음"), draft("고쳤지만 여전히")], checks=[BAD, BAD, OK])
    result, usage = await run(build_graph("v2", mcp=mcp), "REQ-D06")
    assert len(decide_calls) == 2  # 재작성은 최대 1회다
    assert result["validation"].incomplete is True
    rec = result["recommendation"]
    assert any(w.startswith(f"{INCOMPLETE_WARNING}: ") for w in rec.warnings)
    assert rec.verdict == "승인" and rec.explanation == "고쳤지만 여전히"  # 판정은 그대로, 마지막 설명을 쓴다
    assert result["metrics"]["validator_rewrites"] == 1 and result["metrics"]["validator_incomplete"] == 1
    assert usage.llm_calls == 8  # validate가 세 번째로 불리지 않는다


async def test_같은_입력을_반복해도_같은_결과다(mcp):
    graph = build_graph("v2", mcp=mcp)
    outcomes = set()
    for _ in range(3):
        queue("REQ-D08")
        result, _ = await run(graph, "REQ-D08")
        rec = result["recommendation"]
        outcomes.add((rec.verdict, rec.risk, tuple(v.rule_id for v in rec.violations), tuple(rec.similar_history or ())))
    assert len(outcomes) == 1
