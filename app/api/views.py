"""그래프 상태 스냅샷(values)을 API 응답 모양으로 바꾼다 (docs/API_CONTRACT.md).

순수 함수만 둔다. 체크포인터를 읽는 일은 호출하는 쪽이 하고, 여기는 받은 값을 계약의 모양으로 옮긴다. 필드 이름은
app/schemas.py와 데이터 파일의 원래 키를 그대로 쓴다. 값이 없을 수 있는 필드는 키를 빼지 않고 null로 보낸다.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.api.alternative import alternative_diff
from app.api.store import RequestRecord
from app.clock import now_iso
from app.schemas import KST, RulesConfig

RISK_RANK = {"높음": 0, "중간": 1, "낮음": 2}
# change_calendar.json의 scheduled_changes 항목에서 화면에 주는 키
SCHEDULED_CHANGE_KEYS = ("schedule_id", "target", "change_type", "title", "start", "end", "owner")
IMPACT_KEYS = ("id", "name", "tier", "owner_team", "depends_on", "dependents")
SUBAGENT_OF_NODE = {"policy_agent": "policy", "impact_agent": "impact", "history_agent": "history"}


def compose_text(parent_text: str | None, new_text: str) -> str:
    """재요청이면 부모의 마스킹 본문과 새 본문을 합쳐 parse_request에 넣을 문장을 만든다. 프롬프트 파일은 건드리지 않는다.

    합친 문장은 그래프 입력(masked_text)에만 쓰고, 응답의 text에는 이번에 새로 쓴 본문만 보낸다.
    """
    if not parent_text:
        return new_text
    return f"[이전 요청]\n{parent_text}\n\n[이번에 추가하거나 고친 내용: 이전 요청과 다르면 이번 내용이 맞다]\n{new_text}"


def _iso(value) -> str | None:
    return value.astimezone(KST).isoformat() if value is not None else None


def structured_view(values: Mapping[str, Any]) -> dict[str, Any] | None:
    structured = values.get("structured")
    return structured.model_dump(mode="json") if structured is not None else None


def recommendation_view(values: Mapping[str, Any]) -> dict[str, Any] | None:
    recommendation = values.get("recommendation")
    return recommendation.model_dump(mode="json") if recommendation is not None else None


def approval_view(values: Mapping[str, Any]) -> dict[str, Any] | None:
    approval = values.get("approval")
    return approval.model_dump(mode="json") if approval is not None else None


def impact_view(values: Mapping[str, Any]) -> dict[str, Any] | None:
    """impact_agent의 Tool 원본(서비스와 같은 시간대 예정 변경)을 계약의 impact로 옮긴다. 표준 경로나 확인 불가면 null."""
    impact = values.get("impact")
    if impact is None or impact.status != "ok" or not impact.tool_output:
        return None
    service = impact.tool_output.get("service")
    if not service:
        return None
    calendar = impact.tool_output.get("calendar") or {}
    return {
        **{key: service[key] for key in IMPACT_KEYS},
        "same_time_changes": [{key: item[key] for key in SCHEDULED_CHANGE_KEYS if key in item} for item in calendar.get("scheduled_changes", [])],
    }


def unavailable_errors(values: Mapping[str, Any]) -> list[str]:
    """서브에이전트가 끝내 실패한 것을 사용자용 문장("영향도: 확인 불가")으로. 상태의 errors에는 예외 타입 이름이 들어 있어 쓰지 않는다."""
    return [
        sub.summary
        for key in SUBAGENT_OF_NODE.values()
        if (sub := values.get(key)) is not None and sub.status == "unavailable"
    ]


def recommendation_payload(values: Mapping[str, Any], rules: RulesConfig) -> dict[str, Any]:
    """recommendation 이벤트와 상세 응답이 같이 쓰는 묶음."""
    recommendation = values.get("recommendation")
    diff = None
    if recommendation is not None:
        diff = alternative_diff(
            verdict=recommendation.verdict,
            rule_ids=[v.rule_id for v in recommendation.violations],
            structured=values.get("structured"),
            plan=values.get("alternative"),
            rules=rules,
        )
    return {
        "recommendation": recommendation_view(values),
        "alternative_diff": diff,
        "impact": impact_view(values),
        "similar_incidents": list(values.get("similar_incidents", [])),
        "has_similar_incident": bool(values.get("has_similar_incident", False)),
    }


def summary(record: RequestRecord, values: Mapping[str, Any]) -> dict[str, Any]:
    """RequestSummary. 목록 두 개(/api/requests, /api/approvals)가 같은 모양을 쓴다."""
    structured = values.get("structured")
    recommendation = values.get("recommendation")
    return {
        "request_id": record.request_id,
        "parent_request_id": record.parent_request_id,
        "requester": record.requester,
        "submitted_at": record.submitted_at,
        "target": structured.target if structured is not None else None,
        "change_type": structured.change_type if structured is not None else None,
        "start": _iso(structured.start) if structured is not None else None,
        "status": record.status,
        "verdict": recommendation.verdict if recommendation is not None else None,
        "risk": recommendation.risk if recommendation is not None else None,
        "has_similar_incident": bool(values.get("has_similar_incident", False)),
        "standard_change_id": values.get("standard_change_id"),
        "decided_at": values.get("decided_at"),
    }


def detail(record: RequestRecord, values: Mapping[str, Any], *, version: str, rules: RulesConfig) -> dict[str, Any]:
    """GET /api/requests/{id}. 화면의 새로 고침과 SSE 복구 경로이고 승인자 상세 화면의 데이터다."""
    return {
        "request_id": record.request_id,
        "parent_request_id": record.parent_request_id,
        "status": record.status,
        "version": version,
        "requester": record.requester,
        "submitted_at": record.submitted_at,
        "text": record.own_text,
        "route": values.get("route"),
        "nodes": [run.as_dict() for run in record.nodes.values()],
        "structured": structured_view(values),
        **recommendation_payload(values, rules),
        "approval": approval_view(values),
        "errors": unavailable_errors(values),
    }


def terminal_event(record: RequestRecord, values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """서버를 다시 띄운 뒤에는 이벤트 기록이 없다. 체크포인터 상태로 지금의 종료 이벤트 하나를 만든다."""
    if record.status == "awaiting_approval":
        return "awaiting_approval", {"request_id": record.request_id, "status": "awaiting_approval", "at": now_iso()}
    return "done", {
        "request_id": record.request_id,
        "status": record.status,
        "at": values.get("decided_at") or now_iso(),
        "approval": approval_view(values),
        "message": record.message if record.status == "manual_review" else None,
    }


def sort_approvals(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """위험도 높음 > 중간 > 낮음(risk가 null이면 맨 뒤), 같으면 유사 장애가 있는 것이 먼저, 그다음 제출 시각 최신순."""
    newest_first = sorted(items, key=lambda item: item["submitted_at"], reverse=True)
    return sorted(newest_first, key=lambda item: (RISK_RANK.get(item["risk"], len(RISK_RANK)), not item["has_similar_incident"]))
