"""v1 노드가 상태에 남기는 결과 모델 (docs/SPEC.md 2장).

공유 스키마(app/schemas.py)는 건드리지 않고 에이전트 안에서만 쓰는 모델을 여기 둔다.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RuleNote(BaseModel):
    """LLM이 조항 원문을 읽고 쓴 조항별 쉬운 설명. 위반 근거(Violation.evidence)는 엔진 문장이고 이것과 별개다."""

    rule_id: str
    note: str


class SubagentResult(BaseModel):
    """③ 서브에이전트 하나의 결과.

    tool_output은 Tool 원본이고 판단의 근거다. summary와 notes는 LLM이 쓴 설명이라 판정에 쓰지 않는다.
    Tool이 끝내 실패하면 status가 unavailable이고 summary가 "확인 불가"를 말한다 (SPEC 부분 실패).
    """

    status: Literal["ok", "unavailable"] = "ok"
    tool_output: dict[str, Any] | None = None
    summary: str = ""
    notes: list[RuleNote] = Field(default_factory=list)


class AlternativePlan(BaseModel):
    """decide가 조건부 대안을 문장으로 조립하기 전의 구성 요소 (리뷰 K4).

    7단계 API가 원래 일정과 대안 일정의 차이(alternative_diff)를 코드로 계산할 때 쓴다. Recommendation은 바꾸지 않는다.
    start와 end는 규칙 엔진 check_window를 통과한(또는 엔진이 찾은) 시각이고, 일정 대안이 없으면 둘 다 None이다.
    """

    start: datetime | None = None
    end: datetime | None = None
    conditions: list[str] = Field(default_factory=list)
    # LLM 대안이 끝내 위반이라 엔진이 찾은 가장 빠른 통과 시간대(next_valid_window)로 대체했는지
    fallback: bool = False
