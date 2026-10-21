"""평가 지표 테스트. 가짜 예측으로 손계산 값과 비교한다 (dev 라벨만 쓴다)."""

from datetime import datetime

import pytest

from app.data_loader import load_requests, load_rules
from app.schemas import ChangeRequest, LabeledRequest, Recommendation, Violation
from eval.evaluators import (
    ERROR_VERDICT,
    STRUCTURED_FIELDS,
    Prediction,
    aggregate,
    compare_field,
    normalize_source,
    percentile_nearest_rank,
    policy_rule_ids,
    score_case,
    self_contradiction,
    summarize_self_contradiction,
    verdict_from_own_violations,
)

VALID = frozenset({f"CHG-{n:02d}" for n in range(1, 11)} | {"STD-01", "STD-02"})


def gold(request_id: str) -> LabeledRequest:
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def structured_of(item: LabeledRequest, **overrides) -> ChangeRequest:
    data = item.expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    data.update(overrides)
    return ChangeRequest.model_validate(data)


def recommendation_of(item: LabeledRequest, **overrides) -> Recommendation:
    data = {
        "violations": [Violation(rule_id=r, severity="forbidden", evidence="x") for r in item.expected.violations],
        "verdict": item.expected.verdict,
        "risk": item.expected.risk,
        "emergency_valid": item.expected.emergency_valid,
        "citations": list(item.expected.violations),
    }
    data.update(overrides)
    return Recommendation.model_validate(data)


def perfect(item: LabeledRequest, **rec_overrides) -> Prediction:
    return Prediction(structured=structured_of(item), recommendation=recommendation_of(item, **rec_overrides))


def score(item: LabeledRequest, pred: Prediction, **kwargs):
    return score_case(item, pred, valid_rule_ids=VALID, **kwargs)


# ---------------------------------------------------------------------------
# 판정, 위반
# ---------------------------------------------------------------------------


def test_정확한_예측은_모든_지표가_맞다():
    d01 = gold("REQ-D01")
    case = score(d01, perfect(d01))
    assert case.verdict_correct and case.verdict_pred == "반려"
    assert (case.tp, case.fp, case.fn) == (5, 0, 0)
    assert case.hallucinated == [] and case.miscited == [] and case.risk_correct is True
    # 비교하는 필드 13개(restart_required, cert_expires_at은 gold가 null이라 제외)가 모두 맞다
    assert case.structured_fields["restart_required"] is None and case.structured_fields["cert_expires_at"] is None
    assert [v for v in case.structured_fields.values() if v is not None] == [True] * 13


def test_위반_조항_TP_FP_FN은_집합_단위다():
    d01 = gold("REQ-D01")  # gold: CHG-01, 02, 03, 09, 10
    pred = perfect(d01)
    pred.recommendation = recommendation_of(
        d01,
        verdict="승인",
        violations=[Violation(rule_id=r, severity="forbidden", evidence="x") for r in ("CHG-01", "chg-02 ", "CHG-02", "CHG-04")],
    )
    case = score(d01, pred)
    assert not case.verdict_correct
    assert case.violations_pred == ["CHG-01", "CHG-02", "CHG-04"]  # strip/대문자, 중복 제거
    assert (case.tp, case.fp, case.fn) == (2, 1, 3)


# ---------------------------------------------------------------------------
# 환각, 오인용
# ---------------------------------------------------------------------------


def test_규정에_없는_조항은_환각_있지만_gold_위반이_아니면_오인용():
    d01 = gold("REQ-D01")
    pred = perfect(d01, citations=["CHG-01", "CHG-99", "CHG-04", "CHG-07"])
    case = score(d01, pred)
    assert case.hallucinated == ["CHG-99"]
    assert case.miscited == ["CHG-04", "CHG-07"]  # D01은 긴급이 아니라 CHG-07도 오인용
    assert case.miscited_strict == ["CHG-04", "CHG-07"]


def test_긴급_변경의_CHG_07_인용은_허용하고_면제_조항_인용은_오인용():
    d07 = gold("REQ-D07")  # 유효한 긴급, gold 위반 없음
    case = score(d07, perfect(d07, citations=["CHG-07", "CHG-01"]))
    assert case.miscited == ["CHG-01"]
    assert case.miscited_strict == ["CHG-01", "CHG-07"]  # 엄격 수치는 허용 목록이 없다


def test_표준_변경은_gold의_STD_인용만_허용한다():
    d04 = gold("REQ-D04")  # STD-01
    case = score(d04, perfect(d04, citations=["STD-01", "STD-02"]))
    assert case.miscited == ["STD-02"] and case.miscited_strict == ["STD-01", "STD-02"]


def test_violations에_있는_ID도_인용으로_센다():
    d06 = gold("REQ-D06")  # gold 위반 없음
    pred = perfect(d06, violations=[Violation(rule_id="CHG-77", severity="fixable", evidence="x")])
    assert score(d06, pred).hallucinated == ["CHG-77"]


# ---------------------------------------------------------------------------
# 구조화 필드
# ---------------------------------------------------------------------------


def test_단일_IP는_32로_정규화해서_비교한다():
    d02 = gold("REQ-D02")  # source 203.0.113.10/32
    field = lambda src: compare_field("source", d02.expected.structured, structured_of(d02, source=src))  # noqa: E731
    assert field("203.0.113.10") is True
    assert field("203.0.113.10/32") is True
    assert field("203.0.113.0/24") is False
    assert field(None) is False
    assert normalize_source("10.0.0.2, 10.0.0.1") == ("10.0.0.1/32", "10.0.0.2/32")
    assert normalize_source("아무 데서나") == ("아무 데서나",)
    assert normalize_source(" ") is None


def test_시각은_KST_분_단위로_비교한다():
    d01 = gold("REQ-D01")  # start 2026-10-30 20:00 KST
    gold_struct = d01.expected.structured
    same_instant = datetime.fromisoformat("2026-10-30T11:00:30+00:00")  # 20:00:30 KST, 초는 무시
    assert compare_field("start", gold_struct, structured_of(d01, start=same_instant)) is True
    one_minute = datetime.fromisoformat("2026-10-30T20:01:00+09:00")
    assert compare_field("start", gold_struct, structured_of(d01, start=one_minute)) is False
    assert compare_field("start", gold_struct, structured_of(d01, start=None)) is False


def test_gold가_range면_날짜_단위로_비교하고_끝은_마지막_순간의_날짜다():
    d01 = gold("REQ-D01")
    range_gold = d01.expected.structured.model_copy(
        update={
            "time_precision": "range",
            "start": datetime.fromisoformat("2026-10-31T00:00:00+09:00"),
            "end": datetime.fromisoformat("2026-11-02T00:00:00+09:00"),
        }
    )

    def check(name, **overrides):
        return compare_field(name, range_gold, structured_of(d01, **overrides))

    assert check("start", start=datetime.fromisoformat("2026-10-31T09:00:00+09:00")) is True
    assert check("start", start=datetime.fromisoformat("2026-11-01T00:00:00+09:00")) is False
    # 월요일 00:00과 일요일 23:59는 같은 날(마지막 순간이 일요일)로 본다
    assert check("end", end=datetime.fromisoformat("2026-11-02T00:00:00+09:00")) is True
    assert check("end", end=datetime.fromisoformat("2026-11-01T23:59:00+09:00")) is True
    assert check("end", end=datetime.fromisoformat("2026-11-03T00:00:00+09:00")) is False


def test_null과_false는_서로_다른_값이다():
    d01 = gold("REQ-D01")  # has_rollback gold null
    assert compare_field("has_rollback", d01.expected.structured, structured_of(d01, has_rollback=False)) is False
    assert compare_field("has_rollback", d01.expected.structured, structured_of(d01, has_rollback=None)) is True


def test_gold가_null인_restart_required와_cert_expires_at은_제외한다():
    d01, d08 = gold("REQ-D01"), gold("REQ-D08")  # D08 restart_required true
    assert compare_field("restart_required", d01.expected.structured, structured_of(d01, restart_required=True)) is None
    assert compare_field("restart_required", d08.expected.structured, structured_of(d08)) is True
    assert compare_field("restart_required", d08.expected.structured, structured_of(d08, restart_required=False)) is False


def test_change_type은_acceptable_change_types_안이면_정답이다():
    d01 = gold("REQ-D01")
    flexible = d01.expected.structured.model_copy(update={"acceptable_change_types": ["firewall", "deploy"]})
    assert compare_field("change_type", flexible, structured_of(d01, change_type="deploy")) is True
    assert compare_field("change_type", d01.expected.structured, structured_of(d01, change_type="deploy")) is False


def test_티켓과_인증서_만료일():
    d07, d10 = gold("REQ-D07"), gold("REQ-D10")
    assert compare_field("ticket", d07.expected.structured, structured_of(d07, ticket=" inc-2026-0412 ")) is True
    assert compare_field("ticket", d07.expected.structured, structured_of(d07, ticket="INC-2026-0999")) is False
    assert compare_field("cert_expires_at", d10.expected.structured, structured_of(d10)) is True
    assert compare_field("cert_expires_at", d10.expected.structured, structured_of(d10, cert_expires_at="2026-11-21")) is False


def test_필드_15개가_표와_같다():
    assert len(STRUCTURED_FIELDS) == 15 and len(set(STRUCTURED_FIELDS)) == 15


# ---------------------------------------------------------------------------
# 위험도, 긴급, RAG
# ---------------------------------------------------------------------------


def test_위험도는_gold가_null이면_제외한다():
    d04, d01 = gold("REQ-D04"), gold("REQ-D01")  # D04 표준 변경, risk null
    assert score(d04, perfect(d04)).risk_correct is None
    assert score(d01, perfect(d01, risk="낮음")).risk_correct is False
    assert score(d01, perfect(d01)).risk_correct is True


def test_긴급_판정은_gold가_긴급인_요청에서만_센다():
    d07, d01 = gold("REQ-D07"), gold("REQ-D01")
    assert score(d07, perfect(d07)).emergency_correct is True
    assert score(d07, perfect(d07, emergency_valid=False)).emergency_correct is False
    assert score(d01, perfect(d01)).emergency_correct is None


def test_RAG_재현율은_v2만_상위_3건_기준이다():
    d01 = gold("REQ-D01")  # similar_history HIS-049
    hit = perfect(d01).model_copy(update={"retrieved_history": ["HIS-001", "HIS-049", "HIS-002", "HIS-003"]})
    miss = perfect(d01).model_copy(update={"retrieved_history": ["HIS-001", "HIS-002", "HIS-003", "HIS-049"]})
    assert score(d01, hit).rag_recall is None  # v0, v1은 대상이 아니다
    assert score(d01, hit, rag_enabled=True).rag_recall == 1.0
    assert score(d01, miss, rag_enabled=True).rag_recall == 0.0
    # gold가 비었거나 null이면 대상이 아니다
    d05, d04 = gold("REQ-D05"), gold("REQ-D04")
    assert score(d05, perfect(d05), rag_enabled=True).rag_recall is None
    assert score(d04, perfect(d04), rag_enabled=True).rag_recall is None
    # 정답이 둘이고 하나만 찾으면 0.5
    two = d01.model_copy(update={"expected": d01.expected.model_copy(update={"similar_history": ["HIS-049", "HIS-050"]})})
    assert score(two, hit, rag_enabled=True).rag_recall == 0.5


# ---------------------------------------------------------------------------
# 오류 난 실행
# ---------------------------------------------------------------------------


def test_오류_난_실행은_모두_오답이고_위반은_빈_집합이다():
    d07 = gold("REQ-D07")
    case = score(d07, Prediction(error="TimeoutError"))
    assert case.error == "TimeoutError" and case.verdict_pred == ERROR_VERDICT and not case.verdict_correct
    assert (case.tp, case.fp, case.fn) == (0, 0, 0)  # D07은 gold 위반이 없다
    assert case.risk_correct is False and case.emergency_correct is False
    assert [v for v in case.structured_fields.values() if v is not None] == [False] * 13
    d01 = gold("REQ-D01")
    assert score(d01, Prediction(error="X")).fn == 5


def test_권고가_비어_있으면_오류로_센다():
    case = score(gold("REQ-D01"), Prediction())
    assert case.error == "MissingRecommendation" and case.verdict_pred == ERROR_VERDICT


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------


def _wrong_d01() -> Prediction:
    d01 = gold("REQ-D01")
    return Prediction(
        structured=structured_of(d01, source="1.2.3.4"),  # gold null이라 source 한 필드가 틀림
        recommendation=recommendation_of(
            d01,
            verdict="승인",
            violations=[Violation(rule_id=r, severity="forbidden", evidence="x") for r in ("CHG-01", "CHG-02", "CHG-04")],
        ),
    )


def test_위반_P_R_F1은_micro_집계다():
    d01 = gold("REQ-D01")
    cases = [score(d01, perfect(d01), rep=0), score(d01, _wrong_d01(), rep=1)]
    summary = aggregate(cases, reps=2)
    # TP 5+2, FP 0+1, FN 0+3
    assert (summary.violation_tp, summary.violation_fp, summary.violation_fn) == (7, 1, 3)
    assert summary.violation_precision == pytest.approx(7 / 8)
    assert summary.violation_recall == pytest.approx(7 / 10)
    assert summary.violation_f1 == pytest.approx(2 * (7 / 8) * (7 / 10) / (7 / 8 + 7 / 10))
    assert summary.verdict_accuracy == 0.5


def test_위반이_양쪽_모두_비면_P_R_F1은_None이다():
    d06 = gold("REQ-D06")
    summary = aggregate([score(d06, perfect(d06))], reps=1)
    assert (summary.violation_precision, summary.violation_recall, summary.violation_f1) == (None, None, None)


def test_분모가_0이지만_한쪽은_있으면_0점이다():
    d06, d01 = gold("REQ-D06"), gold("REQ-D01")
    over = perfect(d06, violations=[Violation(rule_id="CHG-01", severity="forbidden", evidence="x")])
    only_over = aggregate([score(d06, over)], reps=1)
    assert (only_over.violation_precision, only_over.violation_recall, only_over.violation_f1) == (0.0, 0.0, 0.0)
    nothing = aggregate([score(d01, Prediction(error="X"))], reps=1)
    assert (nothing.violation_precision, nothing.violation_recall, nothing.violation_f1) == (0.0, 0.0, 0.0)


def test_구조화_정확도는_필드_단위_micro_집계와_필드별_표를_낸다():
    d01 = gold("REQ-D01")
    summary = aggregate([score(d01, perfect(d01)), score(d01, _wrong_d01())], reps=1)
    assert summary.structured_accuracy == pytest.approx(25 / 26)
    assert summary.structured_by_field["source"].correct == 1 and summary.structured_by_field["source"].total == 2
    assert summary.structured_by_field["restart_required"].total == 0
    assert summary.structured_by_field["restart_required"].accuracy is None


def test_환각_오인용_합계와_위험도_긴급_개수():
    d01, d07, d04 = gold("REQ-D01"), gold("REQ-D07"), gold("REQ-D04")
    cases = [
        score(d01, perfect(d01, citations=["CHG-99", "CHG-04"])),
        score(d07, perfect(d07, citations=["CHG-07"])),
        score(d04, perfect(d04)),
    ]
    summary = aggregate(cases, reps=1)
    assert (summary.hallucinated_count, summary.miscited_count, summary.miscited_strict_count) == (1, 1, 2)
    assert (summary.risk_n, summary.risk_accuracy) == (2, 1.0)  # D04는 gold risk null이라 제외
    assert (summary.emergency_n, summary.emergency_accuracy) == (1, 1.0)


def test_일관성은_반복이_2회_이상일_때만_정의된다():
    d01, d05 = gold("REQ-D01"), gold("REQ-D05")
    same = [score(d01, perfect(d01), rep=r) for r in range(3)]
    flaky = [
        score(d05, perfect(d05, verdict=("반려", "승인", "반려")[r]), rep=r)
        for r in range(3)
    ]
    assert aggregate(same, reps=3).consistency == 1.0
    assert aggregate(same + flaky, reps=3).consistency == 0.5
    assert aggregate(same, reps=1).consistency is None


def test_오류가_섞인_요청은_일관적이지_않다():
    d01 = gold("REQ-D01")
    runs = [score(d01, perfect(d01), rep=0), score(d01, Prediction(error="TimeoutError"), rep=1)]
    summary = aggregate(runs, reps=2)
    assert summary.consistency == 0.0 and summary.n_errors == 1


def test_대안_품질은_Judge_점수의_평균이다():
    d01, d05, d06 = gold("REQ-D01"), gold("REQ-D05"), gold("REQ-D06")
    cases = [score(d01, perfect(d01)), score(d05, perfect(d05)), score(d06, perfect(d06))]
    assert [c.needs_alternative for c in cases] == [True, True, False]
    cases[0] = cases[0].model_copy(update={"judge_score": 4})
    cases[1] = cases[1].model_copy(update={"judge_score": 3})
    summary = aggregate(cases, reps=1)
    assert (summary.alternative_score, summary.alternative_n) == (3.5, 2)
    assert aggregate([cases[2]], reps=1).alternative_score is None


# ---------------------------------------------------------------------------
# 보조
# ---------------------------------------------------------------------------


def test_p95는_nearest_rank이고_n이_10이면_최댓값이다():
    assert percentile_nearest_rank([float(i) for i in range(1, 11)], 0.95) == 10.0
    assert percentile_nearest_rank([float(i) for i in range(1, 21)], 0.95) == 19.0
    assert percentile_nearest_rank([3.0], 0.95) == 3.0
    assert percentile_nearest_rank([5.0, 1.0, 3.0], 0.5) == 3.0
    assert percentile_nearest_rank([], 0.95) is None


def test_규정_조항_ID_목록():
    ids = policy_rule_ids()
    assert {f"CHG-{n:02d}" for n in range(1, 11)} <= ids and {"STD-01", "STD-02"} <= ids
    assert "CHG-11" not in ids and "CHG-00" not in ids


# ---------------------------------------------------------------------------
# 판정과 자기 위반 목록의 모순 (리뷰 J1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "violations", "recomputed"),
    [
        ("반려", ["CHG-01", "CHG-06"], "반려"),
        ("조건부 승인", ["CHG-01", "CHG-06"], "반려"),  # 금지 조항을 적고도 조건부 승인: v0 dev의 대표 모순
        ("조건부 승인", ["CHG-02", "CHG-03", "CHG-09"], "반려"),  # 금지가 섞인 보완
        ("조건부 승인", ["CHG-04", "CHG-08"], "조건부 승인"),
        ("승인", ["CHG-04"], "조건부 승인"),
        ("반려", ["CHG-04"], "조건부 승인"),
        ("승인", [], "승인"),
        ("자동 승인", [], "자동 승인"),
        ("조건부 승인", [], "승인"),
        ("반려", [], "승인"),
        ("자동 승인", ["CHG-10"], "반려"),
    ],
)
def test_자기_위반_목록으로_다시_계산한_판정(verdict, violations, recomputed):
    assert verdict_from_own_violations(verdict, violations, load_rules()) == recomputed
    assert self_contradiction(verdict, violations, load_rules()) is (verdict != recomputed)


def test_규정에_구분이_없는_ID와_절차_조항은_계산에서_뺀다():
    rules = load_rules()
    # CHG-07은 procedure, STD-01은 표준 목록, CHG-99는 지어낸 ID다. 구분이 없으니 위반으로 세지 않는다.
    assert self_contradiction("승인", ["CHG-07", "STD-01", "CHG-99"], rules) is False
    assert self_contradiction("자동 승인", ["STD-02"], rules) is False
    assert self_contradiction("조건부 승인", ["CHG-99", "CHG-04"], rules) is False


def test_pred가_스스로_단_severity는_보지_않고_rules_json의_구분을_쓴다():
    d01 = gold("REQ-D01")
    liar = Violation(rule_id="CHG-01", severity="fixable", evidence="x")  # CHG-01은 규정상 금지 조항
    case = score(d01, Prediction(structured=structured_of(d01), recommendation=recommendation_of(d01, verdict="조건부 승인", violations=[liar])))
    assert case.self_contradiction is True


def test_오류_난_실행은_자기모순을_정의할_수_없어_None이다():
    assert self_contradiction(ERROR_VERDICT, [], load_rules()) is None
    case = score(gold("REQ-D01"), Prediction(error="TimeoutError"))
    assert case.self_contradiction is None


def test_라벨대로_낸_예측은_모순이_없다():
    for item in load_requests("dev"):
        assert score(item, perfect(item)).self_contradiction is False, item.request_id


def test_자기모순_집계는_오류_실행을_분모에서_뺀다():
    d01, d02 = gold("REQ-D01"), gold("REQ-D02")
    contradicting = perfect(d01, verdict="조건부 승인")  # 금지 위반 5개를 적고 조건부 승인
    cases = [
        score(d01, perfect(d01)),
        score(d01, contradicting, rep=1),
        score(d02, Prediction(error="TimeoutError")),
    ]
    summary = aggregate(cases, reps=2)
    assert (summary.self_contradiction_count, summary.self_contradiction_n) == (1, 2)
    assert summary.self_contradiction_rate == 0.5 and summary.n_errors == 1
    assert summarize_self_contradiction([None, None]) == (0, 0, None)
    assert aggregate([], 1).self_contradiction_rate is None
