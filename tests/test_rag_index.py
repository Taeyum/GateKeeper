"""이력 색인과 검색 테스트 (FakeEmbedder, 네트워크 없음)."""

import json
from datetime import date

import pytest

from app import data_loader
from app.data_loader import load_history
from app.rag import (
    FakeEmbedder,
    HistoryIndex,
    IndexNotReady,
    NumpyVectorStore,
    OllamaEmbedder,
    build_index,
    document_of,
    get_embedder,
    history_digest,
    index_paths,
    metadata_of,
    open_history_index,
)
from app.schemas import HistoryItem


@pytest.fixture
def history() -> list[HistoryItem]:
    return list(load_history())


def by_id(history, history_id: str) -> HistoryItem:
    return next(item for item in history if item.history_id == history_id)


def built(history, tmp_path):
    directory = tmp_path / "idx"
    store, embedder, meta_path = NumpyVectorStore(directory), FakeEmbedder(), directory / "meta.json"
    meta = build_index(history, embedder, store, meta_path)
    return store, embedder, meta_path, meta


def synthetic(history_id: str, service: str, change_type: str, outcome: str, text: str = "임시 허용 후 결제 지연") -> HistoryItem:
    return HistoryItem(
        history_id=history_id, date=date(2025, 1, 1), service=service, change_type=change_type,
        title=text, description=text, outcome=outcome,
    )  # fmt: skip


# ---------------------------------------------------------------------------
# 문서와 메타데이터
# ---------------------------------------------------------------------------


def test_문서는_title과_description이고_메타데이터는_네_가지다(history):
    item = by_id(history, "HIS-049")
    assert document_of(item) == f"{item.title}\n{item.description}"
    assert metadata_of(item) == {
        "history_id": "HIS-049", "service": "payments-db", "change_type": "firewall", "outcome": "장애",
    }  # fmt: skip


def test_색인을_만들면_이력_전체가_들어가고_meta에_모델과_해시가_남는다(history, tmp_path):
    store, embedder, meta_path, meta = built(history, tmp_path)
    assert store.count() == len(history) == meta.count == 100
    saved = json.loads(meta_path.read_text(encoding="utf-8"))
    assert saved["embed_model"] == embedder.name and saved["history_sha256"] == history_digest(history)
    assert saved["count"] == 100 and saved["built_at"].endswith("+09:00")


def test_이력_해시는_순서와_상관없고_내용이_바뀌면_달라진다(history):
    assert history_digest(history) == history_digest(list(reversed(history)))
    changed = [history[0].model_copy(update={"title": history[0].title + "!"}), *history[1:]]
    assert history_digest(changed) != history_digest(history)


def test_다시_만들면_처음부터_새로_채운다(history, tmp_path):
    store, embedder, meta_path, _ = built(history, tmp_path)
    build_index(history, embedder, store, meta_path)
    assert store.count() == 100  # 중복 id로 실패하거나 200건이 되지 않는다


# ---------------------------------------------------------------------------
# 검색
# ---------------------------------------------------------------------------


def test_질의와_단어가_겹치는_이력이_위로_오고_점수는_내림차순이다(history, tmp_path):
    store, embedder, meta_path, _ = built(history, tmp_path)
    index = HistoryIndex(store, embedder, history, meta_path)
    target = by_id(history, "HIS-034")
    hits = index.search(target.title, 3)
    assert hits[0].item.history_id == "HIS-034" and isinstance(hits[0].item, HistoryItem)
    assert len(hits) == 3 and [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_기본_검색은_메타데이터로_거르지_않는다(tmp_path):
    items = [
        synthetic("S-1", "svc-a", "firewall", "장애"),
        synthetic("S-2", "svc-b", "deploy", "성공"),
        synthetic("S-3", "svc-c", "db_change", "실패 후 롤백"),
    ]
    store = NumpyVectorStore()
    embedder = FakeEmbedder()
    build_index(items, embedder, store)
    hits = HistoryIndex(store, embedder, items).search("임시 허용 후 결제 지연", 3)
    assert {h.item.service for h in hits} == {"svc-a", "svc-b", "svc-c"}  # 서비스, 유형, 결과가 달라도 모두 나온다


def test_where를_주면_그_조건의_이력만_대상이고_의미_검색_상위_밖이어도_찾는다(history, tmp_path):
    store, embedder, meta_path, _ = built(history, tmp_path)
    index = HistoryIndex(store, embedder, history, meta_path)
    query = "인증서 갱신 만료 교체"  # payments-db 방화벽 장애 이력(HIS-049)과 무관한 질의
    assert "HIS-049" not in [h.item.history_id for h in index.search(query, 3)]
    exact = index.search(query, 3, where={"service": "payments-db", "change_type": "firewall", "outcome": "장애"})
    assert "HIS-049" in [h.item.history_id for h in exact]
    assert all((h.item.service, h.item.change_type, h.item.outcome) == ("payments-db", "firewall", "장애") for h in exact)


def test_디스크에서_다시_열어도_같은_결과다(history, tmp_path):
    store, embedder, meta_path, _ = built(history, tmp_path)
    first = [h.item.history_id for h in HistoryIndex(store, embedder, history, meta_path).search("결제 승인 지연", 3)]
    reopened = HistoryIndex(NumpyVectorStore(tmp_path / "idx"), FakeEmbedder(), history, meta_path)
    assert [h.item.history_id for h in reopened.search("결제 승인 지연", 3)] == first


# ---------------------------------------------------------------------------
# 색인이 오래됐을 때
# ---------------------------------------------------------------------------


def test_빈_색인이면_안내와_함께_멈춘다(history, tmp_path):
    index = HistoryIndex(NumpyVectorStore(tmp_path / "none"), FakeEmbedder(), history, tmp_path / "none" / "meta.json")
    with pytest.raises(IndexNotReady, match="sh/build_index.sh"):
        index.search("아무거나", 3)


def test_meta가_없으면_멈춘다(history, tmp_path):
    store, embedder, meta_path, _ = built(history, tmp_path)
    meta_path.unlink()
    with pytest.raises(IndexNotReady, match="meta.json"):
        HistoryIndex(store, embedder, history, meta_path).search("x", 1)


def test_임베딩_모델이_바뀌면_조용히_엉뚱한_결과를_내지_않고_멈춘다(history, tmp_path):
    store, _, meta_path, _ = built(history, tmp_path)

    class OtherEmbedder(FakeEmbedder):
        name = "다른-모델"

    with pytest.raises(IndexNotReady, match="임베딩 모델"):
        HistoryIndex(store, OtherEmbedder(), history, meta_path).search("x", 1)


def test_색인을_만든_뒤_이력이_바뀌면_멈추고_다시_만들면_풀린다(history, tmp_path):
    store, embedder, meta_path, _ = built(history, tmp_path)
    changed = history[1:]  # 한 건이 사라졌다
    with pytest.raises(IndexNotReady, match="history.json이 바뀌었다"):
        HistoryIndex(store, embedder, changed, meta_path).search("x", 1)
    build_index(changed, embedder, store, meta_path)
    assert HistoryIndex(store, embedder, changed, meta_path).search("결제", 1)


# ---------------------------------------------------------------------------
# 기본 구성
# ---------------------------------------------------------------------------


def test_fake_provider는_FakeEmbedder_나머지는_provider와_상관없이_CPU_Ollama_임베딩이다(make_settings):
    assert isinstance(get_embedder(make_settings(llm_provider="fake")), FakeEmbedder)
    for provider in ("ollama", "anthropic"):
        embedder = get_embedder(make_settings(llm_provider=provider, embed_model="bge-m3"))
        assert isinstance(embedder, OllamaEmbedder) and embedder.name == "bge-m3"
        assert embedder._client.num_gpu == 0  # LLM과 GPU를 두고 다투지 않는다


def test_색인_위치는_GK_RAG_DIR을_따르고_기본_구성으로_연다(history, tmp_path, monkeypatch, make_settings):
    settings = make_settings(llm_provider="fake", gk_rag_dir=tmp_path / "rag")
    directory, meta_path = index_paths(settings)
    assert directory == tmp_path / "rag" / "history" and meta_path == directory / "meta.json"

    build_index(history, FakeEmbedder(), NumpyVectorStore(directory), meta_path)
    data_loader.clear_caches()
    index = open_history_index(settings)
    assert index.search(by_id(history, "HIS-049").title, 3)[0].item.history_id == "HIS-049"
