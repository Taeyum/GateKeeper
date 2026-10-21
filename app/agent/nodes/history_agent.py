"""③ history_agent: 이력 검토 서브에이전트 (docs/SPEC.md 3장, docs/plans/stage6.md).

policy_agent, impact_agent와 같은 모양의 컴파일된 서브그래프다.

    search → (결과가 있으면) summarize

- search: 같은 질의로 MCP search_history를 두 번 동시에 부른다. 어떤 Tool을 부를지 LLM이 고르지 않고 코드가 정한다.
    (A) 정확 일치: 같은 서비스 + 같은 (실효) 유형 + outcome 장애인 이력만을 대상으로 한 검색. 조건이 순위 전에
        이력 전체에 적용되므로 의미 검색 순위에 좌우되지 않는다.
    (B) 의미 검색: 필터 없는 상위 3건.
  has_similar_incident는 A가 한 건이라도 있는지다. 위험도를 한 단계 올리는 근거이고 코드가 이력 전체에서 정한다
  (상향은 결정론). similar_incidents는 A를 먼저(유사도 순), 남는 자리를 B로 채운 상위 3건이다(사례 제시는 하이브리드 검색).
  판정은 바꾸지 않는다.
- summarize: LLM이 찾은 사례를 두세 문장으로 요약한다. 실패하면 코드가 만든 문장으로 채운다.

실패: 정확 일치 검색(A)이 끝내 실패하면 이력을 확인하지 못한 것이다(status=unavailable). 이력 상향 없이 진행하고
decide가 경고를 붙인다. 의미 검색(B)만 실패하면 정확 일치 결과만으로 진행한다.
"""

import asyncio
import operator
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.agent.mcp_client import MCPClient, MCPClientError
from app.agent.prompting import load_prompt
from app.agent.results import SubagentResult
from app.agent.state import GKState, add_counters
from app.config import get_settings
from app.data_loader import load_cmdb
from app.llm.factory import get_llm, structured_llm
from app.mcp_server.models import HistoryHit
from app.rules_engine._format import CHANGE_TYPE_KO, WEEKDAY_KO
from app.rules_engine.context import effective_change_type
from app.schemas import ChangeRequest, ChangeType, StandardId

HISTORY_TOP_K = 3
INCIDENT_OUTCOME = "장애"  # history.json의 outcome 값. 이 결과의 이력이 "비슷한 장애"다


class HistoryNote(BaseModel):
    """summarize가 LLM에게 받는 출력. 선택 필드가 없어 anthropic에도 그대로 보낸다."""

    summary: str = Field(description="비슷한 과거 사례를 한국어 두세 문장으로 요약")


class HistoryAgentState(TypedDict, total=False):
    # 입력 (부모 상태에서 래퍼가 복사한다). 표준 변경 확정 여부로 실효 유형을 정한다.
    structured: ChangeRequest
    standard_change_id: StandardId | None
    # 서브그래프 안에서만 쓰는 값
    exact_ids: list[str]
    merged: list[HistoryHit]
    # 출력
    result: SubagentResult
    similar_incidents: list[dict[str, Any]]
    has_similar_incident: bool
    errors: Annotated[list[str], operator.add]
    metrics: Annotated[dict[str, int], add_counters]


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


def build_query(request: ChangeRequest, effective_type: ChangeType) -> str:
    """구조화된 요청을 검색 질의 문장으로 조립한다. 같은 요청이면 늘 같은 문장이다.

    이력의 title + description과 의미가 겹치도록 서비스, 유형, 포트, 출발지와 기한, 롤백과 증적, 재시작, 작업 요일/시,
    사유를 짧은 구로 이어 붙인다. 없는 값은 쓰지 않는다. 요청별 예외를 두지 않는 일반 규칙이다.
    """
    service = load_cmdb().get_service(request.target)
    name = f"{service.name} ({request.target})" if service else request.target
    parts = [f"{name} {CHANGE_TYPE_KO[effective_type]}"]
    if request.port is not None:
        parts.append(f"{request.port}번 포트")
    if effective_type == "firewall":
        parts.append(f"출발지 {request.source}" if request.source else "출발지 미지정")
        if request.expires_days is not None:
            parts.append(f"허용 기한 {request.expires_days}일")
        elif request.expires_at is not None:
            parts.append("허용 만료일 지정")
        else:
            parts.append("허용 기한 없음")
    if request.has_rollback is False:
        parts.append("롤백 계획 없음")
    if request.has_test_evidence is False:
        parts.append("테스트 증적 없음")
    if request.restart_required:
        parts.append("재시작 필요")
    if request.start is not None:
        parts.append(f"작업 시각 {WEEKDAY_KO[request.start.weekday()]}요일 {request.start:%H}시")
    if request.reason:
        parts.append(f"사유 {request.reason}")
    return ", ".join(parts) + "."


def merge_hits(exact: list[HistoryHit], semantic: list[HistoryHit], limit: int = HISTORY_TOP_K) -> list[HistoryHit]:
    """정확 일치 건을 먼저(들어온 순서 = 유사도 순), 남는 자리를 의미 검색 결과로 중복 없이 채워 최대 limit건."""
    merged = list(exact[:limit])
    seen = {hit.item.history_id for hit in merged}
    for hit in semantic:
        if len(merged) >= limit:
            break
        if hit.item.history_id not in seen:
            merged.append(hit)
            seen.add(hit.item.history_id)
    return merged


def incident_dicts(hits: list[HistoryHit]) -> list[dict[str, Any]]:
    """docs/API_CONTRACT.md similar_incidents 모양. history.json 항목의 원래 키 그대로다."""
    return [hit.item.model_dump(mode="json") for hit in hits]


def describe_history(merged: list[HistoryHit], exact_ids: list[str]) -> str:
    """코드가 만드는 요약. LLM이 없거나 실패해도 summary가 비지 않게 한다."""
    if exact_ids:
        text = f"같은 서비스와 유형의 장애 이력이 {len(exact_ids)}건 있다({', '.join(exact_ids)})."
    else:
        text = "같은 서비스와 유형의 장애 이력은 없다."
    others = [hit.item.history_id for hit in merged if hit.item.history_id not in exact_ids]
    if others:
        text += f" 참고로 비슷한 사례: {', '.join(others)}."
    return text


def render_history_input(request: ChangeRequest, effective_type: ChangeType, merged: list[HistoryHit], exact_ids: list[str]) -> str:
    lines = []
    for hit in merged:
        item = hit.item
        mark = "[같은 서비스와 유형의 장애] " if item.history_id in exact_ids else ""
        line = f"- {mark}{item.history_id} ({item.service}, {item.change_type}, 결과 {item.outcome}) {item.title}"
        if item.incident is not None:
            line += f" / 영향: {item.incident.impact} / 교훈: {item.incident.lesson}"
        lines.append(line)
    flag = "있음" if exact_ids else "없음"
    return (
        f"요청 대상: {request.target}, 변경 유형: {effective_type}\n"
        f"같은 서비스와 유형의 장애 이력(코드가 이력 전체에서 확인): {flag}\n\n"
        f"## 찾은 과거 사례\n" + ("\n".join(lines) or "- 없음")
    )


# ---------------------------------------------------------------------------
# 서브그래프
# ---------------------------------------------------------------------------


def build_history_subgraph(mcp: MCPClient) -> CompiledStateGraph:
    async def search(state: HistoryAgentState, config: RunnableConfig) -> dict:
        request = state["structured"]
        effective = effective_change_type(request.change_type, state.get("standard_change_id") is not None)
        query = build_query(request, effective)
        metrics: dict[str, int] = {}  # MCP 클라이언트가 tool_retries를 여기에 더한다
        exact_outcome, semantic_outcome = await asyncio.gather(
            mcp.search_history(
                query, HISTORY_TOP_K, service=request.target, change_type=effective, outcome=INCIDENT_OUTCOME, metrics=metrics
            ),
            mcp.search_history(query, HISTORY_TOP_K, metrics=metrics),
            return_exceptions=True,
        )
        for outcome in (exact_outcome, semantic_outcome):
            if isinstance(outcome, BaseException) and not isinstance(outcome, MCPClientError):
                raise outcome

        if isinstance(exact_outcome, MCPClientError):
            return {
                "result": SubagentResult(status="unavailable", summary="이력: 확인 불가"),
                "errors": [f"history_agent: 이력 검색 실패 ({type(exact_outcome).__name__})"],
                "metrics": {**metrics, "partial_failures": 1},
            }
        errors: list[str] = []
        semantic = []
        if isinstance(semantic_outcome, MCPClientError):
            errors.append(f"history_agent: 의미 검색 실패 ({type(semantic_outcome).__name__})")
            metrics["partial_failures"] = 1
        else:
            semantic = semantic_outcome.hits

        exact = exact_outcome.hits
        merged = merge_hits(exact, semantic)
        exact_ids = [hit.item.history_id for hit in exact]
        output = {
            "query": query,
            "has_similar_incident": bool(exact_ids),
            "exact_ids": exact_ids,
            "semantic_ids": [hit.item.history_id for hit in semantic],
            "hits": [{"history_id": hit.item.history_id, "score": round(hit.score, 4)} for hit in merged],
        }
        return {
            "exact_ids": exact_ids,
            "merged": merged,
            "result": SubagentResult(tool_output=output, summary=describe_history(merged, exact_ids)),
            "similar_incidents": incident_dicts(merged),
            "has_similar_incident": bool(exact_ids),
            "errors": errors,
            "metrics": metrics,
        }

    def after_search(state: HistoryAgentState) -> str:
        return "summarize" if state["result"].status == "ok" and state.get("merged") else END

    async def summarize(state: HistoryAgentState, config: RunnableConfig) -> dict:
        request = state["structured"]
        effective = effective_change_type(request.change_type, state.get("standard_change_id") is not None)
        settings = get_settings()
        chain = structured_llm(get_llm("main", settings), HistoryNote, settings)
        messages = [
            SystemMessage(content=load_prompt("history_agent.md")),
            HumanMessage(content=render_history_input(request, effective, state["merged"], state.get("exact_ids", []))),
        ]
        try:
            written = await chain.ainvoke(messages, config)
        except Exception as exc:  # 요약은 부가 정보다. 찾은 사례와 상향 근거는 그대로 두고 코드가 만든 문장을 쓴다
            return {
                "errors": [f"history_agent: 요약 작성 실패 ({type(exc).__name__})"],
                "metrics": {"partial_failures": 1},
            }
        result = state["result"]
        return {"result": result.model_copy(update={"summary": written.summary.strip() or result.summary})}

    graph = StateGraph(HistoryAgentState)
    graph.add_node("search", search)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "search")
    graph.add_conditional_edges("search", after_search, {"summarize": "summarize", END: END})
    graph.add_edge("summarize", END)
    return graph.compile(name="history_agent")


def make_history_agent(mcp: MCPClient):
    """부모 그래프에 붙일 래퍼 노드. 부모 상태에서 입력을 꺼내 서브그래프를 돌리고 결과를 부모 키로 돌려준다."""
    subgraph = build_history_subgraph(mcp)

    async def history_agent(state: GKState, config: RunnableConfig) -> dict:
        # config를 그대로 넘겨야 호출 수 집계 콜백과 LangSmith trace가 서브그래프 안쪽까지 이어진다
        out = await subgraph.ainvoke(
            {"structured": state["structured"], "standard_change_id": state.get("standard_change_id")}, config
        )
        update: dict[str, Any] = {"history": out["result"], "errors": out.get("errors", []), "metrics": out.get("metrics", {})}
        if out["result"].status == "ok":
            update["similar_incidents"] = out.get("similar_incidents", [])
            update["has_similar_incident"] = out.get("has_similar_incident", False)
        return update

    return history_agent
