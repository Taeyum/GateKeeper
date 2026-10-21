"""벡터 저장소 계약 테스트. NumpyVectorStore(메모리, 디스크)와 일부러 따로 만든 ListStore가 같은 테스트를 통과한다.

ListStore가 통과한다는 것은 VectorStore 인터페이스(add, search, clear, count)만 지키면 구현을 바꿔 끼울 수 있다는
뜻이다 (Chroma나 pgvector로 바꿀 때 클래스 하나만 바꾸는 설계의 근거).
"""

import math
from collections.abc import Mapping, Sequence

import pytest

from app.rag.store import NumpyVectorStore, SearchHit, VectorItem, VectorStore


class ListStore:
    """파이썬 리스트만 쓰는 가장 단순한 구현. 계약을 확인하려는 테스트 전용이다."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, list[float], dict]] = []

    @staticmethod
    def _unit(vector: Sequence[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            raise ValueError("영벡터")
        return [x / norm for x in vector]

    def add(self, items: Sequence[VectorItem]) -> None:
        for item in items:
            if any(row[0] == item.id for row in self.rows):
                raise ValueError(f"id가 중복이다: {item.id}")
            vector = self._unit(item.vector)
            if self.rows and len(vector) != len(self.rows[0][1]):
                raise ValueError("차원이 다르다")
            self.rows.append((item.id, vector, dict(item.metadata)))

    def search(self, vector: Sequence[float], k: int, where: Mapping[str, str] | None = None) -> list[SearchHit]:
        if k < 1:
            raise ValueError("k")
        if not self.rows:
            return []
        query = self._unit(vector)
        if len(query) != len(self.rows[0][1]):
            raise ValueError("차원이 다르다")
        scored = [
            SearchHit(id=row[0], score=sum(a * b for a, b in zip(row[1], query)), metadata=row[2])
            for row in self.rows
            if not where or all(row[2].get(key) == value for key, value in where.items())
        ]
        return sorted(scored, key=lambda hit: -hit.score)[:k]  # sorted는 안정 정렬이라 같은 점수는 넣은 순서

    def clear(self) -> None:
        self.rows = []

    def count(self) -> int:
        return len(self.rows)


@pytest.fixture(params=["numpy-메모리", "numpy-디스크", "list"])
def store(request, tmp_path) -> VectorStore:
    if request.param == "numpy-메모리":
        return NumpyVectorStore()
    if request.param == "numpy-디스크":
        return NumpyVectorStore(tmp_path / "idx")
    return ListStore()


def item(item_id: str, vector, **metadata) -> VectorItem:
    return VectorItem(id=item_id, vector=vector, metadata=metadata)


@pytest.fixture
def filled(store) -> VectorStore:
    """질의 (1, 0)에 대한 코사인: a=1.0, b=0.707, c=0.0, d=-1.0. a와 c는 그룹 x, b와 d는 그룹 y. d만 tag=rare."""
    store.add([item("a", [1, 0], group="x"), item("b", [1, 1], group="y"), item("c", [0, 1], group="x"), item("d", [-1, 0], group="y", tag="rare")])
    return store


def test_코사인_순으로_상위_k건을_돌려준다(filled):
    hits = filled.search([1, 0], 3)
    assert [h.id for h in hits] == ["a", "b", "c"]
    assert [round(h.score, 4) for h in hits] == [1.0, 0.7071, 0.0]
    assert hits[0].metadata == {"group": "x"}


def test_k가_건수보다_크면_전부_돌려주고_빈_저장소는_빈_목록이다(filled, store):
    assert [h.id for h in filled.search([1, 0], 10)] == ["a", "b", "c", "d"]
    store.clear()
    assert store.search([1, 0], 3) == [] and store.count() == 0


def test_벡터_크기는_점수에_영향이_없다(store):
    store.add([item("small", [1, 0]), item("big", [100, 0])])
    scores = {h.id: h.score for h in store.search([5, 0], 2)}
    assert scores["small"] == pytest.approx(1.0) and scores["big"] == pytest.approx(1.0)


def test_점수가_같으면_넣은_순서다(store):
    store.add([item("first", [1, 0]), item("second", [2, 0]), item("third", [3, 0])])
    assert [h.id for h in store.search([1, 0], 3)] == ["first", "second", "third"]


def test_where는_순위를_매기기_전에_적용한다(filled):
    # 그룹 y만 대상이면 전체 1등(a)이 아니라 y 안의 1등(b)이 나온다
    assert [h.id for h in filled.search([1, 0], 1, where={"group": "y"})] == ["b"]


def test_where에_맞는_항목이_전체_꼴찌여도_k가_1이어도_찾는다(filled):
    # 질의 (1, 0)에서 d는 전체 꼴찌(-1.0)다. 순위를 매긴 뒤에 거르면 k=1에서 빈 목록이 된다.
    hits = filled.search([1, 0], 1, where={"tag": "rare"})
    assert [h.id for h in hits] == ["d"] and hits[0].score == pytest.approx(-1.0)
    assert [h.id for h in filled.search([1, 0], 2, where={"group": "y"})] == ["b", "d"]


def test_where가_여러_키면_모두_맞아야_하고_없는_키나_맞는_항목이_없으면_빈_목록이다(store):
    store.add([item("p", [1, 0], service="s1", outcome="장애"), item("q", [0, 1], service="s1", outcome="성공"), item("r", [1, 1], service="s2", outcome="장애")])
    assert [h.id for h in store.search([1, 0], 3, where={"service": "s1", "outcome": "장애"})] == ["p"]
    assert store.search([1, 0], 3, where={"service": "s3"}) == []
    assert store.search([1, 0], 3, where={"nope": "x"}) == []
    assert len(store.search([1, 0], 3, where=None)) == 3 and len(store.search([1, 0], 3, where={})) == 3


def test_잘못된_입력은_ValueError다(store):
    store.add([item("a", [1, 0])])
    with pytest.raises(ValueError):
        store.add([item("a", [0, 1])])  # id 중복
    with pytest.raises(ValueError):
        store.add([item("z", [1, 0, 0])])  # 차원 불일치
    with pytest.raises(ValueError):
        store.add([item("zero", [0, 0])])  # 영벡터
    with pytest.raises(ValueError):
        store.search([1, 0, 0], 1)  # 질의 차원 불일치
    with pytest.raises(ValueError):
        store.search([1, 0], 0)  # k < 1
    assert store.count() == 1  # 실패한 add는 아무것도 바꾸지 않았다


def test_clear_뒤에는_다른_차원으로_다시_채울_수_있다(store):
    store.add([item("a", [1, 0])])
    store.clear()
    store.add([item("a", [1, 0, 0])])
    assert store.count() == 1 and store.search([1, 0, 0], 1)[0].id == "a"


# ---------------------------------------------------------------------------
# numpy 구현만의 성질: 파일로 남기기
# ---------------------------------------------------------------------------


def test_디스크_저장소는_다시_열어도_같은_결과를_낸다(tmp_path):
    first = NumpyVectorStore(tmp_path / "idx")
    first.add([item("a", [1, 0], group="x"), item("b", [1, 1], group="y")])
    reopened = NumpyVectorStore(tmp_path / "idx")
    assert reopened.count() == 2
    assert [(h.id, round(h.score, 4)) for h in reopened.search([1, 0], 2)] == [("a", 1.0), ("b", 0.7071)]
    assert reopened.search([1, 0], 1, where={"group": "y"})[0].metadata == {"group": "y"}
    assert not list((tmp_path / "idx").glob("*.tmp"))  # 임시 파일이 남지 않는다


def test_add를_여러_번_해도_이어_붙고_clear는_파일을_지운다(tmp_path):
    directory = tmp_path / "idx"
    store = NumpyVectorStore(directory)
    store.add([item("a", [1, 0])])
    store.add([item("b", [0, 1])])
    assert NumpyVectorStore(directory).count() == 2
    store.clear()
    assert NumpyVectorStore(directory).count() == 0 and not (directory / "vectors.npy").exists()


def test_저장된_파일이_서로_안_맞으면_멈춘다(tmp_path):
    directory = tmp_path / "idx"
    NumpyVectorStore(directory).add([item("a", [1, 0]), item("b", [0, 1])])
    (directory / "items.json").write_text('[{"id": "a", "metadata": {}}]', encoding="utf-8")  # 벡터는 2개, 항목은 1개
    with pytest.raises(ValueError, match="깨졌다"):
        NumpyVectorStore(directory)
