"""MCP 클라이언트. 서버 프로세스를 한 번 띄워 두고 모든 호출이 재사용한다.

나중에 FastAPI lifespan에서 한 번 만들어(start) 앱이 끝날 때 닫는다(stop).

호출마다 타임아웃을 걸고, 일시 오류(시간 초과, 연결 끊김)만 지수 백오프로 재시도한다.
"그런 대상이 없다"(NOT_FOUND)나 인자 오류는 다시 해도 같은 결과라서 재시도하지 않고 바로 올린다.
재시도 횟수는 호출자가 넘긴 metrics["tool_retries"]에 더한다 (SPEC의 state.metrics).

오류 종류
    MCPNotFoundError  대상이 없다 (서비스, 티켓, 조항). 확정적인 답이다
    MCPToolError      Tool이 실패했거나 인자가 잘못됐다
    MCPTimeoutError   재시도를 다 써도 시간 안에 응답이 없다
    MCPConnectionError 서버와 연결이 끊겼다
5단계 서브에이전트는 이 오류를 잡아 해당 결과를 "확인 불가"로 두고 진행한다.
"""

import asyncio
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Any, Self, TypeVar

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError as SDKMCPError

from app.agent.retry import with_retry
from app.config import PROJECT_ROOT
from app.mcp_server.models import CalendarResult, ServiceInfo, WindowCheckResult
from app.schemas import ChangeRequest, ChangeType, Incident, PolicyCheckResult, Violation

T = TypeVar("T")

DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 2
DEFAULT_BASE_DELAY = 0.3

# 서버는 LLM을 쓰지 않으므로 키 종류는 자식 프로세스에 넘기지 않는다 (CLAUDE.md 절대 규칙 5)
_SECRET_ENV = re.compile(r"API_KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)


class MCPClientError(Exception):
    """MCP 호출 실패의 공통 부모."""


class MCPNotFoundError(MCPClientError):
    """조회 대상이 없다."""


class MCPToolError(MCPClientError):
    """Tool이 실패했거나 인자가 잘못됐다."""


class MCPTimeoutError(MCPClientError):
    """재시도를 다 써도 시간 안에 응답이 없다."""


class MCPConnectionError(MCPClientError):
    """서버와 연결이 끊겼다."""


def server_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """자식 서버 프로세스에 넘길 환경 변수. 키 종류는 뺀다."""
    source = os.environ if environ is None else environ
    env = {k: v for k, v in source.items() if not _SECRET_ENV.search(k)}
    env["FASTMCP_SHOW_SERVER_BANNER"] = "false"
    env.setdefault("FASTMCP_LOG_LEVEL", "WARNING")  # 시작 INFO 로그는 끄고 경고와 오류만 남긴다
    return env


def stdio_transport() -> StdioTransport:
    # keep_alive=False: 연결을 닫으면 서버 프로세스도 같이 끝난다
    return StdioTransport(
        command=sys.executable,
        args=["-m", "app.mcp_server.server"],
        env=server_env(),
        cwd=str(PROJECT_ROOT),
        keep_alive=False,
    )


class MCPClient:
    def __init__(
        self,
        transport: FastMCP | StdioTransport | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
    ) -> None:
        """transport가 None이면 서버를 stdio 서브프로세스로 띄운다. 테스트는 FastMCP 인스턴스를 넘겨 메모리 내 연결을 쓴다."""
        self._transport = transport
        self.timeout = timeout
        self.retries = retries
        self.base_delay = base_delay
        self.total_retries = 0
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None

    # ------------------------------------------------------------ 수명

    async def start(self) -> None:
        if self._client is not None:
            return
        stack = AsyncExitStack()
        client = Client(self._transport if self._transport is not None else stdio_transport())
        try:
            await stack.enter_async_context(client)
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._client = stack, client

    async def stop(self) -> None:
        if self._stack is not None:
            stack, self._stack, self._client = self._stack, None, None
            await stack.aclose()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected()

    def _require(self) -> Client:
        if self._client is None:
            raise MCPClientError("MCP 클라이언트가 시작되지 않았다. start()를 먼저 호출한다")
        return self._client

    # ------------------------------------------------------------ 공통 호출 (타임아웃, 재시도, 오류 변환)

    async def _run(
        self,
        label: str,
        op: Callable[[Client], Awaitable[T]],
        *,
        timeout: float | None,
        metrics: dict[str, int] | None,
    ) -> T:
        client = self._require()
        limit = self.timeout if timeout is None else timeout
        attempts = 0

        async def attempt() -> T:
            nonlocal attempts
            attempts += 1
            return await asyncio.wait_for(op(client), limit)

        try:
            result, _ = await with_retry(attempt, retries=self.retries, base_delay=self.base_delay)
            return result
        except asyncio.TimeoutError as exc:
            raise MCPTimeoutError(f"{label}: {limit}초 안에 응답이 없다 (재시도 {self.retries}회 소진)") from exc
        except ConnectionError as exc:
            raise MCPConnectionError(f"{label}: 서버와 연결이 끊겼다 ({exc})") from exc
        except ToolError as exc:
            text = str(exc)
            raise (MCPNotFoundError if text.startswith("NOT_FOUND") else MCPToolError)(text) from exc
        except SDKMCPError as exc:
            text = str(exc)
            raise (MCPNotFoundError if "not found" in text.lower() else MCPClientError)(text) from exc
        finally:
            retried = attempts - 1
            if retried > 0:
                self.total_retries += retried
                if metrics is not None:
                    metrics["tool_retries"] = metrics.get("tool_retries", 0) + retried

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float | None = None,
        metrics: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Tool을 호출하고 구조화된 응답(dict)을 돌려준다."""
        result = await self._run(
            f"Tool {name}", lambda c: c.call_tool(name, arguments), timeout=timeout, metrics=metrics
        )
        if result.structured_content is None:
            raise MCPToolError(f"Tool {name}: 구조화된 응답이 없다")
        return result.structured_content

    async def read_resource(
        self, uri: str, *, timeout: float | None = None, metrics: dict[str, int] | None = None
    ) -> str:
        contents = await self._run(
            f"Resource {uri}", lambda c: c.read_resource(uri), timeout=timeout, metrics=metrics
        )
        text = getattr(contents[0], "text", None) if contents else None
        if text is None:
            raise MCPClientError(f"Resource {uri}: 텍스트 내용이 없다")
        return text

    # ------------------------------------------------------------ 타입이 있는 Tool

    async def check_policy(
        self, request: ChangeRequest, submitted_at: datetime, *, metrics: dict[str, int] | None = None
    ) -> PolicyCheckResult:
        data = await self.call_tool(
            "check_policy",
            {"request": request.model_dump(mode="json"), "submitted_at": submitted_at.isoformat()},
            metrics=metrics,
        )
        return PolicyCheckResult.model_validate(data)

    async def check_window(
        self,
        target: str,
        change_type: ChangeType,
        start: datetime,
        end: datetime,
        submitted_at: datetime,
        *,
        metrics: dict[str, int] | None = None,
    ) -> list[Violation]:
        data = await self.call_tool(
            "check_window",
            {
                "target": target,
                "change_type": change_type,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "submitted_at": submitted_at.isoformat(),
            },
            metrics=metrics,
        )
        return WindowCheckResult.model_validate(data).violations

    async def get_service_info(self, service_id: str, *, metrics: dict[str, int] | None = None) -> ServiceInfo:
        data = await self.call_tool("get_service_info", {"service_id": service_id}, metrics=metrics)
        return ServiceInfo.model_validate(data)

    async def get_calendar(
        self, target: str, start: datetime, end: datetime, *, metrics: dict[str, int] | None = None
    ) -> CalendarResult:
        data = await self.call_tool(
            "get_calendar",
            {"target": target, "start": start.isoformat(), "end": end.isoformat()},
            metrics=metrics,
        )
        return CalendarResult.model_validate(data)

    async def get_incident(self, ticket: str, *, metrics: dict[str, int] | None = None) -> Incident:
        data = await self.call_tool("get_incident", {"ticket": ticket}, metrics=metrics)
        return Incident.model_validate(data)

    # ------------------------------------------------------------ 규정 원문 Resource

    async def read_rule(self, rule_id: str, *, metrics: dict[str, int] | None = None) -> str:
        """조항 원문. 예: read_rule("CHG-03")."""
        return await self.read_resource(f"rules://{rule_id}", metrics=metrics)

    async def read_standard(self, *, metrics: dict[str, int] | None = None) -> str:
        return await self.read_resource("rules://standard", metrics=metrics)

    async def list_rule_uris(self) -> list[str]:
        resources = await self._run("list_resources", lambda c: c.list_resources(), timeout=None, metrics=None)
        return [str(r.uri) for r in resources]
