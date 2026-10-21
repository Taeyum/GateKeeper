"""GateKeeper MCP 서버 (FastMCP, stdio).

Resource: 규정 원문 조각 12개 (rules://index, rules://CHG-01~10, rules://standard).
          호출해도 늘 같은 정적 문서이고 URI로 식별된다.
Tool:     check_policy, check_window, get_service_info, get_calendar, get_incident, search_history.
          입력에 따라 계산하거나 조회하는 동적 행위다.

판정 로직은 여기에 없다. check_policy/check_window는 rules_engine 함수를 감싸기만 하고,
나머지 조회는 queries.py의 순수 함수를 감싼다. stdout은 프로토콜 전용이므로 print를 쓰지 않는다.

실행: python -m app.mcp_server.server  (저장소 루트에서)
"""

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from app import rules_engine
from app.data_loader import load_gk_data
from app.mcp_server import queries
from app.mcp_server.models import (
    CalendarResult,
    HistoryHit,
    HistoryOutcome,
    HistorySearchResult,
    ServiceInfo,
    WindowCheckResult,
)
from app.mcp_server.policy_docs import PolicyDocs, load_policy_docs
from app.rag import HistoryIndex, IndexNotReady, open_history_index
from app.schemas import ChangeRequest, ChangeType, GKData, Incident, KstDatetime, PolicyCheckResult

# Tool은 모두 읽기 전용이고 같은 입력에 같은 결과를 돌려준다
_READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "destructiveHint": False}

MAX_HISTORY_K = 10


def _lookup(fn: Callable[..., Any], *args: Any) -> Any:
    """queries의 오류를 클라이언트가 구분할 수 있는 ToolError로 바꾼다.

    NOT_FOUND는 "그런 대상이 없다"는 확정적인 답이고, INVALID_ARGUMENT는 인자 오류다.
    둘 다 다시 시도해도 같은 결과라서 클라이언트는 재시도하지 않는다.
    """
    try:
        return fn(*args)
    except queries.NotFound as exc:
        raise ToolError(f"NOT_FOUND: {exc}") from exc
    except ValueError as exc:
        raise ToolError(f"INVALID_ARGUMENT: {exc}") from exc


def _call_engine(fn: Callable[..., Any], *args: Any) -> Any:
    """규칙 엔진 함수를 부르고, 엔진의 ValueError(작업 구간 오류 등)를 INVALID_ARGUMENT로 바꾼다.

    대상이 CMDB에 없는 경우는 이 함수보다 먼저 NOT_FOUND로 걸러 둔다(queries.require_service).
    그래서 여기서 잡는 ValueError는 대상 오타가 아니라 입력 값 문제이고, 엔진 오류 메시지를
    문자열로 구분할 필요가 없다. ValueError가 아닌 예상 못 한 오류는 그대로 올려서 mask_error_details가 가린다.
    """
    try:
        return fn(*args)
    except ValueError as exc:
        raise ToolError(f"INVALID_ARGUMENT: {exc}") from exc


def _register_resources(mcp: FastMCP, policy: PolicyDocs) -> None:
    def add(uri: str, name: str, description: str, text: str) -> None:
        @mcp.resource(uri, name=name, description=description, mime_type="text/markdown")
        def read() -> str:
            return text

    add("rules://index", "rules-index", "규정 조항 목록 (조항, 제목, 구분, 적용 대상)", policy.index)
    for clause in policy.clauses.values():
        add(f"rules://{clause.rule_id}", clause.rule_id, clause.title, clause.text)
    add("rules://standard", "rules-standard", "표준 변경 목록 (STD-01, STD-02)", policy.standard)


def _register_tools(mcp: FastMCP, data: GKData | None, history_index: HistoryIndex | None) -> None:
    # data가 None이면 규칙 엔진은 gatekeeper_data의 캐시된 값을 쓰고, 조회 Tool도 같은 값을 쓴다.
    def gk() -> GKData:
        return data or load_gk_data()

    # 이력 색인은 search_history를 처음 부를 때 연다. 다른 Tool은 색인이나 Ollama 없이도 동작해야 한다.
    index_holder: dict[str, HistoryIndex | None] = {"index": history_index}

    def history() -> HistoryIndex:
        if index_holder["index"] is None:
            index_holder["index"] = open_history_index()
        return index_holder["index"]

    @mcp.tool(annotations=_READ_ONLY)
    def check_policy(request: ChangeRequest, submitted_at: KstDatetime) -> PolicyCheckResult:
        """구조화된 변경 요청을 규정 CHG-01~10과 대조한다.

        위반 조항(violations), 유효한 긴급 변경으로 면제된 조항(exempted), 긴급 변경 유효 여부,
        표준 변경 해당 여부를 돌려준다. 위반 여부의 유일한 근거는 이 결과다.
        submitted_at은 요청이 접수된 시각이다 (24시간 전 요청 같은 조항의 기준).
        """
        _lookup(queries.require_service, gk(), request.target)  # 없는 대상은 NOT_FOUND
        return _call_engine(rules_engine.check_all, request, submitted_at, data)

    @mcp.tool(annotations=_READ_ONLY)
    def check_window(
        target: str,
        change_type: ChangeType,
        start: KstDatetime,
        end: KstDatetime,
        submitted_at: KstDatetime,
    ) -> WindowCheckResult:
        """주어진 작업 시간대가 규정에 걸리는지 확인한다. 조건부 대안 일정을 검증할 때 쓴다."""
        if end <= start:
            raise ToolError("INVALID_ARGUMENT: end는 start보다 늦어야 한다")
        _lookup(queries.require_service, gk(), target)  # 없는 대상은 NOT_FOUND
        violations = _call_engine(rules_engine.check_window, target, change_type, start, end, submitted_at, data)
        return WindowCheckResult(violations=violations)

    @mcp.tool(annotations=_READ_ONLY)
    def get_service_info(service_id: str) -> ServiceInfo:
        """서비스의 등급(Tier), 담당 팀, 의존/피의존 서비스, 포트, 기본 포트를 조회한다."""
        return _lookup(queries.service_info, gk(), service_id)

    @mcp.tool(annotations=_READ_ONLY)
    def get_calendar(target: str, start: KstDatetime, end: KstDatetime) -> CalendarResult:
        """작업 구간과 겹치는 동결 기간과 같은 대상의 예정 변경을 조회한다. 구간은 [start, end)다."""
        return _lookup(queries.calendar_overlap, gk(), target, start, end)

    @mcp.tool(annotations=_READ_ONLY)
    def get_incident(ticket: str) -> Incident:
        """장애 티켓(INC-연도-번호)의 상태(open/closed)와 영향 받는 서비스를 조회한다."""
        return _lookup(queries.incident_info, gk(), ticket)

    @mcp.tool(annotations=_READ_ONLY)
    def search_history(
        query: str,
        k: int = 3,
        service: str | None = None,
        change_type: ChangeType | None = None,
        outcome: HistoryOutcome | None = None,
    ) -> HistorySearchResult:
        """과거 변경 이력을 의미로 검색해 비슷한 사례 상위 k건(기본 3건)을 점수 순으로 돌려준다.

        기본 호출은 서비스나 유형으로 미리 거르지 않는다. service, change_type, outcome을 주면 그 조건에 맞는
        이력만을 대상으로 유사도 순 상위 k건을 돌려준다(이력 전체에서 "같은 서비스, 같은 유형, 장애"를 정확히 찾을 때 쓴다).
        """
        text = query.strip()
        if not text:
            raise ToolError("INVALID_ARGUMENT: query가 비어 있다")
        if not 1 <= k <= MAX_HISTORY_K:
            raise ToolError(f"INVALID_ARGUMENT: k는 1 이상 {MAX_HISTORY_K} 이하다")
        where = {
            name: value
            for name, value in (("service", service), ("change_type", change_type), ("outcome", outcome))
            if value is not None
        }
        try:
            ranked = history().search(text, k, where or None)
        except IndexNotReady as exc:
            raise ToolError(f"INDEX_NOT_READY: {exc}") from exc
        return HistorySearchResult(hits=[HistoryHit(score=r.score, item=r.item) for r in ranked])


def build_server(
    data: GKData | None = None, docs: PolicyDocs | None = None, history_index: HistoryIndex | None = None
) -> FastMCP:
    """서버를 만든다. data/docs/history_index를 주면 테스트가 가짜 데이터와 색인을 넣을 수 있다."""
    mcp = FastMCP(
        "gatekeeper",
        instructions=(
            "인프라 변경요청 검토용 서버. rules:// Resource는 규정 원문이고, "
            "Tool은 규정 검사와 서비스/일정/장애 티켓 조회, 과거 이력 검색을 한다."
        ),
        # 예상 못 한 내부 오류의 상세(경로, 스택)는 LLM이 보는 응답에 싣지 않는다. 의도한 ToolError는 그대로 전달된다.
        mask_error_details=True,
    )
    _register_resources(mcp, docs or load_policy_docs())
    _register_tools(mcp, data, history_index)
    return mcp


def main() -> None:
    build_server().run(show_banner=False)


if __name__ == "__main__":
    main()
