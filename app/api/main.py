"""FastAPI 앱 (docs/SPEC.md 6장).

lifespan에서 MCP 클라이언트, 체크포인터, 그래프, 요청 저장소를 한 번만 만든다. 그래프 compile과 MCP 서브프로세스는 비싸고,
체크포인터 연결은 하나만 있어야 interrupt 상태가 갈라지지 않는다. 요청마다 만들면 안 되는 것들이라 서버가 뜰 때 만들어
Container로 묶어 두고 모든 요청이 나눠 쓴다. 종료는 만든 역순이다(AsyncExitStack).

실행: python -m uvicorn app.api.main:create_app --factory (sh/run_api.sh). 앱을 import할 때가 아니라 서버가 뜰 때 설정을 읽는다.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.graph import build_graph
from app.agent.mcp_client import MCPClient
from app.api.deps import Container
from app.api.errors import install_error_handlers
from app.api.middleware import MaskingMiddleware
from app.api.routes import router
from app.api.runner import Runner
from app.api.store import RequestStore, rehydrate
from app.audit import AuditLog
from app.config import Settings, get_settings
from app.persistence.checkpointer import open_checkpointer
from app.rag import open_history_index
from app.rag.index import IndexNotReady

logger = logging.getLogger(__name__)


async def ensure_rag_ready(settings: Settings, client: MCPClient) -> None:
    """v2는 이력 색인과 임베딩이 준비돼야 한다. 아니면 서버를 띄우지 않는다.

    색인이 없을 때 history_agent는 조용히 "확인 불가"로 끝나서 v2가 이력 없이 v1처럼 동작한다. 서버가 뜬 뒤에는 아무도 그것을
    보지 못하므로 시작 때 막는다. 이어서 검색을 한 번 해서 MCP 서버 쪽 색인과 임베딩 모델을 미리 올려 둔다(첫 요청이 느리지 않게).
    """
    try:
        await asyncio.to_thread(open_history_index(settings).ready)
    except IndexNotReady as exc:
        raise RuntimeError(f"RAG 색인이 준비되지 않았다: {exc}") from None
    except Exception as exc:
        raise RuntimeError(
            f"RAG 임베딩을 쓸 수 없다 ({type(exc).__name__}). ollama serve가 떠 있고 {settings.embed_model}이 설치돼 있는지 확인한다."
        ) from None
    await client.search_history("준비 확인", k=1)


def create_app(
    *,
    mcp: MCPClient | None = None,
    checkpoint_path: Path | None = None,
    audit_path: Path | None = None,
    settings: Settings | None = None,
    check_rag: bool | None = None,
) -> FastAPI:
    """테스트는 메모리 MCP와 임시 경로를 넘긴다. check_rag의 기본값은 MCP를 직접 만들 때만 True다(주입한 MCP의 색인은 설정과 무관하다)."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            client = mcp or MCPClient()
            await stack.enter_async_context(client)
            saver = await stack.enter_async_context(open_checkpointer(checkpoint_path or settings.checkpoint_db))
            audit = AuditLog(audit_path or settings.audit_log)
            graph = build_graph(settings.gk_version, mcp=client, checkpointer=saver, audit=audit, approval=True)
            if settings.gk_version == "v2" and (check_rag if check_rag is not None else mcp is None):
                await ensure_rag_ready(settings, client)

            store = RequestStore()
            closed = await rehydrate(store, graph, saver, audit)
            if closed:
                logger.warning("실행 도중 꺼졌던 요청 %d건을 수동 검토로 닫았다", len(closed))
            runner = Runner(graph, audit, timeout_s=settings.gk_run_timeout_s)
            app.state.container = Container(settings=settings, audit=audit, graph=graph, store=store, runner=runner)
            try:
                yield
            finally:
                await runner.shutdown()  # 진행 중인 그래프 task를 먼저 멈춘 뒤 체크포인터와 MCP를 닫는다

    app = FastAPI(title="GateKeeper API", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(MaskingMiddleware)
    # CORS는 가장 바깥에 둔다(마지막에 더한 미들웨어가 바깥이다). 화면 주소만 허용한다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["X-User-Role", "X-User-Name", "Content-Type"],
    )
    app.include_router(router)
    return app
