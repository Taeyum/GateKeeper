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


def _live_settings(provider: str) -> Settings:
    """provider를 쓸 수 있는 설정. 서버, 모델, 키가 없으면 skip한다."""
    settings = Settings(llm_provider=provider)
    if provider == "ollama":
        model = settings.model_for("main")
        if model not in _ollama_models(settings.ollama_base_url):
            pytest.skip(f"Ollama가 꺼져 있거나 모델 {model}이 없다 (ollama serve, ollama pull {model})")
    elif getattr(settings, f"{provider}_api_key") is None:
        pytest.skip(f"{provider.upper()}_API_KEY가 없다 (.env 또는 환경 변수)")
    return settings


@pytest.mark.parametrize("provider", ["ollama", "anthropic"])
async def test_v0_기준선이_dev_요청_1건에서_스키마를_통과한다(monkeypatch, provider):
    """실제 규정/CMDB/캘린더/티켓 프롬프트(약 7.5k 토큰)가 V0Output(중첩 스키마)으로 구조화되는지 본다.

    anthropic은 네이티브 구조화 출력이 중첩 $defs를 받는지 확인하는 용도이기도 하다 (리뷰 J2).
    """
    from langsmith import tracing_context

    from app import data_loader
    from app.agent.graph import build_graph
    from app.data_loader import load_requests
    from app.llm.usage import UsageCollector
    from app.masking import mask_secrets

    settings = _live_settings(provider)
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("GK_VERSION", "v0")
    data_loader.clear_caches()

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
    print(f"\n[{provider}/{settings.model_for('main')}] verdict={result['recommendation'].verdict} violations="
          f"{[v.rule_id for v in result['recommendation'].violations]} input_tokens={usage.input_tokens} "
          f"output_tokens={usage.output_tokens} reasoning_tokens={usage.reasoning_tokens}")  # fmt: skip
    assert result["structured"].target and result["recommendation"].verdict
    assert usage.llm_calls >= 1
    if provider == "ollama":
        assert usage.max_input_tokens < settings.ollama_num_ctx - 1024  # 잘리지 않았다


@pytest.mark.parametrize("provider", ["ollama", "anthropic"])
async def test_Judge가_정수_범위_제약이_있는_스키마로_점수를_낸다(provider):
    """JudgeScore(score: int, ge=1, le=5)가 provider의 구조화 출력에서 받아들여지는지 본다 (리뷰 J2)."""
    from langsmith import tracing_context

    from app.data_loader import load_requests
    from app.llm.usage import UsageCollector
    from app.masking import mask_secrets
    from eval.judge import JudgeScore, judge_alternative

    settings = _live_settings(provider)
    item = load_requests("dev")[0]  # REQ-D01 반려, 대안이 필요한 요청
    collector = UsageCollector()
    with tracing_context(enabled=False):
        score = await judge_alternative(
            item,
            "등록된 파트너 IP를 /32로 지정하고, 정산 동결이 끝난 11/03 14시에 허용 기한 7일로 다시 요청한다.",
            mask_secrets(item.text),
            config={"callbacks": [collector]},
            settings=settings,
        )
    print(f"\n[{provider}/{settings.model_for('judge')}] score={score.score if score else None} "
          f"reason={score.reason if score else None}")  # fmt: skip
    assert isinstance(score, JudgeScore) and 1 <= score.score <= 5 and score.reason
    assert collector.snapshot().llm_calls >= 1


@pytest.mark.parametrize("schema_name", ["WireChangeRequest", "Recommendation", "V0Wire"])
def test_anthropic이_직접_보내는_스키마를_받아들인다(schema_name):
    """anthropic으로 직접 보내는 스키마가 실제 API에서 400 'Schema is too complex' 없이 통과해야 한다 (리뷰 J2).
    도메인 ChangeRequest는 공식 한도 안인데도 거절돼서(app/agent/wire.py) 와이어 모델을 보낸다."""
    from app.agent.wire import V0Wire, WireChangeRequest
    from app.llm.factory import get_llm, structured_llm
    from app.schemas import Recommendation

    schema = {"WireChangeRequest": WireChangeRequest, "Recommendation": Recommendation, "V0Wire": V0Wire}[schema_name]
    settings = _live_settings("anthropic")
    chain = structured_llm(get_llm("small", settings), schema, settings)  # 스키마 수락 여부만 보므로 싼 모델
    request = (
        "요청: payments-db 방화벽을 10월 30일 금요일 오후 8시에 열어 주세요. 출발지는 203.0.113.10, 포트 5432입니다. "
        "위 요청을 주어진 스키마로 구조화한다. 금요일 18시 이후는 배포 금지 시간대(CHG-01)라 판정은 반려다."
    )
    result = chain.invoke(request)
    print(f"\n[anthropic/{settings.model_for('small')}] {schema_name} -> {type(result).__name__}")
    assert isinstance(result, schema)
