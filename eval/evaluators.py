"""평가 지표 (docs/EVAL.md 3장). 순수 함수이고 입출력은 Pydantic 모델이다.

평가기는 측정 대상(SUT)과 독립이어야 한다. 그래서 규칙 엔진의 정규화 함수를 쓰지 않고 여기서 따로
정규화한다. 규정에 있는 조항 ID 목록만 규정 문서 파서에서 가져온다 (환각 판정의 기준).

한 번의 실행(요청 x rep)을 score_case로 채점하고, aggregate로 모아서 지표를 낸다.
오류 난 실행은 판정을 "ERROR", 위반을 빈 집합, 구조화 필드를 모두 오답으로 센다.
"""

import ipaddress
import math
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from app.data_loader import load_policy_text, load_rules
from app.mcp_server.policy_docs import split_policy
from app.schemas import KST, ChangeRequest, LabeledRequest, LabelStructured, Recommendation, RulesConfig

ERROR_VERDICT = "ERROR"

# EVAL.md 3장 구조화 정확도의 필드. 순서는 표와 같다.
STRUCTURED_FIELDS = (
    "target", "change_type", "start", "end", "time_precision", "source", "port", "expires_days",
    "expires_at", "has_rollback", "has_test_evidence", "is_emergency", "ticket",
    "restart_required", "cert_expires_at",
)  # fmt: skip

# gold가 null이면 비교에서 뺀다 (본문에 언급이 없으면 gold도 null이라 "맞혔다"고 세면 부풀려진다).
_EXCLUDE_WHEN_GOLD_NULL = ("restart_required", "cert_expires_at")

_ALTERNATIVE_VERDICTS = ("반려", "조건부 승인")


# ---------------------------------------------------------------------------
# 입출력 모델
# ---------------------------------------------------------------------------


class Prediction(BaseModel):
    """한 번의 실행 결과. 오류면 error만 채우고 나머지는 None이다."""

    structured: ChangeRequest | None = None
    recommendation: Recommendation | None = None
    # 예외 타입 이름만 둔다. 메시지에는 요청 본문이 섞일 수 있다 (절대 규칙 1).
    error: str | None = None
    # v2에서 이력 검색이 돌려준 상위 history_id. v0, v1은 None.
    retrieved_history: list[str] | None = None


class CaseScore(BaseModel):
    """실행 1회의 채점 결과."""

    request_id: str
    rep: int = 0
    error: str | None = None
    verdict_gold: str
    verdict_pred: str
    verdict_correct: bool
    violations_gold: list[str]
    violations_pred: list[str]
    tp: int
    fp: int
    fn: int
    # 필드별 일치 여부. None이면 비교에서 제외한 필드.
    structured_fields: dict[str, bool | None]
    cited: list[str]
    hallucinated: list[str]
    miscited: list[str]
    miscited_strict: list[str]
    risk_correct: bool | None = None
    emergency_correct: bool | None = None
    rag_recall: float | None = None
    # 판정이 자기 위반 목록에서 규정 1장대로 계산한 판정과 다른지. 오류 난 실행은 None.
    self_contradiction: bool | None = None
    needs_alternative: bool
    # Judge가 채운다 (eval/judge.py). 대안이 필요 없거나 Judge를 끄면 None.
    judge_score: int | None = None


class FieldAccuracy(BaseModel):
    correct: int
    total: int
    accuracy: float | None


class MetricsSummary(BaseModel):
    n_requests: int
    reps: int
    n_runs: int
    n_errors: int
    verdict_accuracy: float | None
    violation_tp: int
    violation_fp: int
    violation_fn: int
    violation_precision: float | None
    violation_recall: float | None
    violation_f1: float | None
    structured_accuracy: float | None
    structured_by_field: dict[str, FieldAccuracy]
    hallucinated_count: int
    miscited_count: int
    miscited_strict_count: int
    risk_accuracy: float | None
    risk_n: int
    emergency_accuracy: float | None
    emergency_n: int
    self_contradiction_rate: float | None
    self_contradiction_count: int
    self_contradiction_n: int
    rag_recall_at_3: float | None
    consistency: float | None
    alternative_score: float | None
    alternative_n: int


# ---------------------------------------------------------------------------
# 정규화 (평가기 전용. 규칙 엔진의 것을 쓰지 않는다)
# ---------------------------------------------------------------------------


def _minute(value: datetime | None) -> datetime | None:
    """KST로 바꾸고 초 이하를 버린다."""
    if value is None:
        return None
    return value.astimezone(KST).replace(second=0, microsecond=0)


def _start_day(value: datetime | None) -> date | None:
    return None if value is None else value.astimezone(KST).date()


def _end_day(value: datetime | None) -> date | None:
    """범위의 끝은 반열린 구간이라 마지막 순간(끝 - 1마이크로초)이 속한 날짜로 본다. 월 00:00과 일 23:59가 같은 날이 된다."""
    return None if value is None else (value.astimezone(KST) - timedelta(microseconds=1)).date()


def normalize_source(value: str | None) -> tuple[str, ...] | None:
    """출발지를 비교용으로 정규화한다. 단일 IP는 /32, 쉼표로 여러 개면 정렬한 집합. 파싱이 안 되면 strip한 문자열."""
    if value is None:
        return None
    parts = [part.strip() for part in str(value).split(",") if part.strip()]
    if not parts:
        return None
    normalized: set[str] = set()
    for part in parts:
        try:
            normalized.add(str(ipaddress.ip_network(part, strict=False)))
        except ValueError:
            normalized.add(part)
    return tuple(sorted(normalized))


def _ticket(value: str | None) -> str | None:
    return None if value is None else value.strip().upper()


def normalize_rule_id(value: str) -> str:
    # strip과 대문자만 맞춘다. 더 정규화하면 지어낸 ID가 가려진다.
    return value.strip().upper()


# ---------------------------------------------------------------------------
# 구조화 정확도
# ---------------------------------------------------------------------------


def compare_field(name: str, gold: LabelStructured, pred: ChangeRequest | None) -> bool | None:
    """필드 하나의 일치 여부. 비교에서 제외하는 필드는 None."""
    if name in _EXCLUDE_WHEN_GOLD_NULL and getattr(gold, name) is None:
        return None
    if pred is None:
        return False
    if name == "change_type":
        return pred.change_type in (gold.acceptable_change_types or [gold.change_type])
    if name == "start":
        if gold.time_precision == "range":
            return _start_day(gold.start) == _start_day(pred.start)
        return _minute(gold.start) == _minute(pred.start)
    if name == "end":
        if gold.time_precision == "range":
            return _end_day(gold.end) == _end_day(pred.end)
        return _minute(gold.end) == _minute(pred.end)
    if name == "expires_at":
        return _minute(gold.expires_at) == _minute(pred.expires_at)
    if name == "source":
        return normalize_source(gold.source) == normalize_source(pred.source)
    if name == "ticket":
        return _ticket(gold.ticket) == _ticket(pred.ticket)
    if name == "target":
        return gold.target.strip() == pred.target.strip()
    # port, expires_days, has_rollback, has_test_evidence, is_emergency, restart_required,
    # cert_expires_at, time_precision: null도 하나의 값이라 null과 false는 서로 다르다.
    return getattr(gold, name) == getattr(pred, name)


# ---------------------------------------------------------------------------
# 규정 조항 ID
# ---------------------------------------------------------------------------


def policy_rule_ids() -> frozenset[str]:
    """규정에 있는 조항 ID. 인용한 ID가 여기에 없으면 환각이다."""
    clauses = set(split_policy(load_policy_text()).clauses)
    standards = {spec.std_id for spec in load_rules().standard_changes}
    return frozenset(clauses | standards)


def allowed_extra_citations(gold: LabeledRequest) -> set[str]:
    """위반이 아니어도 판정의 근거로 인용할 수 있는 조항 (오인용으로 세지 않는다).

    gold가 긴급 변경이면 CHG-07, 표준 변경이면 gold의 STD ID. 면제된 조항(CHG-01/05/10)은 허용하지 않는다.
    citations의 의미는 "위반으로 판단한 조항 + 판정의 근거 조항"으로 v0 프롬프트에도 같이 적었다.
    """
    allowed: set[str] = set()
    if gold.expected.structured.is_emergency:
        allowed.add("CHG-07")
    if gold.expected.structured.standard_change_id:
        allowed.add(gold.expected.structured.standard_change_id)
    return allowed


# ---------------------------------------------------------------------------
# 판정과 위반 목록의 자기모순
# ---------------------------------------------------------------------------


def verdict_from_own_violations(verdict_pred: str, violations_pred: list[str], rules: RulesConfig) -> str:
    """pred 위반 목록만으로 규정 1장 판정을 다시 계산한다. 규칙 엔진은 쓰지 않고 rules.json만 읽는다.

    구분은 rules.json의 severity다(pred가 스스로 단 severity는 보지 않는다). 규정에 구분이 없는 ID(STD, 지어낸 ID)와
    procedure(CHG-07)는 계산에서 뺀다. 위반이 없으면 표준 변경 여부를 pred 판정이 "자동 승인"인지로 본다.
    """
    severities = {spec.rule_id: spec.severity for spec in rules.rules}
    found = {severities[rule_id] for rule_id in violations_pred if rule_id in severities}
    by_severity = rules.verdict_by_severity
    if "forbidden" in found:
        return by_severity["forbidden"]
    if "fixable" in found:
        return by_severity["fixable"]
    return by_severity["none_standard"] if verdict_pred == by_severity["none_standard"] else by_severity["none_general"]


def self_contradiction(verdict_pred: str, violations_pred: list[str], rules: RulesConfig) -> bool | None:
    """pred 판정이 자기 위반 목록으로 다시 계산한 판정과 다르면 True. 오류 난 실행은 정의할 수 없어 None.

    v0은 금지 조항을 위반으로 적고도 "조건부 승인"을 내는 식으로 모순된다. v1, v2는 판정을 코드가 위반 목록에서
    계산하므로 구조적으로 False다.
    """
    if verdict_pred == ERROR_VERDICT:
        return None
    return verdict_pred != verdict_from_own_violations(verdict_pred, violations_pred, rules)


def summarize_self_contradiction(flags: list[bool | None]) -> tuple[int, int, float | None]:
    """(모순 실행 수, 따질 수 있는 실행 수, 비율). None은 분모에서 뺀다."""
    defined = [flag for flag in flags if flag is not None]
    return sum(defined), len(defined), _ratio(sum(defined), len(defined))


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------


def score_case(
    gold: LabeledRequest,
    pred: Prediction,
    *,
    rep: int = 0,
    rag_enabled: bool = False,
    valid_rule_ids: frozenset[str] | None = None,
    rules: RulesConfig | None = None,
) -> CaseScore:
    """실행 1회를 채점한다. rag_enabled는 v2일 때만 True (RAG 재현율 대상)."""
    valid = valid_rule_ids if valid_rule_ids is not None else policy_rule_ids()
    rec = None if pred.error else pred.recommendation
    failed = rec is None
    expected = gold.expected

    verdict_pred = ERROR_VERDICT if failed else rec.verdict
    violations_gold = sorted({normalize_rule_id(r) for r in expected.violations})
    violations_pred = [] if failed else sorted({normalize_rule_id(v.rule_id) for v in rec.violations})
    gold_set, pred_set = set(violations_gold), set(violations_pred)

    structured = None if failed else pred.structured
    fields = {name: compare_field(name, expected.structured, structured) for name in STRUCTURED_FIELDS}

    cited = [] if failed else sorted({normalize_rule_id(c) for c in rec.citations} | pred_set)
    hallucinated = [c for c in cited if c not in valid]
    miscited_strict = [c for c in cited if c in valid and c not in gold_set]
    allowed = allowed_extra_citations(gold)
    miscited = [c for c in miscited_strict if c not in allowed]

    risk_correct = None if expected.risk is None else (not failed and rec.risk == expected.risk)
    emergency_correct = (
        (not failed and rec.emergency_valid == expected.emergency_valid)
        if expected.structured.is_emergency
        else None
    )

    rag_recall = None
    if rag_enabled and expected.similar_history:
        retrieved = set((pred.retrieved_history or [])[:3]) if not failed else set()
        rag_recall = len(retrieved & set(expected.similar_history)) / len(set(expected.similar_history))

    return CaseScore(
        request_id=gold.request_id,
        rep=rep,
        error=pred.error or ("MissingRecommendation" if failed else None),
        verdict_gold=expected.verdict,
        verdict_pred=verdict_pred,
        verdict_correct=verdict_pred == expected.verdict,
        violations_gold=violations_gold,
        violations_pred=violations_pred,
        tp=len(gold_set & pred_set),
        fp=len(pred_set - gold_set),
        fn=len(gold_set - pred_set),
        structured_fields=fields,
        cited=cited,
        hallucinated=hallucinated,
        miscited=miscited,
        miscited_strict=miscited_strict,
        risk_correct=risk_correct,
        emergency_correct=emergency_correct,
        rag_recall=rag_recall,
        self_contradiction=self_contradiction(verdict_pred, violations_pred, rules or load_rules()),
        needs_alternative=expected.verdict in _ALTERNATIVE_VERDICTS,
    )


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _mean(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def percentile_nearest_rank(values: list[float], q: float) -> float | None:
    """nearest-rank 백분위수. n=10이고 q=0.95면 순위가 10이라 최댓값과 같다."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def _consistency(cases: list[CaseScore], reps: int) -> float | None:
    """같은 요청의 모든 rep가 오류 없이 같은 판정을 낸 요청의 비율. reps가 1이면 정의할 수 없다."""
    if reps < 2:
        return None
    by_request: dict[str, list[CaseScore]] = {}
    for case in cases:
        by_request.setdefault(case.request_id, []).append(case)
    stable = sum(
        1
        for runs in by_request.values()
        if all(run.error is None for run in runs) and len({run.verdict_pred for run in runs}) == 1
    )
    return stable / len(by_request)


def aggregate(cases: list[CaseScore], reps: int) -> MetricsSummary:
    """실행(요청 x rep) 점수를 모아 지표를 낸다. 위반 P/R/F1은 (요청, rep, rule_id) 쌍의 micro 집계다."""
    n_runs = len(cases)
    tp = sum(c.tp for c in cases)
    fp = sum(c.fp for c in cases)
    fn = sum(c.fn for c in cases)

    # 분모가 0이면 0.0이고, gold와 pred가 모두 비어 측정할 것이 없을 때만 None이다.
    nothing = tp + fp + fn == 0
    precision = None if nothing else (tp / (tp + fp) if tp + fp else 0.0)
    recall = None if nothing else (tp / (tp + fn) if tp + fn else 0.0)
    if precision is None or recall is None:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    by_field: dict[str, FieldAccuracy] = {}
    for name in STRUCTURED_FIELDS:
        outcomes = [c.structured_fields[name] for c in cases if c.structured_fields[name] is not None]
        correct = sum(1 for o in outcomes if o)
        by_field[name] = FieldAccuracy(correct=correct, total=len(outcomes), accuracy=_ratio(correct, len(outcomes)))
    field_correct = sum(f.correct for f in by_field.values())
    field_total = sum(f.total for f in by_field.values())

    risk = [c.risk_correct for c in cases if c.risk_correct is not None]
    emergency = [c.emergency_correct for c in cases if c.emergency_correct is not None]
    rag = [c.rag_recall for c in cases if c.rag_recall is not None]
    judged = [c.judge_score for c in cases if c.judge_score is not None]
    contradiction_count, contradiction_n, contradiction_rate = summarize_self_contradiction(
        [c.self_contradiction for c in cases]
    )

    return MetricsSummary(
        n_requests=len({c.request_id for c in cases}),
        reps=reps,
        n_runs=n_runs,
        n_errors=sum(1 for c in cases if c.error),
        verdict_accuracy=_ratio(sum(c.verdict_correct for c in cases), n_runs),
        violation_tp=tp,
        violation_fp=fp,
        violation_fn=fn,
        violation_precision=precision,
        violation_recall=recall,
        violation_f1=f1,
        structured_accuracy=_ratio(field_correct, field_total),
        structured_by_field=by_field,
        hallucinated_count=sum(len(c.hallucinated) for c in cases),
        miscited_count=sum(len(c.miscited) for c in cases),
        miscited_strict_count=sum(len(c.miscited_strict) for c in cases),
        risk_accuracy=_ratio(sum(risk), len(risk)),
        risk_n=len(risk),
        emergency_accuracy=_ratio(sum(emergency), len(emergency)),
        emergency_n=len(emergency),
        self_contradiction_rate=contradiction_rate,
        self_contradiction_count=contradiction_count,
        self_contradiction_n=contradiction_n,
        rag_recall_at_3=_mean(rag),
        consistency=_consistency(cases, reps),
        alternative_score=_mean([float(s) for s in judged]),
        alternative_n=len(judged),
    )
