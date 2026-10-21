"""alternative_diff: 조건부 대안을 항목별 "지금 -> 이렇게"로 푼 것 (docs/API_CONTRACT.md 3장, 리뷰 K4).

백엔드가 코드로 계산한다. LLM이 쓴 대안 문장은 읽지 않는다. 어떤 필드를 고쳐야 하는지는 위반한 조항이 정하고, 값은 결정론
부분에서 가져온다: 작업 시간은 decide가 규칙 엔진으로 검증한 대안 일정(state.alternative), 허용 기한은 rules.json의 최대 일수,
나머지는 해야 할 일의 뜻이다. 조항을 필드와 라벨에 잇는 표는 화면에 보여 줄 말이고 규정 값이 아니다.
화면은 불리언을 "있음/없음", expires_days 숫자를 "N일", 문자열은 그대로 보여 준다(web/src/lib/review.ts).
"""

from collections.abc import Iterable
from typing import Any

from app.agent.nodes.decide import ALTERNATIVE_VERDICTS, EXPIRY_RULE_ID
from app.agent.results import AlternativePlan
from app.rules_engine.chg02 import RULE_ID as TOO_WIDE_RULE_ID
from app.rules_engine.chg03 import RULE_ID as DB_PORT_RULE_ID
from app.rules_engine.chg04 import RULE_ID as ROLLBACK_RULE_ID
from app.rules_engine.chg08 import RULE_ID as TEST_RULE_ID
from app.rules_engine.registry import WINDOW_RULES
from app.schemas import KST, ChangeRequest, RulesConfig

# 위반한 조항 -> 고쳐야 할 ChangeRequest 필드. 화면의 묶음 순서(작업 시간, 접속할 곳, 기간, 롤백, 테스트)를 따른다.
FIELD_ORDER = ("start", "source", "expires_days", "has_rollback", "has_test_evidence")
LABELS = {
    "start": "작업 시간",
    "source": "접속할 곳",
    "expires_days": "열어 둘 기간",
    "has_rollback": "되돌리는 방법",
    "has_test_evidence": "테스트 결과",
}
SOURCE_SUGGESTION = "등록된 파트너 IP /32"


def field_of(rule_id: str) -> str | None:
    if rule_id in WINDOW_RULES:  # CHG-01, 05, 06, 10: 작업 시각을 옮겨야 풀린다
        return "start"
    if rule_id in (TOO_WIDE_RULE_ID, DB_PORT_RULE_ID):
        return "source"
    return {EXPIRY_RULE_ID: "expires_days", ROLLBACK_RULE_ID: "has_rollback", TEST_RULE_ID: "has_test_evidence"}.get(rule_id)


def fields_to_fix(rule_ids: Iterable[str]) -> list[str]:
    wanted = {field_of(rule_id) for rule_id in rule_ids}
    return [name for name in FIELD_ORDER if name in wanted]


def _iso(value) -> str | None:
    return value.astimezone(KST).isoformat() if value is not None else None


def alternative_diff(
    *,
    verdict: str,
    rule_ids: Iterable[str],
    structured: ChangeRequest | None,
    plan: AlternativePlan | None,
    rules: RulesConfig,
) -> list[dict[str, Any]] | None:
    """반려나 조건부 승인일 때만 만든다. 항목이 하나도 없으면 None이고, 화면은 대안 문장만 보여 준다."""
    if verdict not in ALTERNATIVE_VERDICTS or structured is None:
        return None
    items: list[dict[str, Any]] = []
    for name in fields_to_fix(rule_ids):
        if name == "start":
            if plan is None or plan.start is None:
                continue  # 옮길 시각을 못 찾았으면 항목을 만들지 않는다
            current, suggested = _iso(structured.start), _iso(plan.start)
        elif name == "source":
            current, suggested = structured.source, SOURCE_SUGGESTION
        elif name == "expires_days":
            current, suggested = structured.expires_days, rules.get_rule(EXPIRY_RULE_ID).params["max_days"]  # type: ignore[union-attr]
        else:  # has_rollback, has_test_evidence: 적어 내라는 뜻이다
            current, suggested = getattr(structured, name), True
        items.append({"field": name, "label": LABELS[name], "current": current, "suggested": suggested})
    return items or None
