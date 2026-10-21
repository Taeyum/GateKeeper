"""v0 기준선: LLM 노드 하나가 구조화와 권고를 한 번에 낸다 (docs/SPEC.md 4장).

v0도 v1, v2와 같은 정보를 받는다 (규정 원문, CMDB, 변경 캘린더, 장애 티켓, 요청 시각). "정보를 안 줘서
틀렸다"는 반박을 막고, 차이를 구조(Tool, 라우팅, 검증)만으로 설명하려는 것이다. 이력(history)만 빠지는데
그것은 v2의 RAG가 더하는 정보다.

Tool, RAG, Validator는 없다. 출력은 규칙 엔진으로 보정하지 않는다. 판정을 코드가 정한다는 원칙
(CLAUDE.md 절대 규칙 2)의 예외는 이 기준선뿐이다.
"""

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from app.agent.prompting import format_submitted_at, load_prompt, render_static_context
from app.agent.state import GKState
from app.agent.wire import V0Wire
from app.config import get_settings
from app.llm.factory import get_llm, structured_llm
from app.schemas import ChangeRequest, Recommendation


class V0Output(BaseModel):
    """v0이 한 번에 내는 결과. v1, v2는 이 둘을 노드별로 나눠서 만든다."""

    structured: ChangeRequest
    recommendation: Recommendation


def output_schema(provider: str) -> type[BaseModel]:
    """provider가 받아들이는 구조화 출력 스키마. anthropic은 한 번에 내는 V0Output이 스키마 복잡도 한도를 넘어서
    (선택 필드 25, union 18) 같은 내용을 선택 필드 없이 줄인 V0Wire를 쓴다. 이유와 실측은 app/agent/wire.py 참고."""
    return V0Wire if provider == "anthropic" else V0Output


def to_output(result: BaseModel) -> V0Output:
    if isinstance(result, V0Wire):
        return V0Output(structured=result.structured.to_domain(), recommendation=result.recommendation.to_domain())
    return result  # type: ignore[return-value]


def build_messages(state: GKState) -> list:
    """시스템 메시지는 요청과 무관하게 같은 텍스트다. 같은 접두사를 모델 쪽 캐시가 재사용할 수 있다."""
    system = load_prompt("baseline_v0.md").format(**render_static_context())
    user = (
        f"요청 시각: {format_submitted_at(state['submitted_at'])}\n"
        f"요청자: {state['requester']}\n"
        f"요청 본문:\n{state['masked_text']}"
    )
    return [SystemMessage(content=system), HumanMessage(content=user)]


async def baseline_v0(state: GKState, config: RunnableConfig) -> dict:
    settings = get_settings()
    chain = structured_llm(get_llm("main", settings), output_schema(settings.llm_provider), settings)
    messages = build_messages(state)
    parse_retries = 0
    try:
        output = to_output(await chain.ainvoke(messages, config))
    except (OutputParserException, ValueError):
        # 구조화 출력 파싱/검증 실패는 1회 재시도한다. SPEC ① parse_request와 같은 정책이라 v0과 v1이 같은 조건이다.
        parse_retries = 1
        output = to_output(await chain.ainvoke(messages, config))
    return {
        "structured": output.structured,
        "recommendation": output.recommendation,
        "metrics": {"parse_retries": parse_retries},
    }
