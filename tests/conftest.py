"""테스트 공통 설정.

일반 테스트는 개발자 환경(셸 환경 변수, 실제 .env)과 무관하게 같은 결과가 나와야 한다.
실제 LLM을 부르는 테스트(llm 마크)만 실제 환경 변수와 .env를 그대로 쓴다.
"""

import pytest
from langsmith import tracing_context

from app import config, data_loader
from app.config import Settings
from app.llm.fake import reset_fake_responses

_SETTINGS_ENV = (
    "GK_VERSION", "LLM_PROVIDER", "MODEL_MAIN", "MODEL_SMALL", "MODEL_JUDGE", "EMBED_MODEL",
    "GK_DATA_DIR", "GK_CHECKPOINT_DB", "GK_RAG_DIR", "GK_AUDIT_LOG", "GK_WEB_ORIGINS", "GK_RUN_TIMEOUT_S",
    "OLLAMA_BASE_URL", "OLLAMA_NUM_CTX", "GK_MAX_TOKENS",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
)  # fmt: skip


@pytest.fixture(autouse=True)
def _hermetic(request, monkeypatch):
    if request.node.get_closest_marker("llm"):
        yield
        return
    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: False)
    data_loader.clear_caches()
    reset_fake_responses()
    yield
    data_loader.clear_caches()
    reset_fake_responses()


@pytest.fixture(autouse=True)
def _no_langsmith_tracing(monkeypatch):
    """테스트 중에는 llm 마크를 포함해 LangSmith로 추적을 보내지 않는다.

    .env의 LANGCHAIN_TRACING_V2=true가 load_dotenv로 들어와도 override=False라 여기서 정한 값이 이긴다.
    langsmith가 환경 변수 읽기를 캐시할 수 있어서 tracing_context로도 끈다 (평가 하네스와 같은 방식).
    """
    for name in ("LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING", "LANGSMITH_TRACING_V2"):
        monkeypatch.setenv(name, "false")
    with tracing_context(enabled=False):
        yield


@pytest.fixture
def make_settings():
    """.env를 읽지 않는 Settings를 만든다. make_settings(llm_provider="anthropic", ...)"""

    def _make(**overrides):
        return Settings(_env_file=None, **overrides)

    return _make
