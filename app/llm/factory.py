"""LLM 팩토리. 모든 LLM 호출은 get_llm을 거친다.

temperature 정책
    ollama와 openai는 0이다. anthropic 5.5 모델(sonnet/haiku/opus)은 기본값(1) 외의
    temperature를 API가 400으로 거부하고, langchain-anthropic도 sonnet-5-5에 1이 아닌
    값을 주면 ValueError를 낸다. 그래서 anthropic에는 temperature를 보내지 않는다.
    결정론은 보장되지 않으므로 같은 요청의 반복 실행 일관성은 평가 지표로 따로 본다.

구조화 출력
    anthropic의 with_structured_output 기본 방식(function_calling)은 tool_choice를 강제하는데
    sonnet-5-5와 opus-5-5는 이를 지원하지 않는다. 라이브러리가 강제 없는 tool 바인딩으로
    조용히 폴백하지만 tool을 안 부르면 예외가 난다. 폴백에 기대지 않으려고 모든 실제
    provider에서 method="json_schema"(provider 네이티브 구조화 출력)를 명시한다.
"""

import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel, SecretStr

from app.config import Role, Settings, get_settings
from app.llm.fake import FakeChatModel


class LLMConfigError(RuntimeError):
    """provider 설정이 모자라서 LLM을 만들 수 없다."""


def _require_key(key: SecretStr | None, env_name: str) -> SecretStr:
    if key is None:
        raise LLMConfigError(f"{env_name}가 설정되지 않았다. .env에 넣거나 환경 변수로 준다.")
    return key


def get_llm(role: Role, settings: Settings | None = None) -> BaseChatModel:
    """role(main/small/judge)에 맞는 채팅 모델을 만든다. provider와 모델은 설정으로만 정해진다."""
    settings = settings or get_settings()
    provider = settings.llm_provider
    model = settings.model_for(role)

    if provider == "fake":
        return FakeChatModel(role=role)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        # reasoning=False: qwen3가 <think> 구간을 내지 않게 한다 (지연과 토큰, 구조화 출력 안정성).
        return ChatOllama(
            model=model,
            base_url=settings.ollama_base_url,
            temperature=0,
            num_ctx=settings.ollama_num_ctx,
            reasoning=False,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=_require_key(settings.anthropic_api_key, "ANTHROPIC_API_KEY"),
            max_tokens=settings.gk_max_tokens,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=_require_key(settings.openai_api_key, "OPENAI_API_KEY"),
            temperature=0,
            max_tokens=settings.gk_max_tokens,
        )

    raise LLMConfigError(f"알 수 없는 provider: {provider}")


def structured_llm(llm: BaseChatModel, schema: Any, settings: Settings | None = None) -> Runnable:
    """llm에 구조화 출력을 붙인다. provider별 방식을 이 함수 한 곳에서 정한다."""
    settings = settings or get_settings()
    if settings.llm_provider == "fake":
        return llm.with_structured_output(schema)
    return llm.with_structured_output(schema, method="json_schema")


class StructuredCheck(BaseModel):
    provider: str
    model: str
    ok: bool
    latency_s: float
    parsed: dict[str, Any] | None = None
    error: str | None = None


class _Probe(BaseModel):
    city: str
    country: str


def check_structured_output(role: Role = "main", settings: Settings | None = None) -> StructuredCheck:
    """작은 스키마로 실제 구조화 출력이 되는지 한 번 호출해서 확인한다.

    provider를 바꾸거나 모델을 새로 쓸 때 쓰는 점검용이다. 실패해도 예외를 던지지 않고
    ok=False와 오류 요약을 돌려준다.
    """
    settings = settings or get_settings()
    base = {"provider": settings.llm_provider, "model": settings.model_for(role)}
    started = time.perf_counter()
    try:
        llm = structured_llm(get_llm(role, settings), _Probe, settings)
        result = llm.invoke("다음 문장에서 도시와 나라를 뽑아라. 문장: 서울은 대한민국의 수도다.")
        ok = isinstance(result, _Probe) and bool(result.city.strip()) and bool(result.country.strip())
        return StructuredCheck(
            **base,
            ok=ok,
            latency_s=round(time.perf_counter() - started, 2),
            parsed=result.model_dump() if isinstance(result, BaseModel) else None,
        )
    except Exception as exc:  # 점검 도구라서 어떤 실패든 결과로 돌려준다
        return StructuredCheck(
            **base,
            ok=False,
            latency_s=round(time.perf_counter() - started, 2),
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
