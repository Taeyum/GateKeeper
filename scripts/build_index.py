"""이력 RAG 색인을 다시 만든다 (docs/SPEC.md 9장).

    python scripts/build_index.py              history.json 전체를 임베딩해 GK_RAG_DIR 아래에 색인한다
    bash sh/build_index.sh                     같은 일을 ollama 서버와 모델을 확인하고 한다

항상 저장소를 비우고 처음부터 다시 만든다(이력 100건이라 몇 초). 임베딩 모델(EMBED_MODEL)이나 history.json을
바꾼 뒤에는 이 명령으로 다시 만들어야 하고, 안 하면 검색이 IndexNotReady로 멈춘다.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.data_loader import load_history  # noqa: E402
from app.rag import NumpyVectorStore, build_index, get_embedder, index_paths  # noqa: E402
from app.rag.embeddings import Embedder  # noqa: E402


def main(argv: list[str] | None = None, *, embedder: Embedder | None = None) -> int:
    parser = argparse.ArgumentParser(description="이력 RAG 색인을 다시 만든다.")
    parser.add_argument("--dir", type=Path, default=None, help="색인 폴더. 기본값은 GK_RAG_DIR/history")
    args = parser.parse_args(argv)

    settings = get_settings()
    store_dir, meta_path = index_paths(settings)
    if args.dir is not None:
        store_dir, meta_path = args.dir, args.dir / "meta.json"
    embedder = embedder or get_embedder(settings)
    history = load_history()
    print(f"이력 {len(history)}건을 {embedder.name}로 색인한다 -> {store_dir}")
    try:
        meta = build_index(history, embedder, NumpyVectorStore(store_dir), meta_path)
    except Exception as exc:  # Ollama가 꺼져 있거나 모델이 없을 때 등. 원인을 한 줄로 알린다
        print(f"ERROR: 색인을 만들지 못했다 ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    print(f"색인 완료: {meta.count}건, 임베딩 {meta.embed_model}, {meta.built_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
