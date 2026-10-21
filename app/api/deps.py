"""API 의존성: lifespan이 만든 싱글턴과 역할 확인 (docs/SPEC.md 6장).

역할 확인은 미들웨어가 아니라 FastAPI Depends로 라우트마다 건다. 이유: 권한이 라우트 시그니처에 드러나고 OpenAPI 문서에
보이며, 테스트가 dependency_overrides로 바꿔 끼울 수 있고, 경로 문자열 매칭에 기대지 않아 새 라우트가 권한 없이 열리는
실수가 줄어든다. stream은 EventSource가 헤더를 못 붙이므로 역할 확인을 걸지 않는다(읽기 전용, 계약).

데모용으로 X-User-Role과 X-User-Name 헤더를 믿는다(실서비스는 SSO). 이름은 HTTP 헤더에 한글을 못 넣어서 화면이 퍼센트
인코딩해 보낸다.
"""

import logging
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from fastapi import Header, Request

from app.api.errors import forbidden
from app.api.runner import Runner
from app.api.store import RequestStore
from app.audit import AuditLog, forbidden_record
from app.config import Settings
from app.persistence.checkpointer import thread_config

logger = logging.getLogger(__name__)

Role = Literal["requester", "approver"]
MAX_NAME_LENGTH = 100


@dataclass
class Container:
    """lifespan이 한 번 만들어 app.state에 두는 싱글턴 묶음. 요청마다 만들지 않는다.

    그래프 compile, MCP 서브프로세스, SQLite 연결은 비싸거나 하나만 있어야 하는 것들이라 서버가 뜰 때 한 번 만들고 모든 요청이 나눠 쓴다.
    """

    settings: Settings
    audit: AuditLog
    graph: Any
    store: RequestStore
    runner: Runner

    async def values_of(self, request_id: str) -> dict[str, Any]:
        """체크포인터에 저장된 요청의 지금 상태. 요약과 상세 응답은 항상 여기서 읽는다(진실의 원천 하나)."""
        return (await self.graph.aget_state(thread_config(request_id))).values


@dataclass(frozen=True)
class User:
    role: Role
    name: str


def get_container(request: Request) -> Container:
    return request.app.state.container


def decode_name(header: str | None) -> str:
    """퍼센트 인코딩된 UTF-8 이름을 푼다. 비었거나 너무 길거나 제어 문자가 있으면 빈 문자열이다(= 신원 없음)."""
    if not header:
        return ""
    name = unquote(header).strip()
    if len(name) > MAX_NAME_LENGTH or any(ord(ch) < 32 for ch in name):
        return ""
    return name


def require_role(*allowed: Role):
    """allowed 중 하나의 역할과 이름이 있는 요청만 통과시킨다. 아니면 403이고 감사 로그에 남긴다."""

    async def check(
        request: Request,
        x_user_role: Annotated[str | None, Header()] = None,
        x_user_name: Annotated[str | None, Header()] = None,
    ) -> User:
        name = decode_name(x_user_name)
        if x_user_role in allowed and name:
            return User(role=x_user_role, name=name)  # type: ignore[arg-type]
        try:
            await get_container(request).audit.append(
                "forbidden",
                forbidden_record(role=x_user_role, name=name or None, method=request.method, path=request.url.path),
            )
        except OSError as exc:  # 기록 실패가 403을 막지 않는다
            logger.error("감사 로그를 쓰지 못했다 (%s): forbidden", type(exc).__name__)
        raise forbidden()

    return check


requester_only = require_role("requester")
approver_only = require_role("approver")
any_role = require_role("requester", "approver")
