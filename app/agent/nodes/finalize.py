"""⑦ finalize: 확정 (docs/SPEC.md 3장).

결정이 난 요청의 감사 로그 한 줄을 남긴다. 사람의 결정은 decision, 표준 변경의 자동 승인은 auto_approved 이벤트다.
긴급 유효 변경이면 기록의 post_report_required가 true라서 사후 보고 대상으로 표시된다(규정 CHG-07, 값은 엔진이 정한
Recommendation.post_report_required를 그대로 옮긴다).

감사 로그를 쓰지 못해도 사람의 결정을 되돌리지 않는다. 결정은 이미 체크포인터에 저장됐고, 승인자가 누른 결과를 로그
디스크 오류로 뒤집으면 더 혼란스럽다. 대신 서버 로그에 크게 남기고 errors에 기록한다.
"""

import logging

from app.agent.state import GKState
from app.audit import AuditLog, record_of

logger = logging.getLogger(__name__)


def make_finalize(audit: AuditLog):
    """부모 그래프에 붙일 노드. 감사 로그는 lifespan이 한 번 만들어 넘긴다."""

    async def finalize(state: GKState) -> dict:
        event = "auto_approved" if state["outcome"] == "auto_approved" else "decision"
        try:
            await audit.append(event, record_of(state))
        except OSError as exc:  # 디스크, 권한 문제. 결정은 되돌리지 않는다
            logger.error("감사 로그를 쓰지 못했다 (%s): request_id=%s", type(exc).__name__, state.get("request_id"))
            return {"errors": [f"finalize: 감사 로그 기록 실패 ({type(exc).__name__})"]}
        return {}

    return finalize
