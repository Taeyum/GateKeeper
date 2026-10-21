"""임베딩 (docs/SPEC.md 5장). RAG 임베딩은 LLM provider와 상관없이 항상 로컬 Ollama(EMBED_MODEL)다.

테스트와 fake provider에서는 네트워크 없이 도는 결정론 임베딩(FakeEmbedder)을 쓴다. 실제 의미는 모르고,
단어가 많이 겹칠수록 코사인이 크다는 성질만 있다.

Ollama 임베딩은 CPU(num_gpu=0)로 돌린다. VRAM 6GB에서 LLM(qwen3:4b 4.2GB)과 bge-m3(1.1GB)가 함께 올라가지 못해
서로를 내리고 다시 올리는 일을 피하려는 것이다. 질의 한 건이라 CPU여도 지연 영향이 작다.
"""

import hashlib
import re
from collections.abc import Sequence
from typing import Protocol

from app.config import Settings, get_settings


class Embedder(Protocol):
    name: str  # 색인 meta에 남기는 이름. 모델이 바뀌면 색인이 오래된 것으로 판정한다

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """해시 기반 단어 주머니 임베딩. 같은 입력은 언제나 같은 벡터다 (파이썬 hash()는 실행마다 달라서 쓰지 않는다)."""

    name = "fake-hash-embedding"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in re.findall(r"\w+", text.lower()):
                index = int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:4], "big") % self.dim
                vector[index] += 1.0
            if not any(vector):
                vector[0] = 1.0  # 영벡터는 저장소가 거절한다. 빈 문자열도 어딘가에는 놓는다
            vectors.append(vector)
        return vectors


class OllamaEmbedder:
    def __init__(self, model: str, base_url: str, *, num_gpu: int = 0) -> None:
        from langchain_ollama import OllamaEmbeddings

        self.name = model
        self._client = OllamaEmbeddings(model=model, base_url=base_url, num_gpu=num_gpu)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._client.embed_documents(list(texts))


def get_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    if settings.llm_provider == "fake":
        return FakeEmbedder()
    return OllamaEmbedder(settings.embed_model, settings.ollama_base_url, num_gpu=0)
