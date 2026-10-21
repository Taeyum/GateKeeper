"""공유 스키마.

2단계(규칙 엔진, 클라우드)와 3단계(MCP 서버, 로컬)가 같이 기대는 인터페이스다.
필드나 타입을 바꾸기 전에 사용자에게 먼저 묻는다 (CLAUDE.md).

구성
1. 공통 타입과 KST 시각
2. ChangeRequest / Violation / Recommendation (SPEC 2장)
3. 규칙 엔진 입출력 모델
4. 정답 라벨 모델
5. 데이터 파일 모델과 GKData (규칙 엔진에 데이터를 주입하는 묶음)
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, AliasChoices, BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 1. 공통 타입과 KST 시각
# ---------------------------------------------------------------------------

# rules.json defaults.timezone과 같은 값이다. 둘이 어긋나지 않는지는 테스트가 확인한다.
KST = ZoneInfo("Asia/Seoul")

ChangeType = Literal["firewall", "deploy", "db_change", "standard"]
Severity = Literal["forbidden", "fixable"]
Verdict = Literal["반려", "조건부 승인", "승인", "자동 승인"]
Risk = Literal["높음", "중간", "낮음"]
Route = Literal["standard", "general", "emergency"]
TimePrecision = Literal["exact", "range"]
StandardId = Literal["STD-01", "STD-02"]


def _as_kst(value: datetime) -> datetime:
    """시각은 모두 KST aware로 맞춘다. 시간대가 없는 입력은 KST로 본다."""
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


KstDatetime = Annotated[datetime, AfterValidator(_as_kst)]


# ---------------------------------------------------------------------------
# 2. ChangeRequest / Violation / Recommendation
# ---------------------------------------------------------------------------


class ChangeRequest(BaseModel):
    """① 구조화 결과. 필드 이름은 정답 라벨 expected.structured와 같다.

    본문에 없는 값은 null이다. target과 change_type만 필수라서, 둘을 못 뽑으면
    검증 실패가 되어 parse_request의 1회 재시도 대상이 된다.

    해석 결정 (docs/SPEC.md 3장, 확정 사항)
    - 작업 구간: 반열린 구간 [start, end). 금요일 17:00~18:00은 CHG-01 통과.
    - 경계값: 정확히 24시간 전 요청은 CHG-05 통과. 기한 30일은 통과, 31일은 위반.
    - 종료 시각 없음: 시작 후 1시간.
    - 시각 없이 날짜나 "주말"만 있음: time_precision = range, start/end를 그 범위 전체로 둔다.
      규칙 엔진은 범위 전체를 작업 구간으로 보고 겹치면 위반.
    - 포트 없음: CMDB ports의 첫 번째 값.
    - 출발지 없음: 0.0.0.0/0.
    - expires_at만 있음: 작업 시작 시각부터 기한 일수를 계산.
    - 긴급 티켓 열림: status == "open"만 본다.
    - 표준 변경 확정: LLM이 붙인 standard_change_id를 믿지 않는다. STD-01은
      restart_required == false 이고 extra_changes가 비어 있을 때, STD-02는
      cert_expires_at이 작업일 기준 30일 이내일 때만 코드가 확정한다.
      값이 null이면 표준 변경으로 보지 않는다 (fail-safe).
    """

    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="변경 대상 서비스 ID (예: payments-db)")
    change_type: ChangeType = Field(description="변경 유형")
    standard_change_id: StandardId | None = Field(None, description="표준 변경 후보. 확정은 코드가 한다")
    start: KstDatetime | None = Field(None, description="작업 시작 시각 (Asia/Seoul). 본문에 없으면 null")
    end: KstDatetime | None = Field(None, description="작업 종료 시각. 본문에 없으면 null")
    time_precision: TimePrecision = Field("exact", description="시각까지 명시했으면 exact, 날짜나 주말만 있으면 range")
    source: str | None = Field(None, description="방화벽 출발지 IP/CIDR. 단일 IP는 /32. 본문에 없으면 null")
    port: int | None = Field(None, description="목적지 포트. 본문에 없으면 null")
    expires_days: int | None = Field(None, description="허용 기한(일). 본문에 없으면 null")
    expires_at: KstDatetime | None = Field(None, description="허용 만료 시각. 본문에 없으면 null")
    has_rollback: bool | None = Field(None, description="롤백 계획이 적혀 있는지")
    has_test_evidence: bool | None = Field(None, description="스테이징 테스트 증적이 있는지")
    is_emergency: bool = Field(False, description="요청자가 긴급이라고 표시했는지")
    ticket: str | None = Field(None, description="긴급 변경의 장애 티켓 번호 (INC-연도-번호)")
    reason: str | None = Field(None, description="변경 사유")
    restart_required: bool | None = Field(None, description="재시작이 필요한지. 언급이 없으면 null")
    extra_changes: list[str] | None = Field(None, description="함께 바꾸는 다른 설정. 언급이 없으면 null")
    cert_expires_at: date | None = Field(None, description="현재 인증서 만료일. 언급이 없으면 null")


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: Severity
    evidence: str = Field(description="무엇이 왜 걸렸는지 한국어 한 문장")


class Recommendation(BaseModel):
    """④ 결과이자 평가 대상. v0, v1, v2가 같은 스키마로 낸다."""

    model_config = ConfigDict(extra="forbid")

    violations: list[Violation] = Field(default_factory=list)
    verdict: Verdict
    risk: Risk | None = None
    similar_history: list[str] | None = None
    emergency_valid: bool | None = None
    post_report_required: bool = False
    explanation: str = ""
    conditional_alternative: str | None = None
    citations: list[str] = Field(default_factory=list, description="인용한 rule_id")
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3. 규칙 엔진 입출력 모델 (app/rules_engine/__init__.py의 시그니처가 쓴다)
# ---------------------------------------------------------------------------


class PolicyCheckResult(BaseModel):
    """check_all의 결과. MCP check_policy의 출력이기도 하다."""

    model_config = ConfigDict(extra="forbid")

    violations: list[Violation] = Field(default_factory=list)
    # 유효한 긴급 변경으로 면제되어 위반에서 빠진 조항. 설명에 "면제 적용"을 쓰려고 근거를 같이 둔다.
    exempted: list[Violation] = Field(default_factory=list)
    # 긴급 표시가 있을 때만 true/false, 아니면 None
    emergency_valid: bool | None = None
    standard_change_id: StandardId | None = None


class StandardCheck(BaseModel):
    """is_standard의 결과."""

    model_config = ConfigDict(extra="forbid")

    is_standard: bool
    standard_change_id: StandardId | None = None
    reason: str = ""


class EmergencyCheck(BaseModel):
    """emergency_valid의 결과 (CHG-07)."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    ticket: str | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# 4. 정답 라벨 모델
# ---------------------------------------------------------------------------
# 라벨은 평가 데이터라서 모르는 키가 있어도 로드가 깨지지 않게 extra를 무시한다.
# dev 라벨의 키가 하나도 버려지지 않는지는 테스트가 확인한다.


class LabelStructured(ChangeRequest):
    """expected.structured. ChangeRequest에 라벨 전용 필드 두 개를 더한다.

    acceptable_change_types와 time_note는 평가용 메타데이터라 LLM이 채울 출력 스키마
    (ChangeRequest)에는 넣지 않는다.
    """

    model_config = ConfigDict(extra="ignore")

    acceptable_change_types: list[ChangeType] = Field(default_factory=list)
    time_note: str | None = None


class ExpectedLabel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    structured: LabelStructured
    violations: list[str]
    verdict: Verdict
    emergency_valid: bool | None = None
    post_report_required: bool = False
    risk: Risk | None = None
    similar_history: list[str] | None = None
    conditional_alternative: str | None = None
    rationale: str = ""


class LabeledRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: str
    split: Literal["dev", "eval"]
    submitted_at: KstDatetime
    requester: str
    text: str
    expected: ExpectedLabel
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. 데이터 파일 모델과 GKData
# ---------------------------------------------------------------------------


class _DataModel(BaseModel):
    """데이터 파일 모델의 공통 설정. 파일의 키를 하나도 놓치지 않으려고 extra를 막는다."""

    model_config = ConfigDict(extra="forbid")


class Service(_DataModel):
    id: str
    name: str
    type: str
    tier: Literal["Tier-1", "Tier-2", "Tier-3"]
    owner_team: str
    zone: str
    host: str
    ports: list[int]
    depends_on: list[str]
    dependents: list[str]


class Partner(_DataModel):
    partner: str
    ip: str
    purpose: str


class Cmdb(_DataModel):
    updated_at: date
    internal_cidr: str
    services: list[Service]
    partners: list[Partner]

    def get_service(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)


class CalendarRange(_DataModel):
    start: KstDatetime = Field(validation_alias=AliasChoices("from", "start"))
    end: KstDatetime = Field(validation_alias=AliasChoices("to", "end"))


class FreezePeriod(_DataModel):
    freeze_id: str
    title: str
    start: KstDatetime
    end: KstDatetime
    scope: list[str] = Field(description='동결 대상 서비스 ID. "*"이면 전체')


class ScheduledChange(_DataModel):
    schedule_id: str
    target: str
    change_type: ChangeType
    title: str
    start: KstDatetime
    end: KstDatetime
    owner: str


class ChangeCalendar(_DataModel):
    period: CalendarRange = Field(validation_alias=AliasChoices("range", "period"))
    freeze_periods: list[FreezePeriod]
    scheduled_changes: list[ScheduledChange]


class Incident(_DataModel):
    ticket: str
    title: str
    status: Literal["open", "closed"]
    opened_at: KstDatetime
    closed_at: KstDatetime | None = None
    services: list[str]


class OpenIncidents(_DataModel):
    incidents: list[Incident]

    def get(self, ticket: str) -> Incident | None:
        return next((i for i in self.incidents if i.ticket == ticket), None)


class IncidentDetail(_DataModel):
    severity: str
    duration_minutes: int
    impact: str
    root_cause: str
    lesson: str


class HistoryItem(_DataModel):
    history_id: str
    date: date
    service: str
    change_type: ChangeType
    title: str
    description: str
    outcome: Literal["성공", "장애", "실패 후 롤백"]
    incident: IncidentDetail | None = None
    related_rules: list[str] = Field(default_factory=list)


class RulesDefaults(_DataModel):
    timezone: str
    internal_cidr: str
    work_duration_minutes_if_missing: int


class RuleSpec(_DataModel):
    rule_id: str
    title: str
    severity: Literal["forbidden", "fixable", "procedure"]
    applies_to: list[str]
    emergency_exempt: bool
    params: dict[str, Any] = Field(default_factory=dict)


class StandardChangeSpec(_DataModel):
    std_id: StandardId
    title: str
    condition: str


class RiskConfig(_DataModel):
    tier_to_risk: dict[str, Risk]
    bump_if: str
    standard_change: Risk | None = None


class RulesConfig(_DataModel):
    version: str
    source_of_truth: str
    defaults: RulesDefaults
    verdict_by_severity: dict[str, Verdict]
    rules: list[RuleSpec]
    standard_changes: list[StandardChangeSpec]
    required_fields: dict[str, list[str]]
    risk: RiskConfig

    def get_rule(self, rule_id: str) -> RuleSpec | None:
        return next((r for r in self.rules if r.rule_id == rule_id), None)


class GKData(BaseModel):
    """규칙 엔진이 읽는 데이터 묶음.

    규칙 엔진의 공개 함수는 마지막 선택 인자 data로 이것을 받는다. None이면
    data_loader.load_gk_data()의 캐시된 값을 쓴다. 테스트는 model_copy(update=...)로
    가짜 캘린더/CMDB/티켓을 넣어 실제 데이터로는 만들 수 없는 경계 케이스를 만든다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cmdb: Cmdb
    calendar: ChangeCalendar
    incidents: OpenIncidents
    history: list[HistoryItem]
    rules: RulesConfig
