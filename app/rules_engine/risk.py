"""위험도 (change_policy.md 5장).

기본값은 CMDB 등급(rules.json risk.tier_to_risk). 같은 서비스 + 같은 변경 유형으로 난 장애 이력이
있으면 한 단계 올리고 최대는 높음이다. 표준 변경은 위험도를 매기지 않는다(risk.standard_change = null).
"""

from typing import get_args

from app.schemas import ChangeType, GKData, Risk

from .context import get_service

# schemas.Risk의 선언 순서(높음, 중간, 낮음)를 뒤집어 낮은 쪽부터 올라가는 사다리로 쓴다.
LADDER: tuple[Risk, ...] = tuple(reversed(get_args(Risk)))


def risk_from(target: str, change_type: ChangeType, has_similar_incident: bool, data: GKData) -> Risk | None:
    config = data.rules.risk
    if change_type == "standard":
        return config.standard_change
    base = config.tier_to_risk[get_service(data, target).tier]
    if not has_similar_incident:
        return base
    return LADDER[min(LADDER.index(base) + 1, len(LADDER) - 1)]
