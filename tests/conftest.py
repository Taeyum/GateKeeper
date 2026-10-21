"""테스트 공통 설정.

일반 테스트는 개발자 환경(셸 환경 변수, 실제 .env)과 무관하게 같은 결과가 나와야 한다.
실제 LLM을 부르는 테스트(llm 마크)만 실제 환경 변수와 .env를 그대로 쓴다.
"""

import pytest

from app import config, data_loader
from app.config import Settings
from app.llm.fake import reset_fake_responses

_SETTINGS_ENV = (
    "GK_VERSION", "LLM_PROVIDER", "MODEL_MAIN", "MODEL_SMALL", "MODEL_JUDGE", "EMBED_MODEL",
    "GK_DATA_DIR", "GK_CHECKPOINT_DB", "OLLAMA_BASE_URL", "OLLAMA_NUM_CTX", "GK_MAX_TOKENS",
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


@pytest.fixture
def make_settings():
    """.env를 읽지 않는 Settings를 만든다. make_settings(llm_provider="anthropic", ...)"""

    def _make(**overrides):
        return Settings(_env_file=None, **overrides)

    return _make
