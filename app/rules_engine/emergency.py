"""CHG-07 긴급 변경 절차.

긴급 표시가 있고, 티켓(INC-연도-번호)이 open_incidents에 열린 상태로 있고, 그 장애의 대상 서비스에
요청 대상이 포함되면 유효한 긴급 변경이다. 유효하면 CHG-01, 05, 10을 면제받는다(면제는 check_all).
유효하지 않은 것 자체는 위반이 아니고 일반 변경으로 처리한다. 조건은 rules.json CHG-07 params에서 읽는다.
"""

import re

from app.schemas import ChangeRequest, EmergencyCheck, GKData

from .context import rule_spec

RULE_ID = "CHG-07"


def emergency_valid(request: ChangeRequest, data: GKData) -> EmergencyCheck:
    params = rule_spec(data, RULE_ID).params
    ticket = request.ticket.strip().upper() if request.ticket else None

    def invalid(reason: str) -> EmergencyCheck:
        return EmergencyCheck(valid=False, ticket=ticket, reason=f"{reason} 일반 변경으로 처리한다.")

    if not request.is_emergency:
        return invalid("긴급 표시가 없다.")
    if not ticket:
        return invalid("긴급 표시가 있지만 장애 티켓 번호가 없다.")
    if not re.fullmatch(params["ticket_pattern"], ticket):
        return invalid(f"장애 티켓 번호({ticket})가 INC-연도-번호 형식이 아니다.")
    incident = data.incidents.get(ticket)
    if incident is None:
        return invalid(f"장애 티켓({ticket})이 장애 목록에 없다.")
    if params["ticket_must_be_open"] and incident.status != "open":
        return invalid(f"장애 티켓({ticket}, {incident.title})이 이미 종료(closed)됐다.")
    if params["target_must_be_in_incident_services"] and request.target not in incident.services:
        scope = ", ".join(incident.services)
        return invalid(f"요청 대상({request.target})이 장애 티켓({ticket})의 대상 서비스({scope})에 없다.")
    return EmergencyCheck(
        valid=True,
        ticket=ticket,
        reason=f"열린 장애 티켓({ticket}, {incident.title})의 대상 서비스에 요청 대상({request.target})이 포함된다.",
    )
