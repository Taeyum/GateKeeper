"""조항 id → 검사 함수, 그리고 조항을 돌리는 순서와 적용 대상 거르기.

- 시간 조항(WINDOW_RULES)은 대상, 유형, 작업 구간, 요청 시각만 본다. check_window와 next_valid_window는
  이것만 돌린다.
- 본문 조항(BODY_RULES)은 요청 본문(출발지, 포트, 기한, 롤백, 테스트 증적)이 필요해 check_all에서만 돈다.

적용 대상은 rules.json applies_to로 거른다. "표준 변경에는 CHG-06, 10만 적용"도 코드 분기가 아니라
applies_to에 standard가 있는 조항이 그 둘뿐이라서 정해진다. 순서는 rules.json 조항 순서를 따른다.
"""

from collections.abc import Callable

from app.schemas import Violation

from . import chg01, chg02, chg03, chg04, chg05, chg06, chg08, chg09, chg10
from .context import RequestContext, WindowContext, rule_spec

WINDOW_RULES: dict[str, Callable[[WindowContext], Violation | None]] = {
    chg01.RULE_ID: chg01.check,
    chg05.RULE_ID: chg05.check,
    chg06.RULE_ID: chg06.check,
    chg10.RULE_ID: chg10.check,
}

BODY_RULES: dict[str, Callable[[RequestContext], Violation | None]] = {
    chg02.RULE_ID: chg02.check,
    chg03.RULE_ID: chg03.check,
    chg04.RULE_ID: chg04.check,
    chg08.RULE_ID: chg08.check,
    chg09.RULE_ID: chg09.check,
}

# RequestContext는 WindowContext를 상속하므로 check_all은 두 묶음을 한 번에 돌린다.
ALL_RULES: dict[str, Callable[[RequestContext], Violation | None]] = {**WINDOW_RULES, **BODY_RULES}


def applicable(rule_id: str, ctx: WindowContext) -> bool:
    return ctx.change_type in rule_spec(ctx.data, rule_id).applies_to


def out_of_scope_rules(ctx: RequestContext) -> set[str]:
    """CHG-02 "범위 밖"에 해당하면 건너뛸 조항 id.

    change_policy.md CHG-02 "범위 밖: 외부 공개 웹(web-01, web-02)의 443 포트는 로드밸런서에서 관리하며
    이 규정의 방화벽 변경 대상이 아니다." 대상과 포트, 건너뛸 조항(CHG-02, 03, 09)은 rules.json
    CHG-02 params.out_of_scope에서 읽는다. 시간 조항은 그대로 적용한다.
    """
    scope = rule_spec(ctx.data, chg02.RULE_ID).params.get("out_of_scope")
    if not scope or ctx.change_type != "firewall":
        return set()
    if ctx.target in scope["targets"] and ctx.port == scope["port"]:
        return set(scope["skip_rules"])
    return set()


def run_window_rules(ctx: WindowContext) -> list[Violation]:
    found = []
    for spec in ctx.data.rules.rules:
        check = WINDOW_RULES.get(spec.rule_id)
        if check is not None and applicable(spec.rule_id, ctx) and (v := check(ctx)) is not None:
            found.append(v)
    return found
