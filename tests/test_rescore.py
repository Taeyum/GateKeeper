"""저장된 측정 결과 재채점 테스트 (리뷰 J1). LLM은 부르지 않는다."""

import json
from datetime import datetime

import pytest

from app.data_loader import load_rules
from app.schemas import KST
from eval.rescore import RescoreError, contradiction_flags, jsonl_path_for, main, rescore_summary

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=KST)

# 4단계에서 기록한 v0 dev 측정(ollama qwen3:8b)의 (판정, 위반 목록). 금지 조항(CHG-01/02/03/06)을 위반으로 적고도
# "조건부 승인"을 낸 6건이 자기모순이다. 리뷰는 CHG-01/06만 세서 5건(D09 제외)이라고 적었고 정의대로는 6건이다.
V0_DEV = [
    ("REQ-D01", "반려", ["CHG-01", "CHG-06"], False),
    ("REQ-D02", "조건부 승인", ["CHG-01", "CHG-06"], True),
    ("REQ-D03", "조건부 승인", ["CHG-01", "CHG-06"], True),
    ("REQ-D04", "승인", [], False),
    ("REQ-D05", "반려", ["CHG-02", "CHG-03", "CHG-06"], False),
    ("REQ-D06", "조건부 승인", ["CHG-01", "CHG-06"], True),
    ("REQ-D07", "조건부 승인", ["CHG-01", "CHG-06"], True),
    ("REQ-D08", "조건부 승인", ["CHG-01", "CHG-06"], True),
    ("REQ-D09", "조건부 승인", ["CHG-02", "CHG-03", "CHG-09"], True),
    ("REQ-D10", "승인", [], False),
]


def record(request_id, verdict, violations, **extra):
    return {"request_id": request_id, "rep": 0, "error": None, "verdict_pred": verdict, "violations_pred": violations, **extra}


def write_pair(directory, records, *, n_runs=None):
    summary = {
        "meta": {"version": "v0", "split": "dev"},
        "metrics": {"n_runs": len(records) if n_runs is None else n_runs, "verdict_accuracy": 0.5, "consistency": None},
        "latency": {"p95_s": 65.5},
    }
    summary_path = directory / "20261021-144948_v0_dev.summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    (directory / "20261021-144948_v0_dev.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    return summary_path


def test_v0_dev_측정은_정의대로_6건이_자기모순이다():
    flags = contradiction_flags([record(i, v, vs) for i, v, vs, _ in V0_DEV], load_rules())
    assert flags == [expected for *_, expected in V0_DEV]
    assert sum(flags) == 6


def test_재채점은_summary에_지표와_출처를_더하고_다른_값은_그대로_둔다(tmp_path):
    path = write_pair(tmp_path, [record(i, v, vs) for i, v, vs, _ in V0_DEV])
    before = json.loads(path.read_text(encoding="utf-8"))
    summary = rescore_summary(path, now=NOW)

    metrics = summary["metrics"]
    assert (metrics["self_contradiction_count"], metrics["self_contradiction_n"], metrics["self_contradiction_rate"]) == (6, 10, 0.6)
    assert summary["meta"]["rescored"] == {"at": NOW.isoformat(), "metrics": ["self_contradiction"], "source": "jsonl"}
    assert json.loads(path.read_text(encoding="utf-8")) == summary  # 파일에도 썼다
    for key in ("verdict_accuracy", "consistency", "n_runs"):
        assert metrics[key] == before["metrics"][key]
    assert summary["latency"] == before["latency"] and summary["meta"]["version"] == "v0"


def test_다시_돌려도_같은_값이다(tmp_path):
    path = write_pair(tmp_path, [record(i, v, vs) for i, v, vs, _ in V0_DEV])
    first = rescore_summary(path, now=NOW)
    second = rescore_summary(path, now=NOW)
    assert first == second


def test_오류_난_실행은_분모에서_빠진다(tmp_path):
    records = [
        record("REQ-D01", "반려", ["CHG-01"]),
        record("REQ-D02", "조건부 승인", ["CHG-01"]),
        {"request_id": "REQ-D03", "rep": 0, "error": "TimeoutError", "verdict_pred": "ERROR", "violations_pred": []},
    ]
    summary = rescore_summary(write_pair(tmp_path, records), now=NOW)
    assert (summary["metrics"]["self_contradiction_count"], summary["metrics"]["self_contradiction_n"]) == (1, 2)


def test_실행이_하나도_따질_수_없으면_비율은_None이다(tmp_path):
    only_error = {"request_id": "REQ-D01", "rep": 0, "error": "X", "verdict_pred": "ERROR", "violations_pred": []}
    summary = rescore_summary(write_pair(tmp_path, [only_error]), now=NOW)
    assert summary["metrics"]["self_contradiction_rate"] is None and summary["metrics"]["self_contradiction_n"] == 0


def test_jsonl이_없거나_건수가_다르거나_이름이_다르면_멈춘다(tmp_path):
    path = write_pair(tmp_path, [record("REQ-D01", "반려", ["CHG-01"])], n_runs=2)
    with pytest.raises(RescoreError, match="n_runs"):
        rescore_summary(path, now=NOW)
    path.with_name(path.name.removesuffix(".summary.json") + ".jsonl").unlink()
    with pytest.raises(RescoreError, match="jsonl이 없다"):
        rescore_summary(path, now=NOW)
    with pytest.raises(RescoreError, match="summary 파일"):
        jsonl_path_for(tmp_path / "notes.json")


def test_LLM을_부르지_않는다(tmp_path, monkeypatch):
    import app.llm.factory as factory

    def forbidden(*args, **kwargs):
        raise AssertionError("재채점은 LLM을 부르지 않는다")

    monkeypatch.setattr(factory, "get_llm", forbidden)
    monkeypatch.setattr(factory, "structured_llm", forbidden)
    rescore_summary(write_pair(tmp_path, [record(i, v, vs) for i, v, vs, _ in V0_DEV]), now=NOW)


def test_CLI는_집계만_출력한다(tmp_path, capsys):
    path = write_pair(tmp_path, [record(i, v, vs) for i, v, vs, _ in V0_DEV])
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "60.0% (6/10)" in out
    assert "REQ-D" not in out and "CHG-" not in out  # 요청별 정보와 판정 상세를 내지 않는다


def test_CLI는_실패한_파일이_있으면_종료코드_1이고_나머지는_계속한다(tmp_path, capsys):
    good = write_pair(tmp_path, [record("REQ-D01", "반려", ["CHG-01"])])
    missing = tmp_path / "nope.summary.json"
    missing.write_text(json.dumps({"meta": {}, "metrics": {"n_runs": 1}}), encoding="utf-8")
    assert main([str(missing), str(good)]) == 1
    captured = capsys.readouterr()
    assert "재채점하지 못했다" in captured.err and "0.0% (0/1)" in captured.out
    assert "self_contradiction_rate" in json.loads(good.read_text(encoding="utf-8"))["metrics"]

