"""⑥ approval_gate: 사람 승인 (docs/SPEC.md 3장).

자동 승인은 route가 standard이고 판정이 자동 승인일 때만이다 (리뷰 K3). 작은 모델과 구조화 결과의 분류가 어긋나
route가 general이 된 표준 변경은 판정이 자동 승인이어도 승인자에게 간다. 판정은 엔진이 확정한 값이지만, 그 값이
틀린 입력(분류 오류, 잘못 읽은 시각)에서 나왔을 가능성을 사람이 한 번 더 보게 하려는 것이다. 승인자를 거치지 않는
방향의 오류가 가장 위험하다(PROGRESS의 REQ-D10 qwen3:4b 사례).

그 밖의 모든 요청은 interrupt()로 멈춘다. 멈춘 상태는 체크포인터에 저장되므로 서버를 다시 띄워도 같은 thread_id로
재개할 수 있다. 재개하면 이 노드가 처음부터 다시 실행되므로 interrupt() 앞에는 부작용을 두지 않는다.

재개 값은 {decision, approver, comment, at}이다. approver와 at은 클라이언트가 정하지 못하고 API가 X-User-Name
헤더와 서버 시각으로 채운다(PROGRESS 0단계 결정).
"""

from langgraph.types import interrupt

from app.agent.results import ApprovalRecord, Outcome
from app.agent.state import GKState
from app.clock import now_iso

OUTCOME_OF_DECISION: dict[str, Outcome] = {"approve": "approved", "reject": "rejected"}


def auto_approves(state: GKState) -> bool:
    """표준 변경으로 분류됐고 엔진 판정이 자동 승인이면 사람을 거치지 않는다."""
    recommendation = state.get("recommendation")
    return state.get("route") == "standard" and recommendation is not None and recommendation.verdict == "자동 승인"


async def approval_gate(state: GKState) -> dict:
    recommendation = state["recommendation"]
    if auto_approves(state):
        return {"approval": None, "outcome": "auto_approved", "decided_at": now_iso()}

    answer = interrupt(
        {"request_id": state.get("request_id"), "verdict": recommendation.verdict, "risk": recommendation.risk}
    )
    approval = ApprovalRecord.model_validate(answer)
    return {"approval": approval, "outcome": OUTCOME_OF_DECISION[approval.decision], "decided_at": approval.at}
