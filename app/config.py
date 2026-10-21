"""설정. .env와 환경 변수를 pydantic-settings로 읽는다.

provider와 모델은 환경 변수로만 바꾼다. 모델 이름을 코드 곳곳에 박지 않으려고
provider별 기본값을 이 파일 한 곳에 모아 둔다 (docs/SPEC.md 8장 표).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

GKVersion = Literal["v0", "v1", "v2"]
Provider = Literal["fake", "ollama", "anthropic", "openai"]
Role = Literal["main", "small", "judge"]

# judge는 시스템 모델과 다른 모델이 원칙이라 anthropic은 opus로 둔다.
# ollama 개발 평가에서는 main과 같은 모델이 되므로 4단계에서 다시 정한다.
# openai는 예비 provider라 Base와 같은 모델을 임시로 둔다.
DEFAULT_MODELS: dict[Provider, dict[Role, str]] = {
    "fake": {"main": "fake-main", "small": "fake-small", "judge": "fake-judge"},
    "ollama": {"main": "qwen3:8b", "small": "qwen3:0.6b", "judge": "qwen3:8b"},
    "anthropic": {
        "main": "claude-sonnet-5-5",
        "small": "claude-haiku-5-5",
        "judge": "claude-opus-5-5",
    },
    "openai": {"main": "gpt-4o", "small": "gpt-4o-mini", "judge": "gpt-4o"},
}


def _resolve(path: Path) -> Path:
    """상대 경로는 저장소 루트 기준으로 푼다. 실행 위치와 상관없이 같은 파일을 가리키게 하려는 것."""
    return path if path.is_absolute() else PROJECT_ROOT / path


class Settings(BaseSettings):
    # model_main 같은 필드가 pydantic의 보호 접두사 "model_"와 겹치므로 보호를 끈다.
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    gk_version: GKVersion = "v2"
    llm_provider: Provider = "ollama"
    model_main: str | None = None
    model_small: str | None = None
    model_judge: str | None = None
    embed_model: str = "bge-m3"
    gk_data_dir: Path = Path("gatekeeper_data")
    gk_checkpoint_db: Path = Path(".data/checkpoints.sqlite")
    # RAG 색인(이력 벡터)을 두는 폴더. sh/build_index.sh가 만든다. .data/는 git 제외다.
    gk_rag_dir: Path = Path(".data/rag")
    # 감사 로그(JSON Lines). finalize 노드와 API가 쓴다. .data/는 git 제외다.
    gk_audit_log: Path = Path(".data/audit.jsonl")
    # CORS로 허용할 화면 주소. 쉼표로 여럿을 준다. 화면의 Vite 개발 서버 기본 주소만 둔다.
    gk_web_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    # 요청 하나의 그래프 실행 제한(초). 넘으면 수동 검토로 넘긴다. 사람이 승인을 기다리는 시간은 세지 않는다.
    gk_run_timeout_s: float = Field(default=300.0, gt=0)

    ollama_base_url: str = "http://127.0.0.1:11434"
    # v0 프롬프트(규정 원문 + CMDB + 캘린더 + 티켓)가 길어서 Ollama 기본 컨텍스트로는 조용히 잘린다.
    ollama_num_ctx: int = 16384
    # anthropic은 max_tokens가 필수이고, 5.5 모델은 thinking 토큰도 여기에 포함된다.
    gk_max_tokens: int = 16000

    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    @field_validator(
        "model_main", "model_small", "model_judge", "anthropic_api_key", "openai_api_key",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        """.env.example처럼 값이 빈 줄(KEY=)은 미설정으로 본다."""
        return None if isinstance(value, str) and not value.strip() else value

    def model_for(self, role: Role) -> str:
        explicit = {"main": self.model_main, "small": self.model_small, "judge": self.model_judge}[role]
        return explicit or DEFAULT_MODELS[self.llm_provider][role]

    @property
    def data_dir(self) -> Path:
        return _resolve(self.gk_data_dir)

    @property
    def checkpoint_db(self) -> Path:
        return _resolve(self.gk_checkpoint_db)

    @property
    def rag_dir(self) -> Path:
        return _resolve(self.gk_rag_dir)

    @property
    def audit_log(self) -> Path:
        return _resolve(self.gk_audit_log)

    @property
    def web_origins(self) -> list[str]:
        return [origin.strip() for origin in self.gk_web_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    # LangSmith용 LANGCHAIN_* 변수는 os.environ에 있어야 읽힌다.
    # pydantic-settings는 .env를 읽기만 하고 os.environ으로 내보내지 않는다.
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return Settings()
