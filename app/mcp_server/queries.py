"""get_service_info, get_calendar, get_incident의 조회 로직.

MCP와 무관한 순수 함수다. 서버(server.py)는 이 함수를 감싸기만 하고, 단위 테스트는 여기에 건다.
데이터는 GKData로 주입받는다(테스트의 가짜 캘린더/CMDB/티켓용).

이 조회들은 판정이 아니다. 위반 여부(CHG-06, CHG-10)는 규칙 엔진이 계산하고, get_calendar는
"어떤 동결 기간과 예정 변경이 겹치는지"를 에이전트가 설명에 쓸 수 있게 보여 줄 뿐이다.
"""

from datetime import datetime

from app.mcp_server.models import CalendarResult, ServiceInfo
from app.schemas import GKData, Incident, Service


class NotFound(LookupError):
    """조회 대상이 없다. 서버는 이를 NOT_FOUND 오류로 응답한다."""


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """반열린 구간 [a_start, a_end)와 [b_start, b_end)가 겹치는지.

    작업 구간은 반열린 구간이다(SPEC 3장 해석 결정). 끝 시각에 딱 맞춰 시작하면 겹치지 않는다.
    """
    return a_start < b_end and b_start < a_end


def require_service(data: GKData, service_id: str) -> Service:
    """CMDB에서 서비스를 찾는다. 없으면 NotFound.

    규칙 엔진 Tool도 엔진을 부르기 전에 이 확인을 먼저 한다. 엔진은 CMDB에 없는 대상에 ValueError를
    내는데, 그 오류를 메시지 문자열로 가려내지 않고 대상 존재를 미리 확인해서 NOT_FOUND로 구분하려는 것.
    """
    service = data.cmdb.get_service(service_id)
    if service is None:
        known = ", ".join(s.id for s in data.cmdb.services)
        raise NotFound(f"서비스를 찾을 수 없다: {service_id}. 등록된 서비스: {known}")
    return service


def service_info(data: GKData, service_id: str) -> ServiceInfo:
    service = require_service(data, service_id)
    return ServiceInfo(
        id=service.id,
        name=service.name,
        type=service.type,
        tier=service.tier,
        owner_team=service.owner_team,
        zone=service.zone,
        ports=service.ports,
        default_port=service.ports[0] if service.ports else None,
        depends_on=service.depends_on,
        dependents=service.dependents,
    )


def calendar_overlap(data: GKData, target: str, start: datetime, end: datetime) -> CalendarResult:
    if end <= start:
        raise ValueError("end는 start보다 늦어야 한다")
    # 없는 대상은 "겹치는 것 없음"이 아니라 오류로 응답한다. 오타가 충돌 없음으로 보이면 안전하지 않다.
    require_service(data, target)
    freezes = [
        f
        for f in data.calendar.freeze_periods
        if ("*" in f.scope or target in f.scope) and overlaps(start, end, f.start, f.end)
    ]
    scheduled = [
        s
        for s in data.calendar.scheduled_changes
        if s.target == target and overlaps(start, end, s.start, s.end)
    ]
    return CalendarResult(
        target=target,
        start=start,
        end=end,
        freeze_periods=sorted(freezes, key=lambda f: f.start),
        scheduled_changes=sorted(scheduled, key=lambda s: s.start),
    )


def incident_info(data: GKData, ticket: str) -> Incident:
    incident = data.incidents.get(ticket)
    if incident is None:
        raise NotFound(f"장애 티켓을 찾을 수 없다: {ticket}")
    return incident
