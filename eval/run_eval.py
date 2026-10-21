"""평가 실행 CLI.

    python eval/run_eval.py --version v0 --split dev --reps 1 --provider ollama
    python eval/run_eval.py --version v0 --split dev --reps 1 --provider ollama --langsmith

결과는 results/<날짜시각>_<버전>_<split>.jsonl(요청별)과 .summary.json(집계)로 저장한다.
--split eval이면 집계만 출력한다 (CLAUDE.md 절대 규칙 1). eval 실행은 9단계에서만 한다.
실제 로직은 eval/harness.py에 있다.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.harness import RESULTS_DIR, RunConfig, RunConfigError, format_summary, run_evaluation  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GateKeeper Before/After 평가 실행")
    parser.add_argument("--version", choices=["v0", "v1", "v2"], required=True)
    parser.add_argument("--split", choices=["dev", "eval"], required=True)
    parser.add_argument("--reps", type=int, default=1, help="요청당 반복 횟수 (일관성 지표는 2 이상)")
    parser.add_argument("--provider", choices=["fake", "ollama", "anthropic", "openai"], required=True)
    parser.add_argument("--langsmith", action="store_true", help="측정 중 LangSmith 추적을 켠다 (프로젝트 gk-<버전>-<split>-<시각>)")
    parser.add_argument("--no-judge", action="store_true", help="대안 품질 Judge를 건너뛴다")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N건만 (dev에서만)")
    parser.add_argument("--timeout", type=float, default=600.0, help="요청 1건 시간 제한(초)")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = RunConfig(
        version=args.version,
        split=args.split,
        reps=args.reps,
        provider=args.provider,
        langsmith=args.langsmith,
        judge=not args.no_judge,
        limit=args.limit,
        timeout=args.timeout,
        results_dir=args.results_dir,
    )
    try:
        outcome = asyncio.run(run_evaluation(cfg))
    except RunConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2
    except NotImplementedError as exc:
        print(f"아직 없는 버전이다: {exc}", file=sys.stderr)
        return 2
    print()
    print(format_summary(outcome.summary))
    print(f"\n요청별 결과: {outcome.jsonl_path}\n집계: {outcome.summary_path}")
    # 모든 실행이 오류면 성공으로 끝내지 않는다 (설정 문제를 조용히 넘기지 않으려는 것).
    metrics = outcome.summary["metrics"]
    return 1 if metrics["n_runs"] and metrics["n_errors"] == metrics["n_runs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
