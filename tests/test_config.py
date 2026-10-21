import pytest
from pydantic import ValidationError

from app.config import PROJECT_ROOT, get_settings


def test_기본값(make_settings):
    s = make_settings()
    assert s.gk_version == "v2"
    assert s.llm_provider == "ollama"
    assert s.embed_model == "bge-m3"
    assert s.ollama_num_ctx == 16384
    assert s.gk_max_tokens == 16000
    assert s.anthropic_api_key is None and s.openai_api_key is None


@pytest.mark.parametrize(
    ("provider", "main", "small", "judge"),
    [
        ("ollama", "qwen3:8b", "qwen3:0.6b", "qwen3:8b"),
        ("anthropic", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"),
    ],
)
def test_provider별_기본_모델(make_settings, provider, main, small, judge):
    s = make_settings(llm_provider=provider)
    assert (s.model_for("main"), s.model_for("small"), s.model_for("judge")) == (main, small, judge)


def test_환경변수가_기본값을_덮는다(monkeypatch, make_settings):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("GK_VERSION", "v1")
    monkeypatch.setenv("MODEL_MAIN", "claude-opus-5-5")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    s = make_settings()
    assert (s.llm_provider, s.gk_version, s.ollama_num_ctx) == ("anthropic", "v1", 8192)
    assert s.model_for("main") == "claude-opus-5-5"  # 명시한 모델이 provider 기본값보다 우선
    assert s.model_for("small") == "claude-haiku-5-5"


@pytest.mark.parametrize(("name", "value"), [("GK_VERSION", "v9"), ("LLM_PROVIDER", "gemini")])
def test_잘못된_값은_거부한다(monkeypatch, make_settings, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        make_settings()


def test_빈_값은_미설정으로_본다(monkeypatch, make_settings):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("MODEL_JUDGE", "  ")
    s = make_settings(llm_provider="anthropic")
    assert s.anthropic_api_key is None
    assert s.model_for("judge") == "claude-opus-5-5"


def test_API_키는_출력에_드러나지_않는다(make_settings):
    s = make_settings(anthropic_api_key="sk-ant-secret-value", openai_api_key="sk-openai-secret")
    text = repr(s) + str(s) + s.model_dump_json()
    assert "secret" not in text
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-secret-value"


def test_상대_경로는_저장소_루트_기준으로_푼다(make_settings, tmp_path):
    assert make_settings().data_dir == PROJECT_ROOT / "gatekeeper_data"
    assert make_settings().checkpoint_db == PROJECT_ROOT / ".data/checkpoints.sqlite"
    assert make_settings(gk_data_dir=tmp_path).data_dir == tmp_path


def test_API_설정의_기본값(make_settings):
    s = make_settings()
    assert s.audit_log == PROJECT_ROOT / ".data/audit.jsonl"
    assert s.web_origins == ["http://127.0.0.1:5173", "http://localhost:5173"]  # 화면 개발 서버 주소만
    assert s.gk_run_timeout_s == 300.0


def test_화면_주소는_쉼표로_여럿을_받고_공백과_빈_항목을_버린다(monkeypatch, make_settings):
    monkeypatch.setenv("GK_WEB_ORIGINS", " http://a.example ,, http://b.example:8080 ")
    assert make_settings().web_origins == ["http://a.example", "http://b.example:8080"]


def test_감사_로그_경로는_환경_변수로_바꾸고_상대_경로는_저장소_루트_기준이다(monkeypatch, make_settings, tmp_path):
    assert make_settings(gk_audit_log=tmp_path / "a.jsonl").audit_log == tmp_path / "a.jsonl"
    monkeypatch.setenv("GK_AUDIT_LOG", "logs/audit.jsonl")
    assert make_settings().audit_log == PROJECT_ROOT / "logs/audit.jsonl"


@pytest.mark.parametrize("value", ["0", "-1"])
def test_실행_제한_시간은_양수여야_한다(monkeypatch, make_settings, value):
    monkeypatch.setenv("GK_RUN_TIMEOUT_S", value)
    with pytest.raises(ValidationError):
        make_settings()


def test_get_settings는_캐시된다():
    assert get_settings() is get_settings()
