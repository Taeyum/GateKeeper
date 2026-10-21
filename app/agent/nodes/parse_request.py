"""① parse_request: 자연어 변경요청을 ChangeRequest로 구조화한다 (docs/SPEC.md 3장).

구조화 규칙(structure_rules.md)은 v0과 같은 파일이라 두 버전이 구조화에 같은 지침을 받는다. 다른 점은
v1이 더한 것이다.

1. 구조화가 검증을 통과해도 판정에 필요한 값이 비어 있거나 틀리면 한 번 다시 묻는다.
   - 작업 시각(start): rules.json required_fields에 start가 있는 유형(배포, DB 변경, 표준 변경)에서 비어 있으면
     시간 조항(CHG-01, 05, 06, 10)을 검사할 수 없다 (리뷰 H1). 방화벽은 규정 4장의 필수 항목에 작업 시간이 없어
     다시 묻지 않는다. 다시 물어도 비어 있으면 값을 지어내게 하지 않고 그대로 진행하고, decide가 경고를 붙인다.
   - 작업 구간: 종료(end)가 시작(start)보다 늦지 않으면 규칙 엔진과 get_calendar가 INVALID_ARGUMENT로 거절해서
     policy_agent와 impact_agent가 함께 "확인 불가"가 되고 decide가 판정을 만들지 못한다 (N1). 종료가 적혀 있지
     않을 때의 기본값(시작 후 rules.json의 기본 작업 시간)은 코드가 계산해 알려 준다. 모델이 시각 덧셈을 틀리기 때문이다.
     다시 물어도 틀리면 값을 고쳐 쓰지 않고(본문에 종료가 있었는지 코드는 모른다) ReviewIncomplete로 멈춘다.
   - 대상(target): CMDB에 없으면 서비스 목록을 다시 알려 준다. 그래도 없으면 ReviewIncomplete로 판정을 만들지 않는다.
   그 밖의 필수 항목(출발지, 포트, 롤백, 테스트 증적, 기한)은 비어 있는 것 자체가 위반 근거라 다시 묻지 않는다.
2. LLM 호출은 최대 2회다. 검증 실패의 재시도와 위 점검의 재시도는 예산 하나를 나눠 쓴다.
3. ReviewIncomplete에는 어느 단계에서 왜 멈췄는지(stage, detail)를 단다. 메시지에는 요청 값을 싣지 않고,
   detail은 평가 하네스가 dev 기록에만 남긴다.

anthropic은 도메인 ChangeRequest가 구조화 출력 복잡도 한도에 걸려 WireChangeRequest로 받고 돌려놓는다
(app/agent/wire.py). 입력은 마스킹한 본문(masked_text)만 쓴다.
"""

from datetime import timedelta

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agent.errors import ReviewIncomplete
from app.agent.prompting import format_submitted_at, load_prompt, render_service_list, render_structure_rules
from app.agent.state import GKState
from app.agent.wire import change_request_schema, to_change_request
from app.config import get_settings
from app.data_loader import load_cmdb, load_rules
from app.llm.factory import get_llm, structured_llm
from app.schemas import ChangeRequest, Cmdb, RulesConfig

# 구조화 출력 파싱과 검증 실패. pydantic ValidationError는 ValueError의 하위 클래스다.
_INVALID = (OutputParserException, ValueError)

_UNKNOWN_TARGET = (
    "target {target!r}은(는) 서비스 목록에 없다. 목록의 id 중 본문과 맞는 것으로 고친다. 맞는 것이 없으면 그대로 둔다."
)
_MISSING_START = (
    "작업 시작 시각(start)은 이 변경 유형의 필수 항목인데 비어 있다. 본문에 날짜나 시각 표현이 있으면 요청 시각을 "
    "기준으로 실제 날짜와 시각으로 풀어 start와 end를 채운다. 본문에 정말 없으면 null로 둔다."
)
_INVALID_WINDOW = (
    "작업 종료 시각(end)은 시작 시각(start)보다 늦어야 한다. 종료 시각이 본문에 적혀 있지 않으면 end는 start로부터 "
    "{minutes}분 뒤인 {suggested}이다. 종료 시각이 적혀 있으면 그 시각을 그대로 쓴다."
)


def build_messages(state: GKState) -> list[BaseMessage]:
    """시스템 메시지는 요청과 무관하게 같은 텍스트다. 같은 접두사를 모델 쪽 캐시가 재사용할 수 있다."""
    system = load_prompt("parse_request.md").format(
        services=render_service_list(), structure_rules=render_structure_rules()
    )
    user = (
        f"요청 시각: {format_submitted_at(state['submitted_at'])}\n"
        f"요청자: {state['requester']}\n"
        f"요청 본문:\n{state['masked_text']}"
    )
    return [SystemMessage(content=system), HumanMessage(content=user)]


def has_invalid_window(request: ChangeRequest) -> bool:
    """종료가 시작보다 늦지 않은 작업 구간. 규칙 엔진(ValueError)과 get_calendar(INVALID_ARGUMENT)가 거절하는 입력이다."""
    return request.start is not None and request.end is not None and request.end <= request.start


def find_problems(request: ChangeRequest, cmdb: Cmdb, rules: RulesConfig) -> list[str]:
    """다시 물을 만한 문제. 비어 있으면 그대로 진행한다."""
    problems = []
    if cmdb.get_service(request.target) is None:
        problems.append(_UNKNOWN_TARGET.format(target=request.target))
    if request.start is None and "start" in rules.required_fields.get(request.change_type, []):
        problems.append(_MISSING_START)
    if has_invalid_window(request):
        minutes = rules.defaults.work_duration_minutes_if_missing
        suggested = (request.start + timedelta(minutes=minutes)).isoformat()  # type: ignore[operator]
        problems.append(_INVALID_WINDOW.format(minutes=minutes, suggested=suggested))
    return problems


def retry_message(previous: ChangeRequest, problems: list[str]) -> HumanMessage:
    listed = "\n".join(f"- {problem}" for problem in problems)
    return HumanMessage(
        content=f"앞선 출력:\n{previous.model_dump_json()}\n\n고칠 점:\n{listed}\n\n고쳐서 전체 필드를 다시 출력한다."
    )


def _usable(request: ChangeRequest, cmdb: Cmdb) -> bool:
    return cmdb.get_service(request.target) is not None and not has_invalid_window(request)


def _prefer(first: ChangeRequest, second: ChangeRequest, cmdb: Cmdb) -> ChangeRequest:
    """재시도 결과를 쓰되, 첫 결과는 멀쩡했는데 재시도가 대상이나 작업 구간을 망가뜨렸으면 첫 결과를 쓴다."""
    if _usable(first, cmdb) and not _usable(second, cmdb):
        return first
    return second


async def parse_request(state: GKState, config: RunnableConfig) -> dict:
    settings = get_settings()
    cmdb, rules = load_cmdb(), load_rules()
    chain = structured_llm(get_llm("main", settings), change_request_schema(settings.llm_provider), settings)
    messages = build_messages(state)
    parse_retries = required_field_retries = 0

    try:
        request = to_change_request(await chain.ainvoke(messages, config))
    except _INVALID:
        # 검증 실패는 같은 입력으로 1회 재시도한다. v0과 같은 정책이고, 이 재시도가 예산을 쓴다.
        parse_retries = 1
        request = to_change_request(await chain.ainvoke(messages, config))
    else:
        problems = find_problems(request, cmdb, rules)
        if problems:
            required_field_retries = 1
            try:
                retried = to_change_request(await chain.ainvoke([*messages, retry_message(request, problems)], config))
            except _INVALID:
                pass  # 첫 결과가 이미 유효하므로 그대로 쓴다
            else:
                request = _prefer(request, retried, cmdb)

    if cmdb.get_service(request.target) is None:
        raise ReviewIncomplete(
            "요청 대상이 서비스 목록(CMDB)에 없어 자동 검토를 진행할 수 없다.",
            stage="parse_request",
            detail=f"target={request.target!r}이(가) CMDB에 없다",
        )
    if has_invalid_window(request):
        raise ReviewIncomplete(
            "작업 종료 시각이 시작 시각보다 늦지 않아 자동 검토를 진행할 수 없다.",
            stage="parse_request",
            detail=f"start={request.start.isoformat()}, end={request.end.isoformat()}",  # type: ignore[union-attr]
        )
    return {
        "structured": request,
        "metrics": {"parse_retries": parse_retries, "required_field_retries": required_field_retries},
    }
