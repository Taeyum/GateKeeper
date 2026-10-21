"""실제 Ollama bge-m3로 이력을 색인하고 dev 질의로 검색한다 (pytest -m llm -s -k rag).

Ollama가 떠 있고 EMBED_MODEL이 설치돼 있어야 한다. 하이브리드 결과(정확 일치 먼저)의 재현율은 구조상 높아서 지표로서
의미가 약하다. 그래서 순수 의미 검색 상위 3건의 재현율을 진단 값으로 같이 계산해 보여 준다(단언하지 않는다).
dev 정답(similar_history)만 쓰고 eval split은 열지 않는다.
"""

import pytest

from app.agent.nodes.history_agent import HISTORY_TOP_K, INCIDENT_OUTCOME, build_query, merge_hits
from app.config import get_settings
from app.data_loader import load_history, load_requests
from app.mcp_server.models import HistoryHit
from app.rag import HistoryIndex, NumpyVectorStore, OllamaEmbedder, build_index
from app.rules_engine.context import effective_change_type

pytestmark = pytest.mark.llm


def _hits(ranked) -> list[HistoryHit]:
    return [HistoryHit(score=r.score, item=r.item) for r in ranked]


def test_bge_m3로_색인하고_dev_질의로_검색한다(tmp_path):
    settings = get_settings()
    embedder = OllamaEmbedder(settings.embed_model, settings.ollama_base_url, num_gpu=0)
    history = list(load_history())
    store = NumpyVectorStore(tmp_path / "idx")
    meta = build_index(history, embedder, store, tmp_path / "idx" / "meta.json")
    assert meta.count == 100 and store.count() == 100
    index = HistoryIndex(store, embedder, history, tmp_path / "idx" / "meta.json")
    index.ready()

    semantic_found = hybrid_found = total = 0
    lines = []
    for item in load_requests("dev"):
        gold = item.expected.similar_history
        structured = item.expected.structured
        if structured.change_type == "standard" or not gold:
            continue
        effective = effective_change_type(structured.change_type, False)
        query = build_query(structured, effective)  # type: ignore[arg-type]
        semantic = index.search(query, HISTORY_TOP_K)
        exact = index.search(
            query, HISTORY_TOP_K,
            where={"service": structured.target, "change_type": effective, "outcome": INCIDENT_OUTCOME},
        )  # fmt: skip
        merged = merge_hits(_hits(exact), _hits(semantic))
        assert len(semantic) == HISTORY_TOP_K and [r.score for r in semantic] == sorted((r.score for r in semantic), reverse=True)
        semantic_ids = [r.item.history_id for r in semantic]
        merged_ids = [h.item.history_id for h in merged]
        semantic_found += len(set(gold) & set(semantic_ids))
        hybrid_found += len(set(gold) & set(merged_ids))
        total += len(gold)
        lines.append(f"{item.request_id}: 정답 {gold}, 의미 검색 {semantic_ids}, 하이브리드 {merged_ids}")

    print("\n" + "\n".join(lines))
    print(f"의미 검색만 재현율@3 = {semantic_found}/{total}, 하이브리드 재현율@3 = {hybrid_found}/{total}")
    assert hybrid_found == total  # 정답이 같은 서비스, 같은 유형, 장애 이력이므로 정확 일치 단계가 모두 찾는다
