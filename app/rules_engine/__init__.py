"""규칙 엔진 공개 함수.

판정의 핵심인 결정론 로직이다. LLM은 쓰지 않는다. 시그니처는 1단계에서 정했고 2단계에서 본문을
채웠다. 3단계(MCP 서버)는 이 시그니처만 보고 감싼다. 입출력 타입은 app/schemas.py의 Pydantic 모델이며,
시그니처를 바꾸려면 구현 전에 사용자에게 먼저 묻는다.

이 파일은 데이터 기본값(data=None → load_gk_data())만 채우고 각 모듈에 위임한다.
    context.py   작업 구간, 출발지, 포트 정규화
    chgNN.py     조항별 검사 (CHG-01~06, 08~10)
    registry.py  조항 실행 순서와 applies_to 거르기
    policy.py    check_all
    standard.py  is_standard (STD-01/02)
    emergency.py emergency_valid (CHG-07)
    verdict.py   verdict_from
    risk.py      risk_from
    window.py    check_window, next_valid_window

오류
    CMDB에 없는 대상, 종료가 시작보다 이르거나 같은 작업 구간은 ValueError다. 판정을 만들지 않고
    상위 노드가 "확인 불가"로 처리하게 하려는 것.

데이터 주입
    데이터가 필요한 함수는 마지막 선택 인자 data(GKData)를 받는다. None이면
    data_loader.load_gk_data()의 캐시된 값을 쓴다. verdict_from만 데이터가 필요 없다.
    규정 값(시간대, 포트, 기한, 내부망 대역)은 data.rules(rules.json)와 data.cmdb에서 읽고
    코드에 박지 않는다.

해석 결정 (docs/SPEC.md 3장, schemas.ChangeRequest docstring)
    작업 구간은 반열린 구간 [start, end). 종료 시각이 없으면 시작 후 1시간, 출발지가 없으면
    0.0.0.0/0, 포트가 없으면 CMDB ports의 첫 번째 값. 표준 변경은 코드가 확정한다.
"""

from datetime import datetime, timedelta

from app.data_loader import load_gk_data
from app.schemas import (
    ChangeRequest,
    ChangeType,
    EmergencyCheck,
    GKData,
    PolicyCheckResult,
    Risk,
    StandardCheck,
    Verdict,
    Violation,
)

from . import emergency as _emergency
from . import policy as _policy
from . import risk as _risk
from . import standard as _standard
from . import verdict as _verdict
from . import window as _window

__all__ = [
    "check_all",
    "is_standard",
    "emergency_valid",
    "verdict_from",
    "risk_from",
    "check_window",
    "next_valid_window",
]


def _data(data: GKData | None) -> GKData:
    return data if data is not None else load_gk_data()


def check_all(
    request: ChangeRequest, submitted_at: datetime, data: GKData | None = None
) -> PolicyCheckResult:
    """CHG-01~10 조항을 모두 검사한다 (change_policy.md 2장).

    유효한 긴급 변경(CHG-07)이면 CHG-01, 05, 10을 면제하고 exempted에 근거와 함께 남긴다.
    표준 변경에는 CHG-06, 10만 적용한다. standard_change_id는 is_standard가 확정한 값이다.
    """
    return _policy.check_all(request, submitted_at, _data(data))


def is_standard(request: ChangeRequest, data: GKData | None = None) -> StandardCheck:
    """표준 변경(STD-01/02) 해당 여부를 코드로 확정한다 (change_policy.md 3장).

    LLM이 붙인 standard_change_id는 믿지 않는다.
    STD-01: restart_required == False 이고 extra_changes가 비어 있음.
    STD-02: cert_expires_at이 작업일 기준 30일 이내.
    값이 null이면 표준 변경이 아니다 (fail-safe).
    """
    return _standard.is_standard(request, _data(data))


def emergency_valid(request: ChangeRequest, data: GKData | None = None) -> EmergencyCheck:
    """긴급 변경이 유효한지 확인한다 (CHG-07).

    티켓이 open_incidents에서 status == "open"이고, 요청 대상이 그 장애의 services에 있어야 유효하다.
    티켓이 없거나 종료됐거나 대상이 다르면 유효하지 않고, 이것 자체는 위반이 아니다.
    """
    return _emergency.emergency_valid(request, _data(data))


def verdict_from(violations: list[Violation], is_standard: bool) -> Verdict:
    """위반 목록에서 판정을 낸다 (change_policy.md 1장).

    금지 위반이 하나라도 있으면 반려, 보완 위반만 있으면 조건부 승인,
    위반이 없으면 일반 변경은 승인, 표준 변경은 자동 승인. 위험도는 판정을 바꾸지 않는다.
    """
    return _verdict.verdict_from(violations, is_standard)


def risk_from(
    target: str,
    change_type: ChangeType,
    has_similar_incident: bool,
    data: GKData | None = None,
) -> Risk | None:
    """위험도를 계산한다 (change_policy.md 5장).

    기본값은 CMDB 등급(Tier-1 높음, Tier-2 중간, Tier-3 낮음). 같은 서비스와 같은 변경 유형으로
    난 장애 이력이 있으면(has_similar_incident) 한 단계 올리고 최대는 높음이다.
    표준 변경은 위험도를 매기지 않으므로 None.
    """
    return _risk.risk_from(target, change_type, has_similar_incident, _data(data))


def check_window(
    target: str,
    change_type: ChangeType,
    start: datetime,
    end: datetime,
    submitted_at: datetime,
    data: GKData | None = None,
) -> list[Violation]:
    """주어진 시간대에서 걸리는 위반 목록을 돌려준다. 대안 일정 검증용이다.

    요청 본문에 의존하지 않는 시간 관련 조항(CHG-01, 05, 06, 10)을 본다.
    표준 변경이면 CHG-06, 10만 본다.
    """
    return _window.check_window(target, change_type, start, end, submitted_at, _data(data))


def next_valid_window(
    target: str,
    change_type: ChangeType,
    duration: timedelta,
    after: datetime,
    submitted_at: datetime,
    data: GKData | None = None,
) -> tuple[datetime, datetime] | None:
    """대안 일정을 찾는다. decide 노드가 대안이 계속 위반일 때 이 구간으로 대체한다.

    after 이후 30분 단위로 시작 시각을 옮기며, check_window가 위반 없음을 돌려주는
    가장 빠른 [start, start + duration) 구간을 반환한다. 14일 안에 없으면 None.
    표준 변경이면 CHG-06, CHG-10만 본다.

    첫 후보는 after를 다음 30분 경계로 올림한 시각이다. after가 이미 30분 경계(분이 0 또는 30이고
    초가 0)면 after 자체가 첫 후보다. 대안 일정을 14:00, 14:30처럼 읽기 좋은 시각으로 제시하려는 것.
    """
    return _window.next_valid_window(target, change_type, duration, after, submitted_at, _data(data))
