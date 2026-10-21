"""실제 LLM을 부르는 테스트. pytest -m llm 으로만 실행한다.

ollama는 서버와 모델이 있어야 하고, anthropic/openai는 키가 있어야 한다. 조건이 안 되면 skip한다.
점검 결과(지연, 파싱된 값)를 보려면 -s 를 붙인다.
"""

import httpx
import pytest

from app.config import Settings
from app.llm.factory import check_structured_output

pytestmark = pytest.mark.llm


def _ollama_models(base_url: str) -> list[str]:
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=2)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    return [m["name"] for m in response.json().get("models", [])]


def test_ollama_main_구조화_출력():
    settings = Settings(llm_provider="ollama")
    model = settings.model_for("main")
    if model not in _ollama_models(settings.ollama_base_url):
        pytest.skip(f"Ollama가 꺼져 있거나 모델 {model}이 없다 (ollama serve, ollama pull {model})")
    result = check_structured_output("main", settings)
    print(f"\n[ollama/{result.model}] ok={result.ok} {result.latency_s}s parsed={result.parsed} error={result.error}")
    assert result.ok, result.error


@pytest.mark.parametrize(
    ("provider", "key_field"), [("anthropic", "anthropic_api_key"), ("openai", "openai_api_key")]
)
@pytest.mark.parametrize("role", ["main", "small"])
def test_API_provider_구조화_출력(provider, key_field, role):
    settings = Settings(llm_provider=provider)
    if getattr(settings, key_field) is None:
        pytest.skip(f"{key_field.upper()}가 없다 (.env 또는 환경 변수)")
    result = check_structured_output(role, settings)
    print(f"\n[{provider}/{result.model}] ok={result.ok} {result.latency_s}s parsed={result.parsed} error={result.error}")
    assert result.ok, result.error


async def test_ollama_v0_기준선이_dev_요청_1건에서_스키마를_통과한다(monkeypatch):
    """실제 규정/CMDB/캘린더/티켓 프롬프트(약 7.5k 토큰)가 num_ctx 안에서 V0Output으로 구조화되는지 본다."""
    from app import data_loader
    from app.agent.graph import build_graph
    from app.data_loader import load_requests
    from langsmith import tracing_context

    from app.llm.usage import UsageCollector
    from app.masking import mask_secrets

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("GK_VERSION", "v0")
    data_loader.clear_caches()
    settings = Settings(llm_provider="ollama")
    model = settings.model_for("main")
    if model not in _ollama_models(settings.ollama_base_url):
        pytest.skip(f"Ollama가 꺼져 있거나 모델 {model}이 없다 (ollama serve, ollama pull {model})")

    item = load_requests("dev")[0]
    state = {
        "request_id": item.request_id,
        "submitted_at": item.submitted_at.isoformat(),
        "requester": item.requester,
        "masked_text": mask_secrets(item.text),
    }
    collector = UsageCollector()
    with tracing_context(enabled=False):  # .env의 LANGCHAIN_TRACING_V2와 상관없이 이 테스트는 추적하지 않는다
        result = await build_graph("v0").ainvoke(state, config={"callbacks": [collector]})
    usage = collector.snapshot()
    print(f"\n[ollama/{model}] verdict={result['recommendation'].verdict} violations="
          f"{[v.rule_id for v in result['recommendation'].violations]} input_tokens={usage.input_tokens} "
          f"output_tokens={usage.output_tokens}")  # fmt: skip
    assert result["structured"].target and result["recommendation"].verdict
    assert usage.llm_calls >= 1 and usage.max_input_tokens < settings.ollama_num_ctx - 1024  # 잘리지 않았다
