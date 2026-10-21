"""이력 색인과 검색 (docs/SPEC.md 5장, docs/plans/stage6.md).

문서는 이력의 title + description이고 메타데이터는 history_id, service, change_type, outcome이다.
색인 옆에 meta.json을 두어 어떤 임베딩 모델과 어떤 history.json으로 만들었는지 남긴다. 둘 중 하나가 바뀌었는데
색인을 다시 만들지 않았다면 조용히 엉뚱한 결과를 내지 않고 IndexNotReady로 멈춘다.

search는 기본적으로 메타데이터로 거르지 않는다(의미 검색 결과를 그대로 돌려준다). where를 주면 그 조건에
맞는 이력만을 대상으로 순위를 매긴다.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.rag.embeddings import Embedder
from app.rag.store import VectorItem, VectorStore
from app.schemas import KST, HistoryItem


class IndexNotReady(RuntimeError):
    """색인이 없거나 비어 있거나 현재 임베딩 모델/이력과 맞지 않는다. sh/build_index.sh로 다시 만든다."""


@dataclass(frozen=True)
class IndexMeta:
    embed_model: str
    history_sha256: str
    count: int
    built_at: str


@dataclass(frozen=True)
class RankedItem:
    item: HistoryItem
    score: float


def document_of(item: HistoryItem) -> str:
    return f"{item.title}\n{item.description}"


def metadata_of(item: HistoryItem) -> dict[str, str]:
    return {
        "history_id": item.history_id,
        "service": item.service,
        "change_type": item.change_type,
        "outcome": item.outcome,
    }


def history_digest(history: Sequence[HistoryItem]) -> str:
    """이력 내용의 해시. 순서와 상관없이 같은 내용이면 같은 값이다."""
    rows = sorted((h.model_dump(mode="json") for h in history), key=lambda row: row["history_id"])
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_index(
    history: Sequence[HistoryItem],
    embedder: Embedder,
    store: VectorStore,
    meta_path: Path | None = None,
    *,
    now: Callable[[], datetime] | None = None,
) -> IndexMeta:
    """저장소를 비우고 이력 전체를 다시 색인한다."""
    if not history:
        raise ValueError("색인할 이력이 없다")
    vectors = embedder.embed([document_of(item) for item in history])
    store.clear()
    store.add([VectorItem(id=item.history_id, vector=vector, metadata=metadata_of(item)) for item, vector in zip(history, vectors, strict=True)])
    meta = IndexMeta(
        embed_model=embedder.name,
        history_sha256=history_digest(history),
        count=len(history),
        built_at=(now or (lambda: datetime.now(KST)))().isoformat(),
    )
    if meta_path is not None:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


class HistoryIndex:
    def __init__(
        self, store: VectorStore, embedder: Embedder, history: Sequence[HistoryItem], meta_path: Path | None = None
    ) -> None:
        self._store, self._embedder, self._meta_path = store, embedder, meta_path
        self._by_id = {item.history_id: item for item in history}
        self._digest = history_digest(history)

    def check(self) -> None:
        """색인을 쓸 수 있는지 확인한다. 아니면 IndexNotReady."""
        hint = "bash sh/build_index.sh로 색인을 다시 만든다"
        if self._store.count() == 0:
            raise IndexNotReady(f"색인이 비어 있다. {hint}")
        if self._meta_path is None:
            return
        if not self._meta_path.exists():
            raise IndexNotReady(f"색인 meta.json이 없다. {hint}")
        meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
        if meta.get("embed_model") != self._embedder.name:
            raise IndexNotReady(f"색인은 임베딩 모델 {meta.get('embed_model')}로 만들었는데 지금은 {self._embedder.name}이다. {hint}")
        if meta.get("history_sha256") != self._digest or meta.get("count") != len(self._by_id):
            raise IndexNotReady(f"색인을 만든 뒤 history.json이 바뀌었다. {hint}")

    def ready(self) -> None:
        """검색을 실제로 할 수 있는지 확인한다. check()에 더해 임베딩 한 건을 만들어 본다(Ollama 연결과 모델 설치)."""
        self.check()
        self._embedder.embed(["연결 확인"])

    def search(self, query: str, k: int = 3, where: Mapping[str, str] | None = None) -> list[RankedItem]:
        self.check()
        [vector] = self._embedder.embed([query])
        return [RankedItem(item=self._by_id[hit.id], score=hit.score) for hit in self._store.search(vector, k, where)]
