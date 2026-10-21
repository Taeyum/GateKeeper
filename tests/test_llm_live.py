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
