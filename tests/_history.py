"""이력 검색 테스트 공용 헬퍼. FakeEmbedder와 메모리 저장소로 네트워크 없이 색인을 만든다."""

from collections.abc import Sequence
from datetime import date

from app.data_loader import load_history
from app.rag import FakeEmbedder, HistoryIndex, NumpyVectorStore, build_index
from app.schemas import HistoryItem


def make_item(
    history_id: str,
    service: str,
    change_type: str,
    outcome: str,
    text: str = "임시 허용 후 결제 지연",
) -> HistoryItem:
    return HistoryItem(
        history_id=history_id, date=date(2025, 1, 1), service=service, change_type=change_type,
        title=text, description=text, outcome=outcome,
    )  # fmt: skip


def make_index(items: Sequence[HistoryItem] | None = None) -> HistoryIndex:
    """items를 주지 않으면 실제 history.json 100건으로 만든다."""
    items = list(items if items is not None else load_history())
    store, embedder = NumpyVectorStore(), FakeEmbedder()
    build_index(items, embedder, store)
    return HistoryIndex(store, embedder, items)
