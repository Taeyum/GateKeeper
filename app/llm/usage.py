"""LLM 호출 수와 모델별 토큰 집계 (평가용, EVAL.md 3장 비용 지표).

langchain-core의 UsageMetadataCallbackHandler는 호출 수를 세지 않고 실패한 호출도 놓친다.
평가에는 "요청 1건이 LLM을 몇 번 불렀고 토큰이 얼마인지"가 필요해서 직접 둔다.

시스템용과 Judge용 수집기를 따로 만들어 쓴다. Judge 비용이 시스템 비용에 섞이면
버전 간 비용 비교가 깨지기 때문이다.
"""

import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from pydantic import BaseModel, Field

UNKNOWN_MODEL = "unknown"


class ModelUsage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # anthropic thinking 토큰처럼 출력 토큰에 섞여 있는 추론 토큰. output_tokens에 이미 포함된 값이다.
    reasoning_tokens: int = 0

    def add(self, other: "ModelUsage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.reasoning_tokens += other.reasoning_tokens


class UsageSummary(BaseModel):
    """요청 1건(또는 여러 건을 더한 것)의 LLM 사용량."""

    llm_calls: int = 0
    llm_errors: int = 0
    # 호출 하나의 입력 토큰 최댓값. 컨텍스트 잘림(Ollama num_ctx) 감지는 합계가 아니라 호출 단위 값이 기준이다.
    max_input_tokens: int = 0
    by_model: dict[str, ModelUsage] = Field(default_factory=dict)

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.by_model.values())

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.by_model.values())

    @property
    def reasoning_tokens(self) -> int:
        return sum(u.reasoning_tokens for u in self.by_model.values())

    def add(self, other: "UsageSummary") -> None:
        self.llm_calls += other.llm_calls
        self.llm_errors += other.llm_errors
        self.max_input_tokens = max(self.max_input_tokens, other.max_input_tokens)
        for model, usage in other.by_model.items():
            self.by_model.setdefault(model, ModelUsage()).add(usage)


class UsageCollector(BaseCallbackHandler):
    """config={"callbacks": [collector]}로 넘기면 그 안에서 일어난 LLM 호출을 센다."""

    # 동기 핸들러를 별도 스레드로 보내지 않고 호출 지점에서 바로 실행한다. 집계 순서가 흔들리지 않게 하려는 것.
    run_inline = True

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._summary = UsageSummary()

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        with self._lock:
            self._summary.llm_calls += 1

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        with self._lock:
            self._summary.llm_errors += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        generation = response.generations[0][0] if response.generations and response.generations[0] else None
        model = UNKNOWN_MODEL
        usage = ModelUsage(calls=1)
        if isinstance(generation, ChatGeneration) and isinstance(generation.message, AIMessage):
            message = generation.message
            model = message.response_metadata.get("model_name") or UNKNOWN_MODEL
            meta = message.usage_metadata
            if meta:
                usage.input_tokens = int(meta.get("input_tokens", 0))
                usage.output_tokens = int(meta.get("output_tokens", 0))
                usage.reasoning_tokens = int((meta.get("output_token_details") or {}).get("reasoning", 0))
        with self._lock:
            self._summary.by_model.setdefault(model, ModelUsage()).add(usage)
            self._summary.max_input_tokens = max(self._summary.max_input_tokens, usage.input_tokens)

    def snapshot(self) -> UsageSummary:
        """지금까지의 집계를 복사해서 돌려준다."""
        with self._lock:
            return self._summary.model_copy(deep=True)
