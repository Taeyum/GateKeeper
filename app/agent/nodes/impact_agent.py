"""③ impact_agent: 영향도 서브에이전트 (docs/SPEC.md 3장).

policy_agent와 같은 모양의 컴파일된 서브그래프다.

    lookup → (조회가 모두 성공하면) summarize

- lookup: MCP get_service_info로 등급, 담당 팀, 의존/피의존 서비스를, get_calendar로 작업 구간과 겹치는 동결 기간과
  예정 변경을 조회한다. 작업 시각이 없으면 캘린더는 조회하지 않는다. 종료 시각이 없으면 rules.json의 기본 작업 시간을 쓴다.
- summarize: LLM이 조회 결과를 두세 문장으로 요약한다. 실패하면 코드가 만든 문장으로 채운다.

조회가 하나라도 끝내 실패하면 "영향도: 확인 불가"로 두고 진행한다(SPEC 부분 실패). 영향도는 판정과 위험도를 바꾸지 않는다.
위험도는 CMDB 등급과 이력으로 decide가 엔진 함수로 계산한다.
"""

import asyncio
import json
import operator
from datetime import datetime, timedelta
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
from app.data_loader import load_rules
from app.llm.factory import get_llm, structured_llm
from app.mcp_server.models import CalendarResult, ServiceInfo
from app.schemas import ChangeRequest, Route

NO_START = "작업 시각이 없어 같은 시간대의 동결 기간과 예정 변경은 확인하지 못했다."


class ImpactNote(BaseModel):
    """summarize가 LLM에게 받는 출력. 선택 필드가 없어 anthropic에도 그대로 보낸다."""

    summary: str = Field(description="영향도를 한국어 두세 문장으로 요약")


class ImpactAgentState(TypedDict, total=False):
    structured: ChangeRequest
    submitted_at: str
    route: Route
    # lookup이 얻은 사실. 조회에 실패했거나 건너뛴 것은 None이다.
    service: ServiceInfo | None
    calendar: CalendarResult | None
    result: SubagentResult
    errors: Annotated[list[str], operator.add]
    metrics: Annotated[dict[str, int], add_counters]


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


def work_window(request: ChangeRequest) -> tuple[datetime, datetime] | None:
    """캘린더를 조회할 작업 구간. 시작이 없으면 None, 종료가 없으면 rules.json의 기본 작업 시간."""
    if request.start is None:
        return None
    if request.end is not None:
        return request.start, request.end
    return request.start, request.start + timedelta(minutes=load_rules().defaults.work_duration_minutes_if_missing)


def tool_output_of(service: ServiceInfo | None, calendar: CalendarResult | None, skipped: str | None) -> dict[str, Any]:
    return {
        "service": service.model_dump(mode="json") if service else None,
        "calendar": calendar.model_dump(mode="json") if calendar else None,
        "calendar_skipped": skipped,
    }


def describe_impact(service: ServiceInfo, calendar: CalendarResult | None) -> str:
    """코드가 만드는 요약. LLM이 실패해도 summary가 비지 않게 한다."""
    parts = [f"{service.id}는 {service.tier}이고 담당 팀은 {service.owner_team}이다."]
    parts.append(f"의존하는 서비스: {', '.join(service.depends_on) or '없음'}.")
    parts.append(f"이 서비스에 의존하는 서비스: {', '.join(service.dependents) or '없음'}.")
    if calendar is None:
        parts.append(NO_START)
    else:
        freezes = ", ".join(f"{f.freeze_id}({f.title})" for f in calendar.freeze_periods) or "없음"
        changes = ", ".join(f"{c.schedule_id}({c.title})" for c in calendar.scheduled_changes) or "없음"
        parts.append(f"작업 구간과 겹치는 동결 기간: {freezes}. 같은 대상의 예정 변경: {changes}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 서브그래프
# ---------------------------------------------------------------------------


def build_impact_subgraph(mcp: MCPClient) -> CompiledStateGraph:
    async def lookup(state: ImpactAgentState, config: RunnableConfig) -> dict:
        request = state["structured"]
        window = work_window(request)
        metrics: dict[str, int] = {}
        calls = [mcp.get_service_info(request.target, metrics=metrics)]
        if window is not None:
            calls.append(mcp.get_calendar(request.target, *window, metrics=metrics))
        outcomes = await asyncio.gather(*calls, return_exceptions=True)

        errors = [f"impact_agent: 조회 실패 ({type(o).__name__})" for o in outcomes if isinstance(o, MCPClientError)]
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, MCPClientError):
                raise outcome
        service = outcomes[0] if not isinstance(outcomes[0], MCPClientError) else None
        calendar = outcomes[1] if window is not None and not isinstance(outcomes[1], MCPClientError) else None
        skipped = NO_START if window is None else None
        output = tool_output_of(service, calendar, skipped)

        if errors:
            return {
                "result": SubagentResult(status="unavailable", tool_output=output, summary="영향도: 확인 불가"),
                "errors": errors,
                "metrics": {**metrics, "partial_failures": len(errors)},
            }
        return {
            "service": service,
            "calendar": calendar,
            "result": SubagentResult(tool_output=output, summary=describe_impact(service, calendar)),
            "metrics": metrics,
        }

    def after_lookup(state: ImpactAgentState) -> str:
        return "summarize" if state["result"].status == "ok" else END

    async def summarize(state: ImpactAgentState, config: RunnableConfig) -> dict:
        result = state["result"]
        settings = get_settings()
        chain = structured_llm(get_llm("main", settings), ImpactNote, settings)
        request = state["structured"]
        messages = [
            SystemMessage(content=load_prompt("impact_agent.md")),
            HumanMessage(
                content=(
                    f"요청 대상: {request.target}, 변경 유형: {request.change_type}\n\n"
                    f"## 조회 결과 (JSON)\n{json.dumps(result.tool_output, ensure_ascii=False, indent=2)}"
                )
            ),
        ]
        try:
            written = await chain.ainvoke(messages, config)
        except Exception as exc:  # 요약은 부가 정보다. 코드가 만든 문장을 그대로 둔다
            return {
                "errors": [f"impact_agent: 요약 작성 실패 ({type(exc).__name__})"],
                "metrics": {"partial_failures": 1},
            }
        return {"result": result.model_copy(update={"summary": written.summary.strip() or result.summary})}

    graph = StateGraph(ImpactAgentState)
    graph.add_node("lookup", lookup)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "lookup")
    graph.add_conditional_edges("lookup", after_lookup, {"summarize": "summarize", END: END})
    graph.add_edge("summarize", END)
    return graph.compile(name="impact_agent")


def make_impact_agent(mcp: MCPClient):
    """부모 그래프에 붙일 래퍼 노드. policy_agent와 같은 방식이다."""
    subgraph = build_impact_subgraph(mcp)

    async def impact_agent(state: GKState, config: RunnableConfig) -> dict:
        out = await subgraph.ainvoke(
            {"structured": state["structured"], "submitted_at": state["submitted_at"], "route": state["route"]},
            config,
        )
        return {"impact": out["result"], "errors": out.get("errors", []), "metrics": out.get("metrics", {})}

    return impact_agent
