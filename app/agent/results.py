"""v1 노드가 상태에 남기는 결과 모델 (docs/SPEC.md 2장).

공유 스키마(app/schemas.py)는 건드리지 않고 에이전트 안에서만 쓰는 모델을 여기 둔다.
"""

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
