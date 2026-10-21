"""판정 (change_policy.md 1장).

금지 위반이 하나라도 있으면 반려, 보완 위반만 있으면 조건부 승인, 위반이 없으면 일반 변경은 승인,
표준 변경은 자동 승인이다. 위험도는 판정을 바꾸지 않는다.

공개 시그니처에 data가 없어서 rules.json을 읽지 않고 Violation.severity만 본다. 이 대응이
rules.json verdict_by_severity와 같은지는 테스트가 고정한다.
"""

from app.schemas import Verdict, Violation


def verdict_from(violations: list[Violation], is_standard: bool) -> Verdict:
    severities = {v.severity for v in violations}
    if "forbidden" in severities:
        return "반려"
    if "fixable" in severities:
        return "조건부 승인"
    return "자동 승인" if is_standard else "승인"
