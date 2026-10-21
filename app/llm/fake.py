"""테스트용 가짜 LLM. 테스트가 미리 넣어 둔 응답을 순서대로 돌려준다.

단위 테스트와 클라우드 세션(LLM_PROVIDER=fake)에서 쓴다. 평가에는 쓰지 않는다.
응답은 role(main/small/judge)별 큐에 쌓고, 호출할 때마다 하나씩 꺼낸다. 병렬로 도는 노드가 같은 role의 큐를 함께
쓰면 꺼내는 순서가 실행 순서에 달려 있어서, 구조화 출력 스키마 이름까지 지정한 큐(set_fake_responses_for)를 먼저 본다.
큐가 비면 조용히 기본값을 만들지 않고 예외를 던진다. 테스트가 기대한 호출 수와
실제 호출 수가 어긋나는 것을 바로 드러내려는 것.
"""

import json
from collections import defaultdict, deque
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

_RESPONSES: dict[str, deque[Any]] = defaultdict(deque)


class FakeResponsesExhausted(RuntimeError):
    """role의 응답 큐가 비었는데 또 호출했다."""


def set_fake_responses(role: str, responses: list[Any]) -> None:
    """role의 응답 큐를 주어진 순서로 교체한다.

    문자열은 그대로 응답 본문이 된다. BaseModel/dict는 with_structured_output에서 스키마로 돌려주고,
    일반 호출에서는 JSON 문자열이 된다.
    """
    _RESPONSES[role] = deque(responses)


def set_fake_responses_for(role: str, schema: Any, responses: list[Any]) -> None:
    """role의 구조화 출력 중 schema(클래스 이름으로 구분)를 요청하는 호출만 이 큐에서 꺼낸다.

    같은 role의 노드 둘이 병렬로 호출해도 각자 자기 스키마의 응답을 받는다. 이 큐가 비면 role 큐로 넘어간다.
    """
    _RESPONSES[f"{role}/{getattr(schema, '__name__', str(schema))}"] = deque(responses)


def reset_fake_responses() -> None:
    _RESPONSES.clear()


def _coerce(schema: Any, item: Any) -> Any:
    """큐에서 꺼낸 값을 요청된 스키마의 결과로 바꾼다."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        if isinstance(item, schema):
            return item
        if isinstance(item, BaseModel):
            return schema.model_validate(item.model_dump())
        if isinstance(item, str):
            return schema.model_validate_json(item)
        return schema.model_validate(item)
    # 스키마가 JSON Schema dict면 dict 결과를 돌려준다.
    if isinstance(item, BaseModel):
        return item.model_dump()
    if isinstance(item, str):
        return json.loads(item)
    return item


def _as_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, BaseModel):
        return item.model_dump_json()
    return json.dumps(item, ensure_ascii=False, default=str)


def _fake_tokens(text: str) -> int:
    """글자 4개를 토큰 1개로 센다. 실제 토크나이저가 아니라 결정론적인 가짜 값이다."""
    return max(1, len(text) // 4)


def _message_text(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)


class FakeChatModel(BaseChatModel):
    role: str = "main"
    # with_structured_output으로 만든 체인이 요청하는 스키마 이름. 있으면 그 스키마 전용 큐를 먼저 본다.
    schema_name: str | None = None

    @property
    def _llm_type(self) -> str:
        return "gatekeeper-fake"

    def _next(self) -> Any:
        if self.schema_name is not None:
            specific = _RESPONSES.get(f"{self.role}/{self.schema_name}")
            if specific:
                return specific.popleft()
        queue = _RESPONSES[self.role]
        if not queue:
            raise FakeResponsesExhausted(
                f"fake LLM({self.role})의 응답이 없다. set_fake_responses로 먼저 넣어 둔다."
            )
        return queue.popleft()

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = _as_text(self._next())
        input_tokens = sum(_fake_tokens(_message_text(m)) for m in messages)
        output_tokens = _fake_tokens(text)
        message = AIMessage(
            content=text,
            # 실제 provider처럼 호출 수와 토큰 집계(app/llm/usage.py)가 fake에서도 동작하게 한다.
            usage_metadata={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            response_metadata={"model_name": f"fake-{self.role}"},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def with_structured_output(
        self, schema: Any, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable:
        """응답을 _generate 경로로 꺼낸 뒤 스키마로 바꾼다. 그래야 콜백(호출 수, 토큰)이 실제 모델처럼 불린다."""

        def _parse(message: AIMessage) -> Any:
            parsed = _coerce(schema, message.content)
            if include_raw:
                return {"raw": message, "parsed": parsed, "parsing_error": None}
            return parsed

        bound = self.model_copy(update={"schema_name": getattr(schema, "__name__", None)})
        return bound | RunnableLambda(_parse)
