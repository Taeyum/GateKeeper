"""RAG: 과거 변경 이력의 색인과 검색 (6단계).

저장소는 VectorStore 인터페이스 뒤에 있다. 구현을 바꿀 때(Chroma, pgvector 등)는 default_store() 한 곳만 고친다.
"""

from pathlib import Path

from app.config import Settings, get_settings
from app.data_loader import load_history
from app.rag.embeddings import Embedder, FakeEmbedder, OllamaEmbedder, get_embedder
from app.rag.index import (
    HistoryIndex,
    IndexMeta,
    IndexNotReady,
    RankedItem,
    build_index,
    document_of,
    history_digest,
    metadata_of,
)
from app.rag.store import NumpyVectorStore, SearchHit, VectorItem, VectorStore

__all__ = [
    "Embedder",
    "FakeEmbedder",
    "HistoryIndex",
    "IndexMeta",
    "IndexNotReady",
    "NumpyVectorStore",
    "OllamaEmbedder",
    "RankedItem",
    "SearchHit",
    "VectorItem",
    "VectorStore",
    "build_index",
    "default_store",
    "document_of",
    "get_embedder",
    "history_digest",
    "index_paths",
    "metadata_of",
    "open_history_index",
]


def index_paths(settings: Settings | None = None) -> tuple[Path, Path]:
    """(저장소 폴더, meta.json 경로). 둘 다 GK_RAG_DIR 아래에 둔다."""
    directory = (settings or get_settings()).rag_dir / "history"
    return directory, directory / "meta.json"


def default_store(settings: Settings | None = None) -> VectorStore:
    """기본 저장소. 다른 저장소로 바꿀 때 고치는 곳은 여기 하나다."""
    directory, _ = index_paths(settings)
    return NumpyVectorStore(directory)


def open_history_index(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> HistoryIndex:
    """history.json과 기본 저장소로 검색용 색인을 연다. 테스트는 embedder와 store를 넘겨 바꿔 끼운다."""
    settings = settings or get_settings()
    _, meta_path = index_paths(settings)
    return HistoryIndex(
        store if store is not None else default_store(settings),
        embedder or get_embedder(settings),
        load_history(),
        meta_path,
    )
