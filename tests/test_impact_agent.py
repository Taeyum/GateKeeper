"""③ impact_agent 서브그래프 테스트 (메모리 내 MCP 서버 + fake LLM)."""

from datetime import timedelta

import pytest

from app import data_loader
from app.agent.mcp_client import MCPClient, MCPNotFoundError, MCPTimeoutError
from app.agent.nodes import impact_agent as node
from app.agent.nodes.impact_agent import (
    NO_START,
    ImpactNote,
    build_impact_subgraph,
    describe_impact,
    make_impact_agent,
    work_window,
)
from app.data_loader import load_requests, load_rules
from app.llm.fake import set_fake_responses
from app.llm.usage import UsageCollector
from app.mcp_server.models import CalendarResult
from app.mcp_server.server import build_server
from app.schemas import ChangeRequest


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


def request_of(request_id: str, **overrides) -> ChangeRequest:
    data = gold(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


async def run(mcp, request: ChangeRequest, request_id: str, responses: list):
    set_fake_responses("main", responses)
    collector = UsageCollector()
    state = {"structured": request, "submitted_at": gold(request_id).submitted_at.isoformat(), "route": "general"}
    out = await make_impact_agent(mcp)(state, {"callbacks": [collector]})
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


def spy_calendar(mcp, monkeypatch) -> list:
    calls = []
    original = mcp.get_calendar

    async def spy(target, start, end, **kwargs):
        calls.append((target, start, end))
        return await original(target, start, end, **kwargs)

    monkeypatch.setattr(mcp, "get_calendar", spy)
    return calls


# ---------------------------------------------------------------------------
# 구조와 정상 경로
# ---------------------------------------------------------------------------


def test_컴파일된_서브그래프이고_노드_두_개를_가진다(mcp):
    graph = build_impact_subgraph(mcp)
    assert set(graph.get_graph().nodes) >= {"lookup", "summarize"} and graph.name == "impact_agent"
    assert {"structured", "submitted_at", "route", "service", "calendar", "result"} <= set(graph.get_input_jsonschema()["properties"])


async def test_서비스_정보와_캘린더를_조회해_LLM이_요약한다(mcp, monkeypatch, recorder):
    calendar_calls = spy_calendar(mcp, monkeypatch)
    request = request_of("REQ-D01")  # payments-db, 10/30(금) 20:00
    out, collector = await run(mcp, request, "REQ-D01", [ImpactNote(summary="  Tier-1 결제 DB, 동결 기간과 겹침  ")])

    result = out["impact"]
    assert result.status == "ok" and result.summary == "Tier-1 결제 DB, 동결 기간과 겹침"
    assert result.tool_output["service"]["id"] == "payments-db" and result.tool_output["service"]["tier"] == "Tier-1"
    assert result.tool_output["calendar"]["freeze_periods"] and result.tool_output["calendar_skipped"] is None
    assert calendar_calls == [("payments-db", request.start, request.end)]
    assert collector.snapshot().llm_calls == 1 and out["errors"] == [] and out["metrics"] == {}
    system, user = recorder[0].calls[0]
    assert "영향도 요약 보조" in system.content and '"tier": "Tier-1"' in user.content


async def test_종료_시각이_없으면_rules_json의_기본_작업_시간으로_조회한다(mcp, monkeypatch):
    calendar_calls = spy_calendar(mcp, monkeypatch)
    await run(mcp, request_of("REQ-D01", end=None), "REQ-D01", [ImpactNote(summary="요약")])
    (_, start, end), = calendar_calls
    assert end - start == timedelta(minutes=load_rules().defaults.work_duration_minutes_if_missing)


async def test_작업_시각이_없으면_캘린더를_조회하지_않고_이유를_남긴다(mcp, monkeypatch, recorder):
    calendar_calls = spy_calendar(mcp, monkeypatch)
    out, _ = await run(mcp, request_of("REQ-D06", start=None, end=None), "REQ-D06", [ImpactNote(summary="요약")])
    result = out["impact"]
    assert calendar_calls == [] and result.status == "ok"
    assert result.tool_output["calendar"] is None and result.tool_output["calendar_skipped"] == NO_START
    assert NO_START in recorder[0].calls[0][1].content  # LLM이 "확인하지 못했다"를 쓸 수 있게 이유를 준다


# ---------------------------------------------------------------------------
# 실패
# ---------------------------------------------------------------------------


async def test_서비스_조회가_실패하면_영향도_확인_불가이고_LLM을_부르지_않는다(mcp, monkeypatch):
    async def boom(*args, **kwargs):
        raise MCPTimeoutError("시간 초과")

    monkeypatch.setattr(mcp, "get_service_info", boom)
    out, collector = await run(mcp, request_of("REQ-D01"), "REQ-D01", [])
    result = out["impact"]
    assert result.status == "unavailable" and result.summary == "영향도: 확인 불가"
    assert result.tool_output["service"] is None and result.tool_output["calendar"] is not None  # 성공한 조회는 남긴다
    assert out["errors"] == ["impact_agent: 조회 실패 (MCPTimeoutError)"]
    assert out["metrics"]["partial_failures"] == 1 and collector.snapshot().llm_calls == 0


async def test_캘린더_조회만_실패해도_확인_불가다(mcp, monkeypatch):
    async def boom(*args, **kwargs):
        raise MCPNotFoundError("NOT_FOUND")

    monkeypatch.setattr(mcp, "get_calendar", boom)
    out, collector = await run(mcp, request_of("REQ-D01"), "REQ-D01", [])
    assert out["impact"].status == "unavailable" and out["impact"].tool_output["service"]["id"] == "payments-db"
    assert out["errors"] == ["impact_agent: 조회 실패 (MCPNotFoundError)"] and collector.snapshot().llm_calls == 0


async def test_요약이_실패하면_코드가_만든_문장을_그대로_둔다(mcp):
    out, _ = await run(mcp, request_of("REQ-D01"), "REQ-D01", [])  # main 응답 큐가 비어 있다
    result = out["impact"]
    assert result.status == "ok" and result.summary.startswith("payments-db는 Tier-1이고")
    assert out["errors"] == ["impact_agent: 요약 작성 실패 (FakeResponsesExhausted)"]
    assert out["metrics"] == {"partial_failures": 1}


async def test_래퍼는_config를_넘겨서_서브그래프_안의_LLM_호출도_집계된다(mcp):
    out, collector = await run(mcp, request_of("REQ-D01"), "REQ-D01", [ImpactNote(summary="요약")])
    assert collector.snapshot().llm_calls == 1 and set(out) == {"impact", "errors", "metrics"}


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


async def test_코드가_만드는_요약은_의존_관계와_겹치는_일정을_담는다(mcp):
    service = await mcp.get_service_info("payments-db")
    request = request_of("REQ-D01")
    calendar = await mcp.get_calendar("payments-db", request.start, request.end)
    text = describe_impact(service, calendar)
    assert "Tier-1" in text and service.owner_team in text
    assert "동결 기간: " in text and calendar.freeze_periods[0].freeze_id in text
    empty = CalendarResult(target="payments-db", start=request.start, end=request.end, freeze_periods=[], scheduled_changes=[])
    assert "동결 기간: 없음. 같은 대상의 예정 변경: 없음." in describe_impact(service, empty)
    assert NO_START in describe_impact(service, None)


def test_작업_구간():
    request = request_of("REQ-D01")
    assert work_window(request) == (request.start, request.end)
    assert work_window(request.model_copy(update={"start": None, "end": None})) is None
    assert work_window(request.model_copy(update={"end": None}))[1] == request.start + timedelta(minutes=60)
