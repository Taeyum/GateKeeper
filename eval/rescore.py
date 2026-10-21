"""저장된 측정 결과로 판정-위반 자기모순 지표를 다시 계산한다 (리뷰 J1).

    python eval/rescore.py results/20261021-144948_v0_dev.summary.json

짝이 되는 jsonl(같은 이름, 확장자만 .jsonl)의 verdict_pred와 violations_pred만 읽는다. LLM은 부르지 않는다.
기준선을 다시 돌리지 않는다는 원칙(EVAL.md 2장)과 맞다. 결과는 summary의 metrics에 넣고, 측정 뒤에 더한 값임을
meta.rescored에 남긴다. 터미널에는 집계만 낸다 (eval split에서도 요청별 정보를 내지 않는다).
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_loader import load_rules  # noqa: E402
from app.schemas import KST, RulesConfig  # noqa: E402
from eval.evaluators import ERROR_VERDICT, normalize_rule_id, self_contradiction, summarize_self_contradiction  # noqa: E402

SUMMARY_SUFFIX = ".summary.json"
METRIC_KEYS = ("self_contradiction_rate", "self_contradiction_count", "self_contradiction_n")


class RescoreError(RuntimeError):
    """재채점을 할 수 없는 입력."""


def jsonl_path_for(summary_path: Path) -> Path:
    if not summary_path.name.endswith(SUMMARY_SUFFIX):
        raise RescoreError(f"summary 파일(*{SUMMARY_SUFFIX})이 아니다: {summary_path}")
    return summary_path.with_name(summary_path.name.removesuffix(SUMMARY_SUFFIX) + ".jsonl")


def read_records(path: Path) -> list[dict]:
    if not path.exists():
        raise RescoreError(f"짝이 되는 jsonl이 없다: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def contradiction_flags(records: list[dict], rules: RulesConfig) -> list[bool | None]:
    """실행마다 자기모순 여부. 오류 난 실행(verdict_pred가 ERROR)은 None."""
    flags = []
    for record in records:
        verdict = record.get("verdict_pred", ERROR_VERDICT)
        violations = sorted({normalize_rule_id(rule_id) for rule_id in record.get("violations_pred") or []})
        flags.append(self_contradiction(verdict, violations, rules))
    return flags


def rescore_summary(summary_path: Path, *, now: datetime | None = None, rules: RulesConfig | None = None) -> dict:
    """summary에 자기모순 지표를 더해 파일을 다시 쓰고, 새로 쓴 summary를 돌려준다."""
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = read_records(jsonl_path_for(summary_path))
    expected = summary["metrics"]["n_runs"]
    if len(records) != expected:
        raise RescoreError(f"jsonl의 실행 {len(records)}건이 summary의 n_runs({expected})와 다르다. 짝이 맞는 파일인지 확인한다.")

    count, n, rate = summarize_self_contradiction(contradiction_flags(records, rules or load_rules()))
    summary["metrics"].update(self_contradiction_rate=rate, self_contradiction_count=count, self_contradiction_n=n)
    summary["meta"]["rescored"] = {
        "at": (now or datetime.now(KST)).isoformat(),
        "metrics": ["self_contradiction"],
        "source": "jsonl",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="저장된 jsonl로 판정-위반 자기모순 지표를 다시 계산해 summary에 더한다.")
    parser.add_argument("summary", type=Path, nargs="+", help="results/*.summary.json")
    args = parser.parse_args(argv)

    status = 0
    for path in args.summary:
        try:
            summary = rescore_summary(path)
        except (RescoreError, OSError, KeyError, json.JSONDecodeError) as exc:
            print(f"{path.name}: 재채점하지 못했다 ({type(exc).__name__}: {exc})", file=sys.stderr)
            status = 1
            continue
        m = summary["metrics"]
        rate = "-" if m["self_contradiction_rate"] is None else f"{m['self_contradiction_rate'] * 100:.1f}%"
        print(f"{path.name}: 판정-위반 자기모순 {rate} ({m['self_contradiction_count']}/{m['self_contradiction_n']})")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
