"""scripts/build_index.py 테스트 (FakeEmbedder)."""

import json

from app.data_loader import load_history
from app.rag import FakeEmbedder, HistoryIndex, NumpyVectorStore
from scripts.build_index import main


def test_색인을_만들고_건수를_알린다(tmp_path, capsys):
    directory = tmp_path / "idx"
    assert main(["--dir", str(directory)], embedder=FakeEmbedder()) == 0
    out = capsys.readouterr().out
    assert "이력 100건을 fake-hash-embedding로 색인한다" in out and "색인 완료: 100건" in out
    assert NumpyVectorStore(directory).count() == 100
    assert json.loads((directory / "meta.json").read_text(encoding="utf-8"))["count"] == 100


def test_두_번_돌려도_건수가_그대로다(tmp_path):
    directory = tmp_path / "idx"
    assert main(["--dir", str(directory)], embedder=FakeEmbedder()) == 0
    assert main(["--dir", str(directory)], embedder=FakeEmbedder()) == 0
    assert NumpyVectorStore(directory).count() == 100


class DownEmbedder(FakeEmbedder):
    name = "fake-hash-embedding"

    def embed(self, texts):
        raise ConnectionError("ollama에 연결할 수 없다")


def test_임베딩이_실패하면_한_줄로_알리고_이전_색인은_그대로_둔다(tmp_path, capsys):
    directory = tmp_path / "idx"
    assert main(["--dir", str(directory)], embedder=FakeEmbedder()) == 0
    capsys.readouterr()

    assert main(["--dir", str(directory)], embedder=DownEmbedder()) == 1
    err = capsys.readouterr().err
    assert "ERROR: 색인을 만들지 못했다 (ConnectionError" in err
    # 임베딩을 먼저 하고 저장소를 비우므로 실패해도 쓰던 색인이 남는다
    index = HistoryIndex(NumpyVectorStore(directory), FakeEmbedder(), load_history(), directory / "meta.json")
    assert index.search("결제 승인 지연", 3)
