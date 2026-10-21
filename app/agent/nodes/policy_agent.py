"""③ policy_agent: 규정 검사 서브에이전트 (docs/SPEC.md 3장).

자기 상태를 가진 컴파일된 서브그래프다. 부모 그래프에는 상태를 바꿔 주는 래퍼 노드(make_policy_agent)로 붙는다.

    check_policy → (위반이나 면제가 있으면) read_rules → write_notes

- check_policy: MCP check_policy 결과가 위반의 원본이다. 어떤 Tool을 부를지 LLM이 고르지 않고 코드가 정한다.
  판정 경로에 LLM의 도구 선택을 넣지 않는 것이 이 설계의 주장이다.
- read_rules: 걸린 조항의 원문을 rules://CHG-xx Resource에서 읽는다.
- write_notes: LLM이 원문을 읽고 조항별 쉬운 설명(notes)과 한 문장 요약을 쓴다.

violations[].evidence는 엔진 문장 그대로다 (리뷰 K2). v1에는 Validator가 없어서 LLM이 시각이나 대역을 틀리게 옮겨도
막을 곳이 없고, 화면은 엔진 근거 문장을 "자세한 근거"로 그대로 보여 준다. LLM의 설명은 notes로 따로 두고
decide의 explanation이 쓴다. 엔진 결과에 없는 조항 ID는 버리고, 빠뜨린 조항은 note 없이 둔다.

실패: check_policy가 끝내 실패하면 status=unavailable이다(decide가 판정을 만들지 않는다). 원문 읽기나 LLM이 실패해도
엔진 결과는 그대로 남고 notes만 비며, 요약은 코드가 만든 문장으로 채운다.
"""

import asyncio
import operator
from datetime import datetime
from typing import Annotated

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.agent.mcp_client import MCPClient, MCPClientError
from app.agent.prompting import load_prompt
from app.agent.results import RuleNote, SubagentResult
from app.agent.state import GKState, add_counters
from app.config import get_settings
from app.llm.factory import get_llm, structured_llm
from app.schemas import ChangeRequest, PolicyCheckResult, Route, Violation

_SEVERITY_TEXT = {"forbidden": "금지", "fixable": "보완"}


class RuleNoteItem(BaseModel):
    rule_id: str = Field(description="입력에 나온 조항 ID")
    note: str = Field(description="이 조항이 요청에 왜 걸렸는지(면제면 왜 면제됐는지) 한두 문장")


class PolicyNotes(BaseModel):
    """write_notes가 LLM에게 받는 출력. 선택 필드가 없어 anthropic에도 그대로 보낸다."""

    items: list[RuleNoteItem] = Field(description="입력에 나온 조항마다 하나씩")
    summary: str = Field(description="전체를 한 문장으로 요약")


class PolicyAgentState(TypedDict, total=False):
    # 입력 (부모 상태에서 래퍼가 복사한다)
    structured: ChangeRequest
    submitted_at: str
    route: Route
    # 서브그래프 안에서만 쓰는 값
    check: PolicyCheckResult
    rule_texts: dict[str, str]
    # 출력
    result: SubagentResult
    errors: Annotated[list[str], operator.add]
    metrics: Annotated[dict[str, int], add_counters]


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


def rule_ids_of(check: PolicyCheckResult) -> list[str]:
    """위반과 면제 조항 ID. 위반이 먼저이고 중복은 뺀다."""
    return list(dict.fromkeys(v.rule_id for v in [*check.violations, *check.exempted]))


def describe_check(check: PolicyCheckResult) -> str:
    """코드가 만드는 요약. LLM이 없거나 실패해도 summary가 비지 않게 한다."""
    parts = []
    if check.violations:
        parts.append(f"위반 {len(check.violations)}건({', '.join(v.rule_id for v in check.violations)})")
    else:
        parts.append("위반 없음")
    if check.exempted:
        parts.append(f"유효한 긴급 변경으로 면제된 조항({', '.join(v.rule_id for v in check.exempted)})")
    return ", ".join(parts) + "."


def merge_notes(notes: PolicyNotes, allowed: list[str]) -> list[RuleNote]:
    """엔진 결과에 있는 조항의 note만 받는다. LLM이 더한 ID와 빈 note는 버리고, 조항마다 첫 note만 쓴다. 순서는 엔진 순서."""
    by_rule: dict[str, str] = {}
    for item in notes.items:
        rule_id, text = item.rule_id.strip().upper(), item.note.strip()
        if text and rule_id not in by_rule:
            by_rule[rule_id] = text
    # 엔진 결과에 있는 조항만, 엔진 순서대로 내보낸다. LLM이 더한 ID는 여기서 걸러진다.
    return [RuleNote(rule_id=rule_id, note=by_rule[rule_id]) for rule_id in allowed if rule_id in by_rule]


def _evidence_lines(violations: list[Violation]) -> str:
    return "\n".join(
        f"- {v.rule_id} ({_SEVERITY_TEXT.get(v.severity, v.severity)}): {v.evidence}" for v in violations
    ) or "- 없음"


def render_policy_input(request: ChangeRequest, check: PolicyCheckResult, rule_texts: dict[str, str]) -> str:
    texts = "\n\n".join(f"### {rule_id}\n{text}" for rule_id, text in rule_texts.items()) or "(원문을 읽지 못했다)"
    return (
        f"요청 대상: {request.target}, 변경 유형: {request.change_type}\n\n"
        f"## 규칙 엔진이 찾은 위반\n{_evidence_lines(check.violations)}\n\n"
        f"## 유효한 긴급 변경으로 면제된 조항\n{_evidence_lines(check.exempted)}\n\n"
        f"## 규정 원문\n{texts}"
    )


# ---------------------------------------------------------------------------
# 서브그래프
# ---------------------------------------------------------------------------


def build_policy_subgraph(mcp: MCPClient) -> CompiledStateGraph:
    async def check_policy(state: PolicyAgentState, config: RunnableConfig) -> dict:
        metrics: dict[str, int] = {}  # MCP 클라이언트가 tool_retries를 여기에 더한다
        try:
            check = await mcp.check_policy(state["structured"], datetime.fromisoformat(state["submitted_at"]), metrics=metrics)
        except MCPClientError as exc:
            return {
                "result": SubagentResult(status="unavailable", summary="규정 검사: 확인 불가"),
                "errors": [f"policy_agent: 규정 검사 실패 ({type(exc).__name__})"],
                "metrics": {**metrics, "partial_failures": 1},
            }
        return {
            "check": check,
            "result": SubagentResult(tool_output=check.model_dump(mode="json"), summary=describe_check(check)),
            "metrics": metrics,
        }

    def after_check(state: PolicyAgentState) -> str:
        check = state.get("check")
        return "read_rules" if check is not None and rule_ids_of(check) else END

    async def read_rules(state: PolicyAgentState, config: RunnableConfig) -> dict:
        rule_ids = rule_ids_of(state["check"])
        metrics: dict[str, int] = {}
        outcomes = await asyncio.gather(*(mcp.read_rule(i, metrics=metrics) for i in rule_ids), return_exceptions=True)
        texts: dict[str, str] = {}
        errors: list[str] = []
        for rule_id, outcome in zip(rule_ids, outcomes, strict=True):
            if isinstance(outcome, MCPClientError):
                errors.append(f"policy_agent: {rule_id} 원문 읽기 실패 ({type(outcome).__name__})")
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                texts[rule_id] = outcome
        if errors:
            metrics["partial_failures"] = len(errors)
        return {"rule_texts": texts, "errors": errors, "metrics": metrics}

    async def write_notes(state: PolicyAgentState, config: RunnableConfig) -> dict:
        check, request = state["check"], state["structured"]
        settings = get_settings()
        chain = structured_llm(get_llm("main", settings), PolicyNotes, settings)
        messages = [
            SystemMessage(content=load_prompt("policy_agent.md")),
            HumanMessage(content=render_policy_input(request, check, state.get("rule_texts", {}))),
        ]
        try:
            written = await chain.ainvoke(messages, config)
        except Exception as exc:  # 설명은 부가 정보다. 엔진 결과는 그대로 두고 notes만 비운다
            return {
                "errors": [f"policy_agent: 설명 작성 실패 ({type(exc).__name__})"],
                "metrics": {"partial_failures": 1},
            }
        result = state["result"]
        return {
            "result": result.model_copy(
                update={"notes": merge_notes(written, rule_ids_of(check)), "summary": written.summary.strip() or result.summary}
            )
        }

    graph = StateGraph(PolicyAgentState)
    graph.add_node("check_policy", check_policy)
    graph.add_node("read_rules", read_rules)
    graph.add_node("write_notes", write_notes)
    graph.add_edge(START, "check_policy")
    graph.add_conditional_edges("check_policy", after_check, {"read_rules": "read_rules", END: END})
    graph.add_edge("read_rules", "write_notes")
    graph.add_edge("write_notes", END)
    return graph.compile(name="policy_agent")


def make_policy_agent(mcp: MCPClient):
    """부모 그래프에 붙일 래퍼 노드. 부모 상태에서 입력을 꺼내 서브그래프를 돌리고 결과를 부모 키로 돌려준다."""
    subgraph = build_policy_subgraph(mcp)

    async def policy_agent(state: GKState, config: RunnableConfig) -> dict:
        # config를 그대로 넘겨야 호출 수 집계 콜백과 LangSmith trace가 서브그래프 안쪽까지 이어진다
        out = await subgraph.ainvoke(
            {"structured": state["structured"], "submitted_at": state["submitted_at"], "route": state["route"]},
            config,
        )
        return {"policy": out["result"], "errors": out.get("errors", []), "metrics": out.get("metrics", {})}

    return policy_agent
