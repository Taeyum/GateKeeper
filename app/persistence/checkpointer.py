"""사람 승인(HITL) 대기를 서버 재시작 뒤에도 이어 가는 SQLite 체크포인터 (docs/SPEC.md 6장).

interrupt()로 멈춘 그래프는 체크포인트에 상태가 저장돼 있어야 다른 프로세스가 같은 thread_id로 재개할 수 있다.
그래서 요청의 진행 상태는 이 체크포인터가 유일한 원천이고, thread_id는 request_id다.

상태에는 pydantic 모델(ChangeRequest, Recommendation, SubagentResult ...)이 들어 있다. LangGraph는 허용 목록에 없는
타입을 읽을 때 경고하고(다음 버전에서는 막는다), 그래서 직렬화 허용 목록을 만들어 넘긴다. 목록은 GKState의 주석에서
모델을 모아 만들기 때문에 상태에 모델을 더해도 여기를 고칠 일이 없다.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin, get_type_hints

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from app.agent.state import GKState

MEMORY = ":memory:"


def thread_config(request_id: str) -> dict[str, Any]:
    """그래프 실행 설정의 thread_id는 request_id다. 요청 하나가 thread 하나이고, 재요청은 새 request_id를 쓴다."""
    return {"configurable": {"thread_id": request_id}}


def _collect(annotation: Any, found: set[type[BaseModel]]) -> None:
    """주석 안에서 pydantic 모델을 찾는다. Annotated, 리스트, union은 안으로 들어가고 모델은 필드까지 따라간다."""
    origin = get_origin(annotation)
    if origin is Annotated:
        _collect(get_args(annotation)[0], found)
    elif origin is not None:
        for argument in get_args(annotation):
            _collect(argument, found)
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel) and annotation not in found:
        found.add(annotation)
        for field in annotation.model_fields.values():
            _collect(field.annotation, found)


def state_models() -> list[type[BaseModel]]:
    """GKState에 들어가는 pydantic 모델(필드 안에 중첩된 것 포함)."""
    found: set[type[BaseModel]] = set()
    for annotation in get_type_hints(GKState, include_extras=True).values():
        _collect(annotation, found)
    return sorted(found, key=lambda model: (model.__module__, model.__name__))


def state_allowlist() -> list[tuple[str, str]]:
    return [(model.__module__, model.__name__) for model in state_models()]


def make_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=state_allowlist())


@asynccontextmanager
async def open_checkpointer(path: str | Path = MEMORY) -> AsyncIterator[AsyncSqliteSaver]:
    """SQLite 파일(없으면 폴더째 만든다)에 체크포인트를 저장하는 saver를 연다. 닫으면 연결도 닫힌다.

    from_conn_string은 직렬화기를 받지 않아서 연결을 직접 열고 허용 목록을 넣는다.
    """
    if str(path) != MEMORY:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path)) as connection:
        saver = AsyncSqliteSaver(connection, serde=make_serde())
        await saver.setup()
        yield saver


async def list_thread_ids(saver: AsyncSqliteSaver) -> list[str]:
    """체크포인트가 있는 thread_id를 최신 순으로, 중복 없이. 서버가 시작할 때 요청 저장소를 복구하는 데 쓴다.

    서브그래프도 같은 thread_id로 체크포인트를 남기므로(checkpoint_ns만 다르다) 첫 등장 순서로 걸러 낸다.
    """
    seen: dict[str, None] = {}
    async for checkpoint in saver.alist(None):
        seen.setdefault(checkpoint.config["configurable"]["thread_id"], None)
    return list(seen)
