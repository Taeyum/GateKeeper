"""MCP Tool 출력 모델.

규칙 엔진 결과(PolicyCheckResult, Violation)와 데이터 파일 모델(Incident, FreezePeriod,
ScheduledChange)은 app/schemas.py의 것을 그대로 쓴다. 여기에는 Tool 응답용으로 새로 필요한
모델만 둔다. schemas.py는 2단계와 공유하는 인터페이스라 이번 단계에서 건드리지 않는다.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import FreezePeriod, KstDatetime, ScheduledChange, Violation


class ServiceInfo(BaseModel):
    """get_service_info 응답. CMDB에서 영향도 분석에 필요한 값만 담는다."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: str
    tier: str = Field(description="Tier-1, Tier-2, Tier-3")
    owner_team: str
    zone: str
    ports: list[int]
    default_port: int | None = Field(
        description="요청에 포트가 없을 때 쓰는 포트. CMDB ports의 첫 번째 값이고, 포트가 없는 서비스는 null"
    )
    depends_on: list[str] = Field(description="이 서비스가 의존하는 서비스")
    dependents: list[str] = Field(description="이 서비스에 의존하는 서비스")


class CalendarResult(BaseModel):
    """get_calendar 응답. 조회 구간과 겹치는 것만 담는다."""

    model_config = ConfigDict(extra="forbid")

    target: str
    start: KstDatetime
    end: KstDatetime
    freeze_periods: list[FreezePeriod] = Field(description="대상이 범위(scope)에 속하거나 범위가 *인 동결 기간")
    scheduled_changes: list[ScheduledChange] = Field(description="같은 대상에 이미 승인된 변경")


class WindowCheckResult(BaseModel):
    """check_window 응답. 리스트를 바로 돌려주면 MCP가 {"result": [...]}로 감싸므로 모델로 고정한다."""

    model_config = ConfigDict(extra="forbid")

    violations: list[Violation] = Field(default_factory=list)
