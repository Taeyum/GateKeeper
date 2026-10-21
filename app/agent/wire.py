"""Anthropic 구조화 출력용 와이어 스키마 (리뷰 J2).

공유 도메인 스키마(app/schemas.py)를 그대로 Anthropic 네이티브 구조화 출력(output_config.format)에 보내면
400 "Schema is too complex"로 거절된다. 실측 (claude-sonnet-5-5, claude-haiku-5-5)
    V0Output (ChangeRequest + Recommendation)   선택 필드 25, union 18   거절
    ChangeRequest 단독                          선택 필드 16, union 14   거절
    Recommendation 단독                         선택 필드  9, union  4   통과
    WireChangeRequest 단독, V0Wire              선택 필드  0, union 10/12 통과
공식 한도는 요청 전체 기준 선택 필드 24개, union(anyOf, nullable) 필드 16개인데, ChangeRequest 단독은 그 안인데도
거절된다. 한도 숫자 말고도 내부 컴파일 복잡도가 있고, 선택 필드(required에 없는 필드)가 문법 상태 공간을 키우는
쪽이 문제로 보인다. 그래서 필드를 모두 필수로 만든 와이어 모델만 통과한 것을 기준으로 삼는다.

쓰는 곳: v0 (V0Wire, 두 스키마를 한 번에 낸다), 5단계 v1 parse_request (WireChangeRequest). anthropic에서만 쓰고
ollama는 도메인 모델을 그대로 쓴다 (기록한 v0 측정이 유효하게). 변환은 to_domain()이 한다.

와이어 규칙
1. 모든 필드를 필수로 둔다 (선택 필드 0개).
2. null과 빈 값이 같은 뜻인 필드는 빈 문자열이나 빈 목록으로 받고 코드에서 None으로 바꾼다:
   source, ticket, reason, extra_changes, conditional_alternative.
3. v0이 낼 수 없는 필드는 뺀다: similar_history (v0은 이력을 받지 않는다).
4. null과 false처럼 뜻이 다른 세 값 필드와 시각, 포트 같은 값은 nullable로 남긴다.

도메인 모델과 필드가 어긋나지 않는지, 와이어가 복잡도 기준을 지키는지는 tests/test_wire.py가 고정하고,
실제 API 수락은 tests/test_llm_live.py(pytest -m llm)가 확인한다.
"""

from datetime import date

from pydantic import BaseModel, Field

from app.schemas import (
    ChangeRequest,
    ChangeType,
    Recommendation,
    Risk,
    StandardId,
    TimePrecision,
    Verdict,
    Violation,
)
from app.schemas import KstDatetime

_EMPTY = "본문에 없으면 빈 문자열"


class WireChangeRequest(BaseModel):
    target: str = Field(description="변경 대상 서비스 ID")
    change_type: ChangeType = Field(description="변경 유형")
    standard_change_id: StandardId | None = Field(description="표준 변경 후보면 STD ID, 아니면 null")
    start: KstDatetime | None = Field(description="작업 시작 시각 (+09:00). 알 수 없으면 null")
    end: KstDatetime | None = Field(description="작업 종료 시각. 알 수 없으면 null")
    time_precision: TimePrecision = Field(description="시각까지 있으면 exact, 날짜나 주말만 있으면 range")
    source: str = Field(description=f"방화벽 출발지 IP/CIDR. 단일 IP는 /32. {_EMPTY}")
    port: int | None = Field(description="목적지 포트. 본문에 없으면 null")
    expires_days: int | None = Field(description="허용 기한(일). 본문에 없으면 null")
    expires_at: KstDatetime | None = Field(description="허용 만료 시각. 본문에 없으면 null")
    has_rollback: bool | None = Field(description="롤백 계획이 적혀 있으면 true, 없으면 false, 해당 없는 유형이면 null")
    has_test_evidence: bool | None = Field(description="테스트 증적이 있으면 true, 없으면 false, 해당 없는 유형이면 null")
    is_emergency: bool = Field(description="요청자가 긴급이라고 표시했는지")
    ticket: str = Field(description=f"장애 티켓 번호(INC-연도-번호). {_EMPTY}")
    reason: str = Field(description=f"변경 사유. {_EMPTY}")
    restart_required: bool | None = Field(description="재시작이 필요한지. 언급이 없으면 null")
    extra_changes: list[str] = Field(description="함께 바꾸는 다른 설정. 언급이 없으면 빈 목록")
    cert_expires_at: date | None = Field(description="현재 인증서 만료일. 언급이 없으면 null")

    def to_domain(self) -> ChangeRequest:
        data = self.model_dump()
        for name in ("source", "ticket", "reason"):
            data[name] = (data[name] or "").strip() or None
        data["extra_changes"] = data["extra_changes"] or None
        return ChangeRequest.model_validate(data)


class WireRecommendation(BaseModel):
    violations: list[Violation] = Field(description="위반한 조항. 없으면 빈 목록")
    verdict: Verdict = Field(description="반려, 조건부 승인, 승인, 자동 승인 중 하나")
    risk: Risk | None = Field(description="높음, 중간, 낮음. 표준 변경은 null")
    emergency_valid: bool | None = Field(description="긴급 표시가 있을 때만 true 또는 false, 아니면 null")
    post_report_required: bool = Field(description="유효한 긴급 변경이면 true")
    explanation: str = Field(description="판정 이유 (한국어)")
    conditional_alternative: str = Field(description="반려나 조건부 승인일 때의 대안. 그 밖에는 빈 문자열")
    citations: list[str] = Field(description="인용한 rule_id")
    warnings: list[str] = Field(description="판단하지 못한 부분. 없으면 빈 목록")

    def to_domain(self) -> Recommendation:
        data = self.model_dump()
        data["conditional_alternative"] = data["conditional_alternative"].strip() or None
        # v0은 이력을 받지 않으므로 similar_history는 항상 null이다.
        return Recommendation.model_validate({**data, "similar_history": None})


class V0Wire(BaseModel):
    structured: WireChangeRequest
    recommendation: WireRecommendation


def change_request_schema(provider: str) -> type[BaseModel]:
    """parse_request가 provider에 보낼 구조화 출력 스키마. anthropic은 선택 필드가 없는 와이어 모델을 쓴다."""
    return WireChangeRequest if provider == "anthropic" else ChangeRequest


def to_change_request(result: BaseModel) -> ChangeRequest:
    """구조화 출력 결과를 도메인 ChangeRequest로 돌린다. 도메인 모델이면 그대로."""
    return result.to_domain() if isinstance(result, WireChangeRequest) else result  # type: ignore[return-value]
