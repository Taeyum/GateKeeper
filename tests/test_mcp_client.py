import asyncio
import shutil
import subprocess

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app import data_loader, rules_engine
from app.agent.mcp_client import (
    MCPClient,
    MCPClientError,
    MCPNotFoundError,
    MCPTimeoutError,
    MCPToolError,
    server_env,
)
from app.mcp_server.server import build_server
from app.schemas import HistoryItem, PolicyCheckResult, Violation
from tests._engine import dev_case, requires_engine
from tests._history import make_index


def _server_process_count() -> int:
    out = subprocess.run(["pgrep", "-f", "app.mcp_server.server"], capture_output=True, text=True).stdout
    return len(out.split())


# ---------------------------------------------------------------- 실제 stdio 서브프로세스


@pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep이 필요하다")
async def test_서버_프로세스를_한_번만_띄워_재사용한다():
    before = _server_process_count()
    async with MCPClient() as mcp:
        assert mcp.is_connected and _server_process_count() == before + 1

        assert "5432" in await mcp.read_rule("CHG-03")
        assert (await mcp.get_service_info("payments-db")).tier == "Tier-1"
        # 병렬 서브에이전트처럼 동시에 불러도 연결 하나로 처리된다 (Lock 없이)
        results = await asyncio.gather(
            *[mcp.get_incident("INC-2026-0412") for _ in range(6)], mcp.list_rule_uris()
        )
        assert [r.status for r in results[:6]] == ["open"] * 6 and len(results[6]) == 12
        assert _server_process_count() == before + 1  # 호출을 늘려도 프로세스는 그대로

        with pytest.raises(MCPNotFoundError, match="payment-db"):
            await mcp.get_service_info("payment-db")
        assert await mcp.get_service_info("payments-db")  # 오류 뒤에도 같은 연결로 계속 쓴다
    assert _server_process_count() == before  # 종료하면 서버 프로세스도 끝난다


def test_자식_프로세스에는_키_종류를_넘기지_않는다():
    env = server_env(
        {
            "PATH": "/usr/bin",
            "GK_DATA_DIR": "gatekeeper_data",
            "ANTHROPIC_API_KEY": "a",
            "OPENAI_API_KEY": "b",
            "LANGCHAIN_API_KEY": "c",
            "GITHUB_TOKEN": "d",
            "DB_PASSWORD": "e",
            "MY_SECRET": "f",
        }
    )
    assert env["PATH"] == "/usr/bin" and env["GK_DATA_DIR"] == "gatekeeper_data"
    assert not any(k in env for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LANGCHAIN_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "MY_SECRET"))
    assert env["FASTMCP_SHOW_SERVER_BANNER"] == "false"


# ---------------------------------------------------------------- 메모리 내 연결: 타입이 있는 헬퍼


@pytest.fixture
async def mcp():
    async with MCPClient(build_server(), base_delay=0.01) as client:
        yield client


async def test_타입이_있는_조회_헬퍼(mcp):
    info = await mcp.get_service_info("payments-db")
    assert (info.id, info.tier, info.default_port) == ("payments-db", "Tier-1", 5432)

    from datetime import datetime

    window = (datetime.fromisoformat("2026-10-30T20:00:00+09:00"), datetime.fromisoformat("2026-10-30T21:00:00+09:00"))
    calendar = await mcp.get_calendar("payments-db", *window)
    assert [f.freeze_id for f in calendar.freeze_periods] == ["FRZ-01"]

    ticket = await mcp.get_incident("INC-2026-0412")
    assert ticket.status == "open" and "payments-db" in ticket.services


async def test_규정_원문_읽기(mcp):
    assert "### CHG-01" in await mcp.read_rule("CHG-01")
    assert "STD-02" in await mcp.read_standard()
    assert set(await mcp.list_rule_uris()) == {"rules://index", "rules://standard", *[f"rules://CHG-{i:02d}" for i in range(1, 11)]}


async def test_없는_조항은_MCPNotFoundError(mcp):
    with pytest.raises(MCPNotFoundError):
        await mcp.read_rule("CHG-99")


async def test_인자_오류는_MCPToolError(mcp):
    from datetime import datetime

    t = datetime.fromisoformat("2026-10-30T20:00:00+09:00")
    with pytest.raises(MCPToolError, match="INVALID_ARGUMENT"):
        await mcp.get_calendar("payments-db", t, t)


async def test_search_history_타입이_있는_헬퍼와_필터():
    async with MCPClient(build_server(history_index=make_index()), base_delay=0.01) as client:
        result = await client.search_history("파트너 임시 허용 결제 승인 지연")
        assert len(result.hits) == 3 and all(isinstance(hit.item, HistoryItem) for hit in result.hits)
        assert [hit.score for hit in result.hits] == sorted((hit.score for hit in result.hits), reverse=True)

        # 필터는 서버로 그대로 전달되고, 질의와 무관해도 그 조건의 이력을 찾는다
        exact = await client.search_history("인증서 갱신 만료 교체", service="payments-db", change_type="firewall", outcome="장애")
        assert "HIS-049" in [hit.item.history_id for hit in exact.hits]
        assert all(hit.item.service == "payments-db" and hit.item.outcome == "장애" for hit in exact.hits)


async def test_search_history_오류는_MCPToolError이고_재시도하지_않는다(tmp_path, monkeypatch):
    async with MCPClient(build_server(history_index=make_index()), base_delay=0.01) as client:
        metrics: dict[str, int] = {}
        with pytest.raises(MCPToolError, match="INVALID_ARGUMENT"):
            await client.search_history("x", 0, metrics=metrics)
        assert metrics == {} and client.total_retries == 0

    monkeypatch.setenv("GK_RAG_DIR", str(tmp_path / "없는_색인"))
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()
    async with MCPClient(build_server(), base_delay=0.01) as client:
        with pytest.raises(MCPToolError, match="INDEX_NOT_READY"):
            await client.search_history("결제")
        assert client.total_retries == 0


async def test_엔진_배선_check_policy와_check_window(mcp, monkeypatch):
    request, submitted_at, _ = dev_case("REQ-D01")
    violation = Violation(rule_id="CHG-01", severity="forbidden", evidence="가짜")
    monkeypatch.setattr(rules_engine, "check_all", lambda req, at, data=None: PolicyCheckResult(violations=[violation]))
    monkeypatch.setattr(rules_engine, "check_window", lambda *args, **kwargs: [violation])

    result = await mcp.check_policy(request, submitted_at)
    assert isinstance(result, PolicyCheckResult) and result.violations == [violation]
    assert await mcp.check_window("payments-db", "firewall", request.start, request.end, submitted_at) == [violation]


async def test_엔진_Tool도_없는_대상은_MCPNotFoundError이고_재시도하지_않는다(mcp, monkeypatch):
    request, submitted_at, _ = dev_case("REQ-D01")

    def must_not_run(*args, **kwargs):
        raise AssertionError("엔진 호출 전에 NOT_FOUND여야 한다")

    monkeypatch.setattr(rules_engine, "check_all", must_not_run)
    monkeypatch.setattr(rules_engine, "check_window", must_not_run)
    metrics: dict[str, int] = {}
    with pytest.raises(MCPNotFoundError, match="nope-db"):
        await mcp.check_policy(request.model_copy(update={"target": "nope-db"}), submitted_at, metrics=metrics)
    with pytest.raises(MCPNotFoundError, match="nope-db"):
        await mcp.check_window("nope-db", "firewall", request.start, request.end, submitted_at, metrics=metrics)
    assert metrics == {}


async def test_엔진의_ValueError는_MCPToolError_INVALID_ARGUMENT이고_재시도하지_않는다(mcp, monkeypatch):
    request, submitted_at, _ = dev_case("REQ-D01")
    calls = []

    def bad_window(*args, **kwargs):
        calls.append(1)
        raise ValueError("작업 종료 시각이 시작 시각보다 늦어야 한다")

    monkeypatch.setattr(rules_engine, "check_all", bad_window)
    metrics: dict[str, int] = {}
    with pytest.raises(MCPToolError, match="INVALID_ARGUMENT") as excinfo:
        await mcp.check_policy(request, submitted_at, metrics=metrics)
    assert not isinstance(excinfo.value, MCPNotFoundError)  # 대상 없음과 구분된다
    assert len(calls) == 1 and metrics == {}


async def test_시작_전에_부르면_안내한다():
    with pytest.raises(MCPClientError, match="start"):
        await MCPClient(build_server()).get_incident("INC-2026-0412")


async def test_start는_두_번_불러도_연결을_새로_만들지_않는다():
    client = MCPClient(build_server())
    await client.start()
    first = client._client
    await client.start()
    assert client._client is first
    await client.stop()
    await client.stop()  # stop도 여러 번 안전
    assert not client.is_connected


# ---------------------------------------------------------------- 타임아웃과 재시도


def _slow_server() -> tuple[FastMCP, dict[str, int]]:
    """느리거나 실패하는 Tool을 가진 테스트용 서버와 Tool별 호출 횟수."""
    server = FastMCP("slow")
    calls = {"slow_once": 0, "always_slow": 0, "fast": 0, "invalid": 0, "missing": 0}

    @server.tool
    async def slow_once() -> dict:
        calls["slow_once"] += 1
        if calls["slow_once"] == 1:
            await asyncio.sleep(0.6)  # 첫 호출만 타임아웃을 넘긴다
        return {"ok": True, "call": calls["slow_once"]}

    @server.tool
    async def always_slow() -> dict:
        calls["always_slow"] += 1
        await asyncio.sleep(0.6)
        return {"ok": True}

    @server.tool
    def fast() -> dict:
        calls["fast"] += 1
        return {"ok": True}

    @server.tool
    def invalid() -> dict:
        calls["invalid"] += 1
        raise ToolError("INVALID_ARGUMENT: 인자가 잘못됐다")

    @server.tool
    def missing() -> dict:
        calls["missing"] += 1
        raise ToolError("NOT_FOUND: 없다")

    return server, calls


@pytest.fixture
async def slow():
    server, calls = _slow_server()
    async with MCPClient(server, timeout=0.2, retries=2, base_delay=0.01) as client:
        yield client, calls


async def test_타임아웃_뒤_재시도로_복구하고_재시도_횟수를_센다(slow):
    client, calls = slow
    metrics: dict[str, int] = {}
    result = await client.call_tool("slow_once", {}, metrics=metrics)
    assert result == {"ok": True, "call": 2}
    assert calls["slow_once"] == 2
    assert metrics == {"tool_retries": 1} and client.total_retries == 1


async def test_재시도를_다_쓰면_MCPTimeoutError(slow):
    client, calls = slow
    metrics = {"tool_retries": 5}  # 기존 값에 더한다
    with pytest.raises(MCPTimeoutError, match="always_slow"):
        await client.call_tool("always_slow", {}, metrics=metrics)
    assert calls["always_slow"] == 3  # 최초 1번 + 재시도 2번
    assert metrics["tool_retries"] == 7  # 실패한 호출의 재시도도 센다


async def test_타임아웃을_겪은_뒤에도_같은_연결로_계속_쓴다(slow):
    client, calls = slow
    with pytest.raises(MCPTimeoutError):
        await client.call_tool("always_slow", {})
    assert await client.call_tool("fast", {}) == {"ok": True}


async def test_호출마다_타임아웃을_바꿀_수_있다(slow):
    client, calls = slow
    assert await client.call_tool("always_slow", {}, timeout=2.0) == {"ok": True}
    assert calls["always_slow"] == 1


async def test_영구_오류는_재시도하지_않는다(slow):
    client, calls = slow
    metrics: dict[str, int] = {}
    with pytest.raises(MCPToolError, match="INVALID_ARGUMENT"):
        await client.call_tool("invalid", {}, metrics=metrics)
    with pytest.raises(MCPNotFoundError):
        await client.call_tool("missing", {}, metrics=metrics)
    assert calls["invalid"] == 1 and calls["missing"] == 1
    assert metrics == {} and client.total_retries == 0


# ---------------------------------------------------------------- 규칙 엔진 필요 (2단계 머지 후)


@requires_engine
async def test_check_policy_REQ_D01_위반_5개(mcp):
    request, submitted_at, _ = dev_case("REQ-D01")
    result = await mcp.check_policy(request, submitted_at)
    assert {v.rule_id for v in result.violations} == {"CHG-01", "CHG-02", "CHG-03", "CHG-09", "CHG-10"}
