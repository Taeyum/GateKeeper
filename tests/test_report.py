"""리포트와 차트 테스트 (합성 summary, fake provider 실행 결과)."""

import copy
import json
from datetime import datetime

import pytest

from app import data_loader
from app.data_loader import load_requests
from app.llm.fake import set_fake_responses
from app.schemas import KST
from eval.evaluators import STRUCTURED_FIELDS, aggregate
from eval.harness import RunConfig, run_evaluation
from eval.report import (
    VERSIONS,
    _nice_max,
    build_report,
    chart_specs,
    comparability_warnings,
    detail_table,
    headline_table,
    main,
    select_latest,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=KST)


def make_summary(version="v0", *, split="dev", provider="ollama", started="2026-10-21T09:00:00+09:00", **overrides):
    """harness가 쓰는 summary와 같은 키를 가진 합성 summary."""
    metrics = aggregate([], 1).model_dump()
    metrics.update(
        n_requests=10, n_runs=10, n_errors=0, verdict_accuracy=0.6, violation_precision=0.7, violation_recall=0.5,
        violation_f1=0.5833, structured_accuracy=0.8, hallucinated_count=3, miscited_count=2, miscited_strict_count=3,
        risk_accuracy=0.75, risk_n=8, emergency_accuracy=1.0, emergency_n=1, rag_recall_at_3=None, consistency=None,
        alternative_score=3.0, alternative_n=6, self_contradiction_rate=0.6, self_contradiction_count=6,
        self_contradiction_n=10,
    )  # fmt: skip
    summary = {
        "meta": {
            "version": version, "split": split, "provider": provider, "reps": 1, "n_requests": 10,
            "started_at": started, "finished_at": started,
            "git": {"commit": "a" * 40, "dirty": False},
            "models": {"configured": {"main": "qwen3:8b", "small": "qwen3:0.6b", "judge": "qwen3:8b"},
                       "observed": ["qwen3:8b"], "judge_observed": ["qwen3:8b"]},
            "prompt_sha256": {"baseline_v0.md": "b" * 64, "judge_prompt.md": "c" * 64},
            "data_sha256": {"change_policy.md": "1", "rules.json": "2", "cmdb.json": "3", "change_calendar.json": "4",
                            "open_incidents.json": "5", f"requests_{split}.json": "6"},
            "packages": {}, "ollama_num_ctx": 16384, "timeout_s": 600.0, "warmup": "first_request", "warmup_error": None,
            "langsmith": {"enabled": False, "project": None},
            "judge": {"enabled": True, "model": "qwen3:8b", "independent": False},
        },
        "metrics": metrics,
        "latency": {"n": 10, "mean_s": 20.0, "p95_s": 38.2, "max_s": 38.2},
        "usage": {
            "llm_calls_total": 10, "llm_calls_per_run": 1.0, "llm_errors": 0, "input_tokens_total": 80000,
            "output_tokens_total": 6000, "reasoning_tokens_total": 0, "input_tokens_per_run": 8000.0,
            "output_tokens_per_run": 600.0, "tokens_per_run": 8600.0, "by_model": {},
        },
        "cost": {"unit": "USD per 1M tokens", "total_usd": None, "per_run_usd": None},
        "judge_usage": {"llm_calls": 6, "input_tokens": 1, "output_tokens": 1, "errors": 0},
        "counters": {"parse_retries": 1},
        "flags": {"possible_truncation_runs": 0},
    }  # fmt: skip
    for section, values in overrides.items():
        summary[section].update(values)
    return summary


def write_summaries(directory, summaries):
    directory.mkdir(parents=True, exist_ok=True)
    for index, summary in enumerate(summaries):
        meta = summary["meta"]
        name = f"{index:02d}_{meta['version']}_{meta['split']}.summary.json"
        (directory / name).write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")


def three_versions():
    v0 = make_summary("v0")
    v1 = make_summary("v1", metrics={"verdict_accuracy": 0.8, "violation_f1": 0.85, "hallucinated_count": 1},
                      latency={"p95_s": 60.0}, usage={"tokens_per_run": 12000.0})  # fmt: skip
    v2 = make_summary("v2", metrics={"verdict_accuracy": 0.9, "violation_f1": 0.9, "hallucinated_count": 0,
                                     "rag_recall_at_3": 0.8}, latency={"p95_s": 80.0}, usage={"tokens_per_run": 15000.0})  # fmt: skip
    return [v0, v1, v2]


# ---------------------------------------------------------------------------
# summary 고르기
# ---------------------------------------------------------------------------


def test_버전별_최신_summary를_고른다():
    old = make_summary("v0", started="2026-10-21T09:00:00+09:00", metrics={"verdict_accuracy": 0.1})
    new = make_summary("v0", started="2026-10-21T11:00:00+09:00", metrics={"verdict_accuracy": 0.9})
    other_split = make_summary("v0", split="eval", started="2026-10-21T12:00:00+09:00")
    provider, chosen = select_latest([old, new, other_split], "dev", None)
    assert provider == "ollama" and set(chosen) == {"v0"} and chosen["v0"]["metrics"]["verdict_accuracy"] == 0.9


def test_provider를_안_주면_가장_최근_실행의_provider를_쓰고_다른_provider는_섞지_않는다():
    local = make_summary("v0", provider="ollama", started="2026-10-21T09:00:00+09:00")
    cloud = make_summary("v1", provider="anthropic", started="2026-10-21T10:00:00+09:00")
    provider, chosen = select_latest([local, cloud], "dev", None)
    assert provider == "anthropic" and set(chosen) == {"v1"}
    provider, chosen = select_latest([local, cloud], "dev", "ollama")
    assert provider == "ollama" and set(chosen) == {"v0"}


def test_split에_summary가_없으면_빈_결과():
    assert select_latest([make_summary("v0")], "eval", None) == (None, {})


# ---------------------------------------------------------------------------
# 표
# ---------------------------------------------------------------------------


def test_Before_After_표는_없는_버전을_대시로_두고_변화를_v0에서_v2로_계산한다():
    only_v0 = headline_table({"v0": make_summary("v0")})
    row = next(line for line in only_v0.splitlines() if line.startswith("| 판정 정확도"))
    assert row == "| 판정 정확도 | 60.0% | - | - | - |"

    full = {s["meta"]["version"]: s for s in three_versions()}
    table = headline_table(full)
    assert "| 판정 정확도 | 60.0% | 80.0% | 90.0% | +30.0%p |" in table
    assert "| 환각 조항 수 | 3 | 1 | 0 | -3 |" in table
    assert "| 지연 p95 (초) | 38.2 | 60.0 | 80.0 | +41.8 |" in table
    assert "| 토큰 / 실행 (입력 + 출력) | 8,600 | 12,000 | 15,000 | +6,400 |" in table
    assert "| RAG 재현율@3 | - | - | 80.0% | - |" in table  # v0, v1은 대상이 아니라 변화도 없다
    assert "| 예상 비용 / 실행 (USD) | - | - | - | - |" in table


def test_자기모순_지표는_Before_After_표와_전체_지표_표에_나온다():
    v0 = make_summary("v0")
    v1 = make_summary("v1", metrics={"self_contradiction_rate": 0.0, "self_contradiction_count": 0})
    v2 = make_summary("v2", metrics={"self_contradiction_rate": 0.0, "self_contradiction_count": 0})
    table = headline_table({"v0": v0, "v1": v1, "v2": v2})
    assert "| 판정-위반 자기모순 (낮을수록 좋음) | 60.0% | 0.0% | 0.0% | -60.0%p |" in table
    detail = detail_table({"v0": v0, "v1": v1})
    assert "| 판정-위반 자기모순 (낮을수록 좋음) | 60.0% | 0.0% | - |" in detail
    assert "|   자기모순 실행 수 / 따진 실행 수 | 6 / 10 | 0 / 10 | - |" in detail


def test_자기모순_지표가_없던_시절의_summary는_대시로_나오고_리포트가_깨지지_않는다(tmp_path):
    old = make_summary("v0")
    for key in ("self_contradiction_rate", "self_contradiction_count", "self_contradiction_n"):
        del old["metrics"][key]
    assert "| 판정-위반 자기모순 (낮을수록 좋음) | - | - | - | - |" in headline_table({"v0": old})
    write_summaries(tmp_path, [old])
    assert build_report(tmp_path, tmp_path / "report.md", tmp_path / "charts", ["dev"], None, now=NOW) == ["dev"]


def test_ollama에서_v1을_재면_병렬이_줄을_설_수_있다는_주석과_NUM_PARALLEL_행이_나온다(tmp_path):
    v0, v1 = make_summary("v0"), make_summary("v1")
    v1["meta"]["ollama_num_parallel"] = "1"
    write_summaries(tmp_path, [v0, v1])
    build_report(tmp_path, tmp_path / "report.md", tmp_path / "charts", ["dev"], None, now=NOW)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| OLLAMA_NUM_PARALLEL | - | 1 | - |" in text and "서버에서 줄을 서서 병렬의 지연 이득이" in text

    only_v0 = tmp_path / "v0_only"
    write_summaries(only_v0, [v0])
    build_report(only_v0, only_v0 / "report.md", only_v0 / "charts", ["dev"], None, now=NOW)
    assert "줄을 서서" not in (only_v0 / "report.md").read_text(encoding="utf-8")  # v0은 병렬 호출이 없다


def test_rescored_실행은_리포트_참고에_밝힌다(tmp_path):
    rescored = make_summary("v0")
    rescored["meta"]["rescored"] = {"at": "2026-10-21T18:00:00+09:00", "metrics": ["self_contradiction"], "source": "jsonl"}
    write_summaries(tmp_path, [rescored])
    build_report(tmp_path, tmp_path / "report.md", tmp_path / "charts", ["dev"], None, now=NOW)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "측정 뒤 저장된 결과(jsonl)로 지표를 다시 계산" in text and "구조적으로 0이다" in text


# ---------------------------------------------------------------------------
# 비교 가능성 경고
# ---------------------------------------------------------------------------


def test_모델_요청_수_추적_여부가_다르면_경고하고_커밋_차이는_경고하지_않는다():
    v0, v1, _ = three_versions()
    v1["meta"]["git"]["commit"] = "f" * 40  # 단계마다 달라지는 것이 당연하다
    assert comparability_warnings({"v0": v0, "v1": v1}) == []
    v1["meta"]["models"]["configured"]["main"] = "claude-sonnet-5-5"
    v1["meta"]["n_requests"] = 20
    v1["meta"]["langsmith"]["enabled"] = True
    v1["meta"]["data_sha256"]["cmdb.json"] = "changed"
    text = "\n".join(comparability_warnings({"v0": v0, "v1": v1}))
    for label in ("main 모델", "요청 수", "LangSmith 추적 여부", "규정/CMDB/캘린더/티켓 데이터"):
        assert label in text
    assert comparability_warnings({"v0": v0}) == []


# ---------------------------------------------------------------------------
# 차트
# ---------------------------------------------------------------------------


def test_차트_사양_6장():
    chosen = {s["meta"]["version"]: s for s in three_versions()}
    specs = chart_specs(chosen)
    assert [s["name"] for s in specs] == [
        "verdict_accuracy", "violation_f1", "latency_p95", "tokens_per_run", "consistency", "self_contradiction",
    ]  # fmt: skip
    latency = next(s for s in specs if s["name"] == "latency_p95")
    assert latency["values"] == {"v0": 38.2, "v1": 60.0, "v2": 80.0} and latency["ylim"][1] >= 80.0 * 1.18
    assert next(s for s in specs if s["name"] == "consistency")["values"] == {"v0": None, "v1": None, "v2": None}
    assert next(s for s in specs if s["name"] == "self_contradiction")["values"] == {"v0": 0.6, "v1": 0.6, "v2": 0.6}


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 1.0), (0.8, 1.0), (12.3, 15), (38.2, 50), (96.0, 120), (8600, 12000), (15000, 20000)]
)
def test_축_상한은_막대_위_라벨_자리를_남긴다(value, expected):
    top = _nice_max(value)
    assert top == expected and (value == 0 or top >= value * 1.18)


# ---------------------------------------------------------------------------
# report.md
# ---------------------------------------------------------------------------


def test_report_md와_차트_PNG를_만든다(tmp_path):
    write_summaries(tmp_path, three_versions())
    out = tmp_path / "report.md"
    written = build_report(tmp_path, out, tmp_path / "charts", ["dev", "eval"], None, now=NOW)
    assert written == ["dev"]  # eval summary는 없다
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# GateKeeper 평가 리포트") and "생성 2026-10-21 12:00" in text
    assert "원자료는 results/*.summary.json이다.\n\n## dev 평가" in text  # 머리말과 첫 절 사이에 빈 줄
    for heading in ("## dev 평가", "### Before/After 표", "### 전체 지표", "### 구조화 필드별 정확도", "### 실험 메타데이터", "### 참고", "### 차트"):
        assert heading in text
    assert "| 판정 정확도 | 60.0% | 80.0% | 90.0% | +30.0%p |" in text
    assert all(name in text for name in STRUCTURED_FIELDS)
    assert "비독립 평가" in text and "n=10이면 최댓값과 같다" in text  # 비독립 Judge, p95 정의 주석
    images = sorted((tmp_path / "charts").glob("dev_*.png"))
    assert [p.stem for p in images] == sorted(
        f"dev_{n}"
        for n in ("verdict_accuracy", "violation_f1", "latency_p95", "tokens_per_run", "consistency", "self_contradiction")
    )
    assert all(p.read_bytes().startswith(PNG_MAGIC) and p.stat().st_size > 5_000 for p in images)
    assert "![dev_verdict_accuracy](charts/dev_verdict_accuracy.png)" in text


def test_버전이_하나뿐이어도_리포트가_만들어진다(tmp_path):
    write_summaries(tmp_path, [make_summary("v0")])
    assert build_report(tmp_path, tmp_path / "report.md", tmp_path / "charts", ["dev"], None, now=NOW) == ["dev"]
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "측정한 버전: v0." in text and "경고" not in text


def test_split별로_절을_나누고_eval_절에는_집계만_있다(tmp_path):
    write_summaries(tmp_path, [make_summary("v0"), make_summary("v0", split="eval", started="2026-10-21T10:00:00+09:00")])
    out = tmp_path / "report.md"
    assert build_report(tmp_path, out, tmp_path / "charts", ["dev", "eval"], None, now=NOW) == ["dev", "eval"]
    text = out.read_text(encoding="utf-8")
    assert "## dev 평가" in text and "## eval 평가" in text
    assert "REQ-" not in text  # 요청 ID나 본문이 없다
    assert (tmp_path / "charts/eval_verdict_accuracy.png").exists()
    # --split을 좁히면 그 절만 쓴다
    build_report(tmp_path, out, tmp_path / "charts", ["eval"], None, now=NOW)
    assert "## dev 평가" not in out.read_text(encoding="utf-8")


def test_CLI는_summary가_없으면_종료코드_1(tmp_path, capsys):
    assert main(["--results-dir", str(tmp_path)]) == 1
    assert "summary가 없다" in capsys.readouterr().err


def test_CLI가_report를_만든다(tmp_path, capsys):
    write_summaries(tmp_path, three_versions())
    assert main(["--split", "dev", "--results-dir", str(tmp_path)]) == 0
    assert (tmp_path / "report.md").exists() and "report.md" in capsys.readouterr().out


async def test_실제_실행_결과_summary로도_리포트가_만들어진다(tmp_path, monkeypatch):
    """합성 summary와 harness의 실제 summary 형식이 어긋나지 않는지 확인한다."""
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("GK_VERSION", "v0")
    data_loader.clear_caches()
    item = load_requests("dev")[1]  # D02 승인(대안 없음)
    expected = item.expected
    response = {
        "structured": expected.structured.model_dump(mode="json", exclude={"acceptable_change_types", "time_note"}),
        "recommendation": {"verdict": expected.verdict, "risk": expected.risk, "explanation": "설명"},
    }
    set_fake_responses("main", [response, response])
    cfg = RunConfig(version="v0", split="dev", provider="fake", limit=None, results_dir=tmp_path, judge=False)
    cfg.limit = 1
    # 요청 1건만 돌리려고 로더 결과를 D02 하나로 줄인다
    monkeypatch.setattr("eval.harness.load_split", lambda split: [item])
    await run_evaluation(cfg, now=lambda: datetime(2026, 10, 21, 9, 0, tzinfo=KST), out=lambda _: None)
    assert build_report(tmp_path, tmp_path / "report.md", tmp_path / "charts", ["dev"], None, now=NOW) == ["dev"]
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| 판정 정확도 | 100.0% | - | - | - |" in text and "provider fake" in text


def test_summary_복사본을_바꿔도_원본은_그대로():
    base = make_summary("v0")
    copy.deepcopy(base)["metrics"]["verdict_accuracy"] = 0.0
    assert base["metrics"]["verdict_accuracy"] == 0.6 and VERSIONS == ("v0", "v1", "v2")
