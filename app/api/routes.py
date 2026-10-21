"""엔드포인트 6개 (docs/API_CONTRACT.md). 계약의 경로, 권한, 응답 모양을 그대로 따른다.

라우트는 얇게 둔다. 그래프 실행과 이벤트 변환은 러너, 응답 모양은 views, 권한은 deps의 Depends가 맡는다.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from app.agent.results import ApprovalRecord
from app.api import views
from app.api.deps import Container, User, any_role, approver_only, get_container, requester_only
from app.api.errors import ApiError, conflict, not_found
from app.api.events import format_event
from app.api.store import RequestRecord
from app.clock import now_iso
from app.data_loader import load_rules

router = APIRouter(prefix="/api")

Current = Annotated[Container, Depends(get_container)]


class CreateRequestBody(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    # 반려 뒤 재요청이면 이전 요청 번호. 같은 request_id를 다시 쓰지 않고 새 요청을 만든다.
    parent_request_id: str | None = None


class DecisionBody(BaseModel):
    """approver를 받지 않는다. 승인자는 X-User-Name 헤더에서 서버가 채우고, 본문에 와도 무시한다(PROGRESS 0단계 결정)."""

    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=1000)


def _find(container: Container, request_id: str) -> RequestRecord:
    record = container.store.get(request_id)
    if record is None:
        raise not_found()
    return record


@router.post("/requests", status_code=202)
async def create_request(body: CreateRequestBody, user: Annotated[User, Depends(requester_only)], container: Current) -> dict[str, Any]:
    """요청 접수. 그래프는 백그라운드로 돌고 응답은 바로 돌아온다."""
    parent_text = None
    if body.parent_request_id is not None:
        parent = container.store.get(body.parent_request_id)
        if parent is None:
            raise not_found("이전 요청을 찾을 수 없어요.")
        # 부모의 그래프 입력(masked_text)을 쓴다. 부모가 이미 재요청이면 own_text에는 새로 쓴 부분만 있어 원 요청의 대상이 빠진다.
        # 부모의 체크포인트가 아직 없으면(방금 접수됨) own_text로 대신한다.
        parent_text = (await container.values_of(parent.request_id)).get("masked_text") or parent.own_text

    request_id = container.store.new_id()
    submitted_at = now_iso()
    record = RequestRecord(
        request_id=request_id,
        requester=user.name,
        submitted_at=submitted_at,
        own_text=body.text,
        parent_request_id=body.parent_request_id,
    )
    container.store.add(record)
    container.runner.start(
        record,
        {
            "request_id": request_id,
            "parent_request_id": body.parent_request_id,
            "requester": user.name,
            "submitted_at": submitted_at,
            # LLM에는 부모 본문과 합친 문장이 가고, 응답의 text에는 이번에 새로 쓴 본문(request_text)만 나간다.
            # raw_text는 넣지 않는다. 마스킹 미들웨어가 앞에서 가려서 원문이 여기까지 오지 않는다.
            "masked_text": views.compose_text(parent_text, body.text),
            "request_text": body.text,
            "trace_id": uuid.uuid4().hex,
        },
    )
    return {"request_id": request_id, "parent_request_id": body.parent_request_id, "status": "reviewing"}


@router.get("/requests")
async def list_requests(user: Annotated[User, Depends(any_role)], container: Current) -> dict[str, Any]:
    """요청자는 본인 요청만, 승인자는 전부. 최신순. 데모는 헤더를 믿는다(실서비스는 SSO로 소유자를 확인해야 한다)."""
    records = [r for r in container.store.newest_first() if user.role == "approver" or r.requester == user.name]
    return {"items": [views.summary(r, await container.values_of(r.request_id)) for r in records]}


@router.get("/requests/{request_id}")
async def get_request(request_id: str, user: Annotated[User, Depends(any_role)], container: Current) -> dict[str, Any]:
    record = _find(container, request_id)
    return views.detail(record, await container.values_of(request_id), version=container.settings.gk_version, rules=load_rules())


@router.get("/requests/{request_id}/stream")
async def stream_request(request_id: str, container: Current) -> StreamingResponse:
    """SSE. EventSource가 헤더를 못 붙여서 역할 확인이 없다(읽기 전용). 연결하면 지금까지의 이벤트를 다시 보내고 이어서 보낸다."""
    record = _find(container, request_id)

    async def live():
        async for chunk in record.log.stream():
            yield chunk

    async def after_restart():
        # 서버를 다시 띄운 뒤에는 이벤트 기록이 없다. 체크포인터 상태로 지금의 종료 이벤트 하나만 보내고 닫는다.
        name, data = views.terminal_event(record, await container.values_of(request_id))
        yield format_event(name, data)

    replay = record.replayable and (bool(record.log.events) or record.status == "reviewing")
    return StreamingResponse(
        live() if replay else after_restart(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/approvals")
async def list_approvals(
    user: Annotated[User, Depends(approver_only)],
    container: Current,
    status: Literal["awaiting_approval", "auto_approved"] = "awaiting_approval",
) -> dict[str, Any]:
    """승인자의 결재함. status=auto_approved면 자동 승인 기록(별도 엔드포인트 없이 같은 경로)."""
    items = [views.summary(r, await container.values_of(r.request_id)) for r in container.store.newest_first() if r.status == status]
    return {"items": views.sort_approvals(items)}


@router.post("/requests/{request_id}/decision")
async def decide_request(request_id: str, body: DecisionBody, user: Annotated[User, Depends(approver_only)], container: Current) -> dict[str, Any]:
    """승인자의 결정. 같은 thread를 이어서 실행한다. 요청별 잠금 안에서 확인하고 재개해 두 사람이 겹쳐 결정하지 못한다."""
    record = _find(container, request_id)
    async with record.lock:
        if record.status != "awaiting_approval":
            raise conflict("승인 대기 중인 요청이 아니에요.")
        approval = ApprovalRecord(decision=body.decision, approver=user.name, comment=body.comment, at=now_iso())
        await container.runner.resume(record, approval)
        if record.status not in ("approved", "rejected"):
            raise ApiError(500, "internal")  # 재개 중 오류. 러너가 이미 수동 검토로 닫았다
    return {"request_id": request_id, "status": record.status, "approval": approval.model_dump()}
