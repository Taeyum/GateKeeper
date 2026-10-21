"""벡터 저장소 인터페이스와 numpy 구현 (docs/SPEC.md 5장, docs/plans/stage6.md).

RAG 색인이 기대는 저장소는 VectorStore 하나다. 구현은 NumpyVectorStore 하나이고 app/rag/__init__.py의
default_store()가 주입한다. Chroma나 pgvector로 바꿀 때는 이 프로토콜을 구현한 클래스 하나를 만들고
default_store()만 고치면 된다.

numpy를 고른 이유: 이력은 100건이다. 전수 코사인 검색은 근사 오차가 0이고 의존성이 없다.
수만 건 이상이면 ANN 저장소로 교체한다.

where는 메타데이터 일치 조건이고 순위를 매기기 전에 적용한다. "같은 서비스 + 같은 유형 + 장애"를 이력 전체에서
정확히 찾을 때 쓴다 (Chroma의 where, pgvector의 WHERE에 대응한다). 조건에 맞는 항목이 의미 검색 상위 k건 밖에
있어도 찾아야 해서, 순위를 매긴 뒤에 거르지 않는다.
"""

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class VectorItem:
    id: str
    vector: Sequence[float]
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    id: str
    score: float  # 코사인 유사도. 클수록 비슷하다
    metadata: Mapping[str, str]


class VectorStore(Protocol):
    def add(self, items: Sequence[VectorItem]) -> None: ...

    def search(
        self, vector: Sequence[float], k: int, where: Mapping[str, str] | None = None
    ) -> list[SearchHit]: ...

    def clear(self) -> None:
        """재색인용. 저장된 항목을 모두 지운다."""
        ...

    def count(self) -> int: ...


_VECTORS, _ITEMS = "vectors.npy", "items.json"


class NumpyVectorStore:
    """L2 정규화한 벡터를 행렬 하나로 들고 전수 코사인으로 찾는다. directory가 있으면 파일로 남겨 다시 열 수 있다."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = Path(directory) if directory is not None else None
        self._vectors = np.empty((0, 0), dtype=np.float32)
        self._items: list[dict] = []  # {"id": str, "metadata": dict}, 행렬의 행 순서와 같다
        if self._dir is not None and (self._dir / _VECTORS).exists() and (self._dir / _ITEMS).exists():
            self._vectors = np.load(self._dir / _VECTORS, allow_pickle=False)
            self._items = json.loads((self._dir / _ITEMS).read_text(encoding="utf-8"))
            if len(self._items) != len(self._vectors):
                raise ValueError(f"저장된 색인이 깨졌다: 항목 {len(self._items)}개, 벡터 {len(self._vectors)}개 ({self._dir})")

    def count(self) -> int:
        return len(self._items)

    def add(self, items: Sequence[VectorItem]) -> None:
        if not items:
            return
        new = np.asarray([list(item.vector) for item in items], dtype=np.float32)
        if new.ndim != 2 or new.shape[1] == 0:
            raise ValueError("벡터는 길이가 같은 비어 있지 않은 목록이어야 한다")
        if self._items and new.shape[1] != self._vectors.shape[1]:
            raise ValueError(f"벡터 차원이 다르다: 저장된 {self._vectors.shape[1]}, 새 {new.shape[1]}")
        norms = np.linalg.norm(new, axis=1)
        if (norms == 0).any():
            raise ValueError("영벡터는 넣을 수 없다")
        seen = {entry["id"] for entry in self._items}
        for item in items:
            if item.id in seen:
                raise ValueError(f"id가 중복이다: {item.id}")
            seen.add(item.id)
        new = new / norms[:, None]
        self._vectors = new if not self._items else np.vstack([self._vectors, new])
        self._items += [{"id": item.id, "metadata": dict(item.metadata)} for item in items]
        self._persist()

    def search(
        self, vector: Sequence[float], k: int, where: Mapping[str, str] | None = None
    ) -> list[SearchHit]:
        if k < 1:
            raise ValueError("k는 1 이상이어야 한다")
        if not self._items:
            return []
        query = np.asarray(list(vector), dtype=np.float32)
        if query.ndim != 1 or query.shape[0] != self._vectors.shape[1]:
            raise ValueError(f"질의 벡터 차원이 다르다: 저장된 {self._vectors.shape[1]}, 질의 {query.shape[0] if query.ndim == 1 else query.shape}")
        norm = np.linalg.norm(query)
        if norm == 0:
            raise ValueError("영벡터로는 검색할 수 없다")
        query = query / norm

        rows = np.arange(len(self._items))
        if where:
            # 순위를 매기기 전에 조건으로 후보를 줄인다 (이력 전체에서 정확히 찾으려는 것)
            keep = [i for i, entry in enumerate(self._items) if all(entry["metadata"].get(key) == value for key, value in where.items())]
            if not keep:
                return []
            rows = np.asarray(keep)
        scores = self._vectors[rows] @ query
        order = np.argsort(-scores, kind="stable")[:k]  # 점수가 같으면 넣은 순서
        return [
            SearchHit(id=self._items[rows[o]]["id"], score=float(scores[o]), metadata=dict(self._items[rows[o]]["metadata"]))
            for o in order
        ]

    def clear(self) -> None:
        self._vectors = np.empty((0, 0), dtype=np.float32)
        self._items = []
        if self._dir is not None:
            for name in (_VECTORS, _ITEMS):
                (self._dir / name).unlink(missing_ok=True)

    def _persist(self) -> None:
        if self._dir is None:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        # 임시 파일에 쓴 뒤 바꿔 끼워서, 쓰다 멈춰도 반쯤 쓴 파일이 남지 않게 한다
        tmp_vectors, tmp_items = self._dir / f"{_VECTORS}.tmp", self._dir / f"{_ITEMS}.tmp"
        with tmp_vectors.open("wb") as handle:
            np.save(handle, self._vectors, allow_pickle=False)
        tmp_items.write_text(json.dumps(self._items, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_vectors, self._dir / _VECTORS)
        os.replace(tmp_items, self._dir / _ITEMS)
