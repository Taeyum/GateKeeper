"""③ history_agent 서브그래프 테스트 (메모리 내 MCP 서버 + FakeEmbedder 색인 + fake LLM)."""

import pytest

from app import data_loader
from app.agent.mcp_client import MCPClient, MCPTimeoutError
from app.agent.nodes import history_agent as node
from app.agent.nodes.history_agent import (
    HISTORY_TOP_K,
    HistoryNote,
    build_history_subgraph,
    build_query,
    describe_history,
    make_history_agent,
    merge_hits,
)
from app.data_loader import load_history, load_requests
from app.llm.fake import set_fake_responses
from app.llm.usage import UsageCollector
from app.mcp_server.models import HistoryHit
from app.mcp_server.server import build_server
from app.schemas import ChangeRequest
from tests._history import make_index, make_item

HIS_KEYS = {"history_id", "date", "service", "change_type", "title", "description", "outcome", "incident", "related_rules"}


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


def request_of(request_id: str, **overrides) -> ChangeRequest:
    data = gold(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


async def run(mcp, request: ChangeRequest, responses: list | None = None, standard_change_id=None):
    set_fake_responses("main", responses if responses is not None else [HistoryNote(summary="요약")])
    collector = UsageCollector()
    state = {"structured": request, "standard_change_id": standard_change_id}
    out = await make_history_agent(mcp)(state, {"callbacks": [collector]})
    return out, collector


class Recorder:
    def __init__(self, chain):
        self.chain, self.calls = chain, []

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return await self.chain.ainvoke(messages, config)


@pytest.fixture
def recorder(monkeypatch):
    holder: list[Recorder] = []
    real = node.structured_llm

    def wrapped(llm, schema, settings=None):
        holder.append(Recorder(real(llm, schema, settings)))
        return holder[-1]

    monkeypatch.setattr(node, "structured_llm", wrapped)
    return holder


def spy_search(mcp, monkeypatch) -> list:
    calls = []
    original = mcp.search_history

    async def spy(query, k=3, **kwargs):
        calls.append((query, k, {key: value for key, value in kwargs.items() if key != "metrics"}))
        return await original(query, k, **kwargs)

    monkeypatch.setattr(mcp, "search_history", spy)
    return calls


# ---------------------------------------------------------------------------
# 구조
# ---------------------------------------------------------------------------


def test_컴파일된_서브그래프이고_노드_두_개를_가진다(mcp):
    graph = build_history_subgraph(mcp)
    assert set(graph.get_graph().nodes) >= {"search", "summarize"} and graph.name == "history_agent"
    assert {"structured", "standard_change_id", "result", "similar_incidents", "has_similar_incident"} <= set(
        graph.get_input_jsonschema()["properties"]
    )


# ---------------------------------------------------------------------------
# 질의 조립 (순수 함수)
# ---------------------------------------------------------------------------


def test_질의는_구조화_필드로_조립하고_같은_요청이면_늘_같다():
    request = request_of("REQ-D01")  # payments-db 방화벽, 출발지/기한 없음, 금요일 20시
    query = build_query(request, "firewall")
    assert query == build_query(request, "firewall")
    for fragment in ("결제 DB", "(payments-db)", "방화벽 변경", "출발지 미지정", "허용 기한 없음", "금요일 20시"):
        assert fragment in query, fragment
    assert query.endswith(".")


def test_방화벽이_아니면_출발지와_기한을_쓰지_않고_없는_값은_넣지_않는다():
    query = build_query(request_of("REQ-D03"), "db_change")  # billing-db DB 변경, 롤백 계획 없음
    assert "DB 변경" in query and "롤백 계획 없음" in query
    assert "출발지" not in query and "허용 기한" not in query
    bare = build_query(request_of("REQ-D03", start=None, end=None, reason=None, has_rollback=None, port=None), "db_change")
    assert "작업 시각" not in bare and "사유" not in bare and "롤백" not in bare and "번 포트" not in bare


# ---------------------------------------------------------------------------
# 병합 (순수 함수)
# ---------------------------------------------------------------------------


def hit(history_id: str, score: float, **kwargs) -> HistoryHit:
    return HistoryHit(score=score, item=make_item(history_id, kwargs.get("service", "s"), "firewall", kwargs.get("outcome", "성공")))


def test_정확_일치를_먼저_두고_남는_자리를_의미_검색으로_중복_없이_채운다():
    exact = [hit("E-1", 0.3, outcome="장애")]
    semantic = [hit("S-1", 0.9), hit("E-1", 0.8, outcome="장애"), hit("S-2", 0.7), hit("S-3", 0.6)]
    assert [h.item.history_id for h in merge_hits(exact, semantic)] == ["E-1", "S-1", "S-2"]


def test_정확_일치가_없으면_의미_검색_그대로이고_정확_일치가_많으면_앞의_3건만_쓴다():
    semantic = [hit("S-1", 0.9), hit("S-2", 0.8), hit("S-3", 0.7)]
    assert [h.item.history_id for h in merge_hits([], semantic)] == ["S-1", "S-2", "S-3"]
    many = [hit(f"E-{i}", 0.5 - i / 10, outcome="장애") for i in range(5)]
    assert [h.item.history_id for h in merge_hits(many, semantic)] == ["E-0", "E-1", "E-2"]
    assert merge_hits([], []) == [] and HISTORY_TOP_K == 3


def test_코드_요약은_정확_일치_유무와_참고_사례를_말한다():
    merged = [hit("E-1", 0.3, outcome="장애"), hit("S-1", 0.9)]
    assert describe_history(merged, ["E-1"]) == "같은 서비스와 유형의 장애 이력이 1건 있다(E-1). 참고로 비슷한 사례: S-1."
    assert describe_history([hit("S-1", 0.9)], []) == "같은 서비스와 유형의 장애 이력은 없다. 참고로 비슷한 사례: S-1."


# ---------------------------------------------------------------------------
# 정확 일치와 하이브리드 결과
# ---------------------------------------------------------------------------


async def test_dev_요청의_has_similar_incident는_라벨과_같고_정답_사례가_상위_3건에_들어온다(mcp):
    for item in load_requests("dev"):
        if item.request_id in ("REQ-D04", "REQ-D10"):  # 표준 변경은 이력 검사를 돌지 않는다
            continue
        out, _ = await run(mcp, request_of(item.request_id))
        expected = item.expected.similar_history or []
        assert out["has_similar_incident"] is bool(expected), item.request_id
        ids = [incident["history_id"] for incident in out["similar_incidents"]]
        assert len(ids) == 3 and set(expected) <= set(ids), item.request_id
        assert ids[: len(expected)] == expected  # 정확 일치 건이 맨 앞이다


async def test_부모_상태_키와_사례_모양(mcp):
    out, collector = await run(mcp, request_of("REQ-D01"), [HistoryNote(summary="  결제 DB 방화벽 장애 이력 있음  ")])
    history = out["history"]
    assert history.status == "ok" and history.summary == "결제 DB 방화벽 장애 이력 있음"
    assert history.tool_output["has_similar_incident"] is True and history.tool_output["exact_ids"] == ["HIS-049"]
    assert history.tool_output["query"] == build_query(request_of("REQ-D01"), "firewall")
    assert set(history.tool_output["semantic_ids"]) and len(history.tool_output["hits"]) == 3
    first = out["similar_incidents"][0]
    assert set(first) == HIS_KEYS and first["history_id"] == "HIS-049" and first["outcome"] == "장애"
    assert first["incident"]["lesson"]  # history.json 원래 키 그대로
    assert out["errors"] == [] and collector.snapshot().llm_calls == 1


async def test_정확_일치가_의미_검색_상위_3건_밖이어도_맨_앞에_오고_상향_근거가_된다():
    request = request_of("REQ-D01")
    query = build_query(request, "firewall")
    items = [make_item(f"S-{i}", "svc-x", "firewall", "성공", text=query) for i in range(1, 5)]  # 질의와 똑같아 의미 검색 상위를 채운다
    items.append(make_item("S-5", "payments-db", "firewall", "장애", text="인증서 갱신 만료 교체 전혀 다른 내용"))
    async with MCPClient(build_server(history_index=make_index(items)), base_delay=0.01) as mcp:
        out, _ = await run(mcp, request)
    assert out["history"].tool_output["semantic_ids"] == ["S-1", "S-2", "S-3"]  # 의미 검색만으로는 S-5가 안 보인다
    assert out["has_similar_incident"] is True
    assert [i["history_id"] for i in out["similar_incidents"]] == ["S-5", "S-1", "S-2"]


@pytest.mark.parametrize(
    ("service", "change_type", "outcome", "expected"),
    [
        ("payments-db", "firewall", "장애", True),
        ("payments-db", "firewall", "성공", False),  # 결과가 장애가 아니다
        ("payments-db", "deploy", "장애", False),  # 서비스는 같지만 유형이 다르다
        ("billing-db", "firewall", "장애", False),  # 유형은 같지만 서비스가 다르다
    ],
)
async def test_정확_일치는_같은_서비스_같은_유형_장애_세_조건이_모두_맞을_때만이다(service, change_type, outcome, expected):
    request = request_of("REQ-D01")
    other = make_item("O-1", "svc-z", "standard", "성공", text=build_query(request, "firewall"))
    candidate = make_item("C-1", service, change_type, outcome, text="전혀 다른 내용")
    async with MCPClient(build_server(history_index=make_index([other, candidate])), base_delay=0.01) as mcp:
        out, _ = await run(mcp, request)
    assert out["has_similar_incident"] is expected


async def test_두_검색을_같은_질의로_부르고_정확_일치는_실효_유형으로_거른다(mcp, monkeypatch):
    calls = spy_search(mcp, monkeypatch)
    await run(mcp, request_of("REQ-D01"))
    (exact_call,) = [c for c in calls if c[2]]
    (semantic_call,) = [c for c in calls if not c[2]]
    assert exact_call[0] == semantic_call[0] and exact_call[1] == semantic_call[1] == 3
    assert exact_call[2] == {"service": "payments-db", "change_type": "firewall", "outcome": "장애"}

    calls.clear()
    # 표준 후보였는데 엔진이 확정하지 못했으면(standard_change_id 없음) 실효 유형은 배포다
    await run(mcp, request_of("REQ-D04"), standard_change_id=None)
    assert [c[2]["change_type"] for c in calls if c[2]] == ["deploy"]
    calls.clear()
    await run(mcp, request_of("REQ-D04"), standard_change_id="STD-01")
    assert [c[2]["change_type"] for c in calls if c[2]] == ["standard"]


# ---------------------------------------------------------------------------
# LLM 요약
# ---------------------------------------------------------------------------


async def test_요약_프롬프트에_사례와_정확_일치_표시가_들어간다(mcp, recorder):
    await run(mcp, request_of("REQ-D01"))
    system, user = recorder[0].calls[0]
    assert "과거 사례 요약 보조" in system.content and "판정" in system.content
    assert "같은 서비스와 유형의 장애 이력(코드가 이력 전체에서 확인): 있음" in user.content
    assert "[같은 서비스와 유형의 장애] HIS-049" in user.content and "교훈:" in user.content


async def test_요약_LLM이_실패해도_찾은_사례와_상향_근거는_그대로이고_코드_요약이_남는다(mcp):
    out, collector = await run(mcp, request_of("REQ-D01"), responses=[])  # fake 응답 큐가 비어 호출이 실패한다
    assert out["has_similar_incident"] is True and out["similar_incidents"][0]["history_id"] == "HIS-049"
    assert out["history"].status == "ok" and out["history"].summary.startswith("같은 서비스와 유형의 장애 이력이 1건 있다(HIS-049)")
    assert out["errors"] == ["history_agent: 요약 작성 실패 (FakeResponsesExhausted)"]
    assert out["metrics"]["partial_failures"] == 1


# ---------------------------------------------------------------------------
# 검색 실패
# ---------------------------------------------------------------------------


def fail_when(mcp, monkeypatch, *, exact: bool):
    original = mcp.search_history

    async def flaky(query, k=3, **kwargs):
        if bool(kwargs.get("outcome")) is exact:
            raise MCPTimeoutError("시간 초과")
        return await original(query, k, **kwargs)

    monkeypatch.setattr(mcp, "search_history", flaky)


async def test_정확_일치_검색이_실패하면_확인_불가로_진행하고_이력_키를_남기지_않는다(mcp, monkeypatch):
    fail_when(mcp, monkeypatch, exact=True)
    out, collector = await run(mcp, request_of("REQ-D01"))
    assert out["history"].status == "unavailable" and out["history"].summary == "이력: 확인 불가"
    assert "similar_incidents" not in out and "has_similar_incident" not in out  # 상향 없이 진행한다
    assert out["errors"] == ["history_agent: 이력 검색 실패 (MCPTimeoutError)"] and out["metrics"]["partial_failures"] == 1
    assert collector.snapshot().llm_calls == 0  # 요약 LLM을 부르지 않는다


async def test_의미_검색만_실패하면_정확_일치_결과로_진행한다(mcp, monkeypatch):
    fail_when(mcp, monkeypatch, exact=False)
    out, _ = await run(mcp, request_of("REQ-D01"))
    assert out["history"].status == "ok" and out["has_similar_incident"] is True
    assert [i["history_id"] for i in out["similar_incidents"]] == ["HIS-049"]
    assert out["errors"] == ["history_agent: 의미 검색 실패 (MCPTimeoutError)"] and out["metrics"]["partial_failures"] == 1


def test_이력_파일에_장애가_있는_조합이_dev_라벨의_정답과_맞는지_교차_확인한다():
    """정확 일치 판단이 쓰는 조건을 history.json에서 직접 세어, 에이전트와 같은 결론인지 본다."""
    history = list(load_history())
    for item in load_requests("dev"):
        structured = item.expected.structured
        if structured.change_type == "standard":
            continue
        direct = any(
            h.service == structured.target and h.change_type == structured.change_type and h.outcome == "장애" for h in history
        )
        assert direct is bool(item.expected.similar_history), item.request_id
