"""평가 실행 로직 (docs/EVAL.md 4장). run_eval.py는 이 모듈을 부르는 얇은 CLI다.

흐름: 환경 고정 -> provider/모델 검사 -> 정답셋 로드 -> warm-up(첫 요청 1건 버림) -> 요청 x rep 순차 실행
-> 채점 -> Judge -> 집계 -> jsonl/summary 저장.

공정성 장치
    같은 입력, 같은 provider와 모델. 요청은 순서대로 한 건씩 실행한다 (동시 실행은 지연 측정을 오염시킨다).
    warm-up은 모든 버전에 같은 방식(split의 첫 요청을 그래프로 한 번 실행하고 버림)이라 모델 로드와
    정적 프롬프트 접두사 캐시가 함께 데워진다. 추적은 --langsmith 여부로만 정해지고 .env 값은 무시한다.

절대 규칙 1 (eval 유출 방지)
    split이 eval이면 요청 본문, explanation, evidence, Judge 근거, 예외 메시지를 jsonl과 터미널에 내지 않는다.
    예외는 타입 이름만 남긴다. summary에는 요청별 목록이나 태그별 분해를 넣지 않는다.
"""

import asyncio
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from app import data_loader
from app.agent.errors import ReviewIncomplete
from app.agent.graph import build_graph
from app.agent.mcp_client import MCPClient
from app.agent.prompting import file_sha256, prompt_hashes
from app.config import PROJECT_ROOT, GKVersion, Provider, Settings, get_settings
from app.llm.usage import UsageCollector, UsageSummary
from app.masking import mask_secrets
from app.schemas import KST, LabeledRequest
from eval.data import load_split
from eval.evaluators import (
    CaseScore,
    Prediction,
    aggregate,
    percentile_nearest_rank,
    policy_rule_ids,
    score_case,
)
from eval.judge import JudgeScore, judge_alternative, judge_independent, judge_prompt_hash
from eval.tracing import api_key_present, project_name, tracing

RESULTS_DIR = PROJECT_ROOT / "results"
PRICING_PATH = Path(__file__).resolve().parent / "pricing.json"
ROLES = ("main", "small", "judge")
# 입력 토큰이 num_ctx에서 이만큼 안쪽으로 들어오면 출력 여유가 없어 잘렸을 수 있다고 본다.
TRUNCATION_MARGIN = 1024
_PACKAGES = ("langchain-core", "langchain-ollama", "langchain-anthropic", "langgraph", "langsmith", "pydantic")


class RunConfigError(RuntimeError):
    """실행 전에 잡은 설정 오류. LLM을 부르기 전에 멈춘다."""


@dataclass
class RunConfig:
    version: GKVersion
    split: str
    reps: int = 1
    provider: Provider = "ollama"
    langsmith: bool = False
    judge: bool = True
    limit: int | None = None
    timeout: float = 600.0
    results_dir: Path = RESULTS_DIR


@dataclass
class EvalOutcome:
    summary: dict[str, Any]
    jsonl_path: Path
    summary_path: Path


# ---------------------------------------------------------------------------
# 실행 전 점검
# ---------------------------------------------------------------------------


def check_provider_models(settings: Settings) -> None:
    """provider와 모델 이름이 맞는지 본다. .env의 MODEL_*가 다른 provider용 값이면 멈춘다.

    model_for는 provider를 보지 않아서, 예컨대 .env에 MODEL_MAIN=claude-sonnet-5-5가 있으면
    --provider ollama로 돌려도 그대로 쓰인다. 백그라운드 실행이면 아무도 출력을 안 보므로 출력이 아니라 검사로 막는다.
    """
    provider = settings.llm_provider
    problems: list[str] = []
    for role in ROLES:
        model = settings.model_for(role)
        if provider == "ollama" and model.startswith(("claude-", "gpt-")):
            problems.append(f"{role}={model}: ollama인데 다른 provider의 모델 이름")
        elif provider == "anthropic" and not model.startswith("claude-"):
            problems.append(f"{role}={model}: anthropic인데 claude-로 시작하지 않음")
        elif provider == "openai" and not model.startswith("gpt-"):
            problems.append(f"{role}={model}: openai인데 gpt-로 시작하지 않음")
    if problems:
        raise RunConfigError(
            f"provider={provider}와 모델 이름이 맞지 않는다: {'; '.join(problems)}. "
            ".env의 MODEL_MAIN/MODEL_SMALL/MODEL_JUDGE를 비우면 provider 기본값을 쓴다."
        )


def apply_run_env(version: str, provider: str) -> Settings:
    """버전과 provider를 환경 변수로 고정하고 캐시를 비운 설정을 돌려준다 (CLAUDE.md 절대 규칙 4, 6)."""
    os.environ["LLM_PROVIDER"] = provider
    os.environ["GK_VERSION"] = version
    data_loader.clear_caches()
    return get_settings()


# ---------------------------------------------------------------------------
# 비용, 메타데이터
# ---------------------------------------------------------------------------


def load_pricing(path: Path = PRICING_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"models": {}}


def estimate_cost(usage: UsageSummary, pricing: dict[str, Any]) -> float | None:
    """모델별 토큰 x 단가(USD per 1M tokens). 쓰인 모델 중 단가가 비어 있는 것이 하나라도 있으면 None."""
    total = 0.0
    for model, used in usage.by_model.items():
        price = pricing.get("models", {}).get(model) or {}
        if price.get("input") is None or price.get("output") is None:
            return None
        total += used.input_tokens / 1e6 * price["input"] + used.output_tokens / 1e6 * price["output"]
    return total


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def git_info() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {"commit": _git("rev-parse", "HEAD"), "dirty": None if status is None else bool(status)}


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in _PACKAGES:
        try:
            versions[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def data_hashes(split: str, settings: Settings) -> dict[str, str]:
    data_dir = settings.data_dir
    files = {
        "change_policy.md": data_dir / "rules/change_policy.md",
        "rules.json": data_dir / "rules/rules.json",
        "cmdb.json": data_dir / "data/cmdb.json",
        "change_calendar.json": data_dir / "data/change_calendar.json",
        "open_incidents.json": data_dir / "data/open_incidents.json",
        f"requests_{split}.json": data_dir / f"eval/requests_{split}.json",
    }
    return {name: file_sha256(path) for name, path in files.items()}


# ---------------------------------------------------------------------------
# 요청 1건 실행
# ---------------------------------------------------------------------------


@dataclass
class SingleRun:
    prediction: Prediction
    latency_s: float
    usage: UsageSummary
    counters: dict[str, int]
    result: dict[str, Any] | None = None


def initial_state(item: LabeledRequest, masked_text: str) -> dict[str, Any]:
    """그래프 입력. 마스킹한 본문만 넣고 raw_text는 넣지 않는다 (추적이 켜지면 상태가 LangSmith로 간다)."""
    return {
        "request_id": item.request_id,
        "submitted_at": item.submitted_at.astimezone(KST).isoformat(),
        "requester": item.requester,
        "masked_text": masked_text,
    }


async def run_once(
    graph,
    item: LabeledRequest,
    masked_text: str,
    *,
    thread_id: str,
    timeout: float,
    run_metadata: dict[str, Any] | None = None,
) -> SingleRun:
    """요청 1건을 그래프로 실행한다. 예외는 타입 이름만 기록하고 올리지 않는다."""
    collector = UsageCollector()
    config = {
        "callbacks": [collector],
        "configurable": {"thread_id": thread_id},
        "run_name": item.request_id,
        "metadata": run_metadata or {},
    }
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(graph.ainvoke(initial_state(item, masked_text), config), timeout)
    except Exception as exc:  # 측정 도구라서 어떤 실패든 오류 기록으로 바꾼다
        return SingleRun(
            # 진단 문장은 판정을 못 만든 이유를 찾으려는 것이다. 어디에 남길지는 build_record가 split으로 정한다.
            prediction=Prediction(
                error=type(exc).__name__,
                error_detail=exc.diagnosis() if isinstance(exc, ReviewIncomplete) else None,
            ),
            latency_s=time.perf_counter() - started,
            usage=collector.snapshot(),
            counters={},
        )
    latency = time.perf_counter() - started
    return SingleRun(
        # 이력 검색 결과(retrieved_history)는 6단계에서 history 상태가 생기면 여기서 읽는다.
        prediction=Prediction(structured=result.get("structured"), recommendation=result.get("recommendation")),
        latency_s=latency,
        usage=collector.snapshot(),
        counters=dict(result.get("metrics") or {}),
        result=result,
    )


# ---------------------------------------------------------------------------
# 기록
# ---------------------------------------------------------------------------


def build_record(split: str, case: CaseScore, run: SingleRun, judge: JudgeScore | None) -> dict[str, Any]:
    """jsonl 한 줄. eval이면 본문과 자유 서술(설명, 근거, Judge 근거)을 빼고 ID와 점수만 둔다."""
    record: dict[str, Any] = {
        "request_id": case.request_id,
        "rep": case.rep,
        "error": case.error,
        "verdict_gold": case.verdict_gold,
        "verdict_pred": case.verdict_pred,
        "verdict_correct": case.verdict_correct,
        "violations_gold": case.violations_gold,
        "violations_pred": case.violations_pred,
        "hallucinated": case.hallucinated,
        "miscited": case.miscited,
        "structured_fields": case.structured_fields,
        "risk_correct": case.risk_correct,
        "emergency_correct": case.emergency_correct,
        "self_contradiction": case.self_contradiction,
        "judge_score": case.judge_score,
        "latency_s": round(run.latency_s, 3),
        "llm_calls": run.usage.llm_calls,
        "input_tokens": run.usage.input_tokens,
        "output_tokens": run.usage.output_tokens,
        "counters": run.counters,
    }
    if split == "dev":
        rec = run.prediction.recommendation
        record["detail"] = {
            "structured": run.prediction.structured.model_dump(mode="json") if run.prediction.structured else None,
            "recommendation": rec.model_dump(mode="json") if rec else None,
            "judge_reason": judge.reason if judge else None,
            # 판정을 못 만든 이유 (N1). 대상 값, 작업 구간처럼 본문에서 온 값이 들어 있어 dev에만 남긴다.
            "error_detail": run.prediction.error_detail,
        }
    return record


def console_run_line(split: str, index: int, total: int, case: CaseScore, run: SingleRun) -> str:
    """진행 한 줄. eval은 번호만 낸다 (정답과 예측 판정은 오답 상세에 해당한다)."""
    if split != "dev":
        return f"진행 {index}/{total}"
    mark = "O" if case.verdict_correct else "X"
    extra = f" error={case.error}" if case.error else ""
    if run.prediction.error_detail:
        extra += f" ({run.prediction.error_detail})"
    return (
        f"[{index}/{total}] {case.request_id} rep{case.rep} 판정 {mark} (정답 {case.verdict_gold}, 예측 {case.verdict_pred}) "
        f"위반 정답={','.join(case.violations_gold) or '-'} 예측={','.join(case.violations_pred) or '-'} "
        f"{run.latency_s:.1f}s{extra}"
    )


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


async def _warm_up(graph, item: LabeledRequest, timeout: float) -> str | None:
    """첫 요청 1건을 추적 없이 한 번 실행하고 버린다. 모델 로드와 정적 접두사 캐시를 함께 데운다.

    출력 형식 실패(ValueError)와 판정을 만들지 못한 경우(ReviewIncomplete, 예: 모델이 대상을 못 뽑음)는 측정할 시스템
    행동이라 넘어간다. 그 밖의 실패(연결, 모델 없음, 인증, 시간 초과)는 설정이 잘못된 것이라 멈춘다.
    결과는 기록하지도 출력하지도 않는다.
    """
    state = initial_state(item, mask_secrets(item.text))
    config = {"configurable": {"thread_id": "warmup"}}
    try:
        with tracing(False):
            await asyncio.wait_for(graph.ainvoke(state, config), timeout)
    except (ValueError, ReviewIncomplete) as exc:
        return type(exc).__name__
    except Exception as exc:
        raise RunConfigError(f"warm-up 실행이 실패했다 ({type(exc).__name__}). 모델과 서버 설정을 확인한다.") from None
    return None


def _latency_summary(latencies: list[float]) -> dict[str, Any]:
    return {
        "n": len(latencies),
        "mean_s": sum(latencies) / len(latencies) if latencies else None,
        "p95_s": percentile_nearest_rank(latencies, 0.95),
        "max_s": max(latencies) if latencies else None,
    }


async def run_evaluation(
    cfg: RunConfig,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(KST),
    out: Callable[[str], None] = print,
    mcp_factory: Callable[[], MCPClient] = MCPClient,
) -> EvalOutcome:
    """v0은 그래프만 만든다. v1 이후는 MCP 서버를 한 번 띄워 모든 요청이 재사용하고 끝나면 닫는다.

    서버 기동 시간은 요청 지연에 들어가지 않는다. 서비스에서는 lifespan이 한 번 만들어 두는 것과 같은 조건이다.
    테스트는 mcp_factory로 메모리 내 서버를 넘긴다.
    """
    settings = apply_run_env(cfg.version, cfg.provider)
    check_provider_models(settings)
    if cfg.langsmith and not api_key_present():
        raise RunConfigError("--langsmith는 LANGCHAIN_API_KEY(또는 LANGSMITH_API_KEY)가 필요하다.")
    if cfg.limit is not None and cfg.split != "dev":
        raise RunConfigError("--limit은 dev에서만 쓴다 (eval은 20건 전체를 한 번에 측정한다).")
    if cfg.reps < 1:
        raise RunConfigError("--reps는 1 이상이어야 한다.")

    mcp = mcp_factory() if cfg.version != "v0" else None
    # 클라이언트는 아직 시작하지 않았다. 지원하지 않는 버전은 서버 프로세스를 띄우기 전에 여기서 걸린다.
    graph = build_graph(cfg.version, mcp=mcp)
    if mcp is not None:
        try:
            await mcp.start()
        except Exception as exc:
            raise RunConfigError(f"MCP 서버를 시작하지 못했다 ({type(exc).__name__}).") from None
    try:
        return await _run_with_graph(cfg, graph, settings, now, out)
    finally:
        if mcp is not None:
            await mcp.stop()


async def _run_with_graph(
    cfg: RunConfig, graph, settings: Settings, now: Callable[[], datetime], out: Callable[[str], None]
) -> EvalOutcome:
    items = load_split(cfg.split)  # type: ignore[arg-type]
    if cfg.limit is not None:
        items = items[: cfg.limit]
    if not items:
        raise RunConfigError(f"{cfg.split}에 실행할 요청이 없다.")
    models = {role: settings.model_for(role) for role in ROLES}
    out(f"버전={cfg.version} split={cfg.split} reps={cfg.reps} provider={cfg.provider} 모델={models}")

    started = now()
    project = project_name(cfg.version, cfg.split, started) if cfg.langsmith else None
    stem = f"{started:%Y%m%d-%H%M%S}_{cfg.version}_{cfg.split}"
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = cfg.results_dir / f"{stem}.jsonl"
    summary_path = cfg.results_dir / f"{stem}.summary.json"

    warmup_error = await _warm_up(graph, items[0], cfg.timeout)
    out("warm-up 완료")

    valid_ids = policy_rule_ids()
    rag_enabled = cfg.version == "v2"
    git = git_info()
    judge_collector = UsageCollector()
    system_usage = UsageSummary()
    counters: dict[str, int] = {}
    cases: list[CaseScore] = []
    latencies: list[float] = []
    judge_errors = 0
    truncated_runs = 0
    total = len(items) * cfg.reps
    done = 0

    with tracing(cfg.langsmith, project, {"version": cfg.version, "split": cfg.split, "commit": git["commit"]}):
        with jsonl_path.open("w", encoding="utf-8") as sink:
            for rep in range(cfg.reps):
                for item in items:
                    done += 1
                    masked = mask_secrets(item.text)
                    run = await run_once(
                        graph,
                        item,
                        masked,
                        thread_id=f"{stem}-{item.request_id}-{rep}",
                        timeout=cfg.timeout,
                        run_metadata={"request_id": item.request_id, "rep": rep, "version": cfg.version},
                    )
                    case = score_case(item, run.prediction, rep=rep, rag_enabled=rag_enabled, valid_rule_ids=valid_ids)

                    judge: JudgeScore | None = None
                    if cfg.judge and case.needs_alternative:
                        rec = run.prediction.recommendation
                        alternative = rec.conditional_alternative if rec else None
                        try:
                            with tracing(False):
                                judge = await judge_alternative(
                                    item, alternative, masked, config={"callbacks": [judge_collector]}, settings=settings
                                )
                        except Exception:  # Judge 실패는 점수 없음으로 두고 건수만 센다
                            judge_errors += 1
                        if judge is not None:
                            case = case.model_copy(update={"judge_score": judge.score})

                    system_usage.add(run.usage)
                    for key, value in run.counters.items():
                        counters[key] = counters.get(key, 0) + value
                    if cfg.provider == "ollama" and run.usage.max_input_tokens >= settings.ollama_num_ctx - TRUNCATION_MARGIN:
                        truncated_runs += 1
                    cases.append(case)
                    latencies.append(run.latency_s)
                    sink.write(json.dumps(build_record(cfg.split, case, run, judge), ensure_ascii=False) + "\n")
                    sink.flush()
                    out(console_run_line(cfg.split, done, total, case, run))

    metrics = aggregate(cases, cfg.reps)
    n_runs = len(cases)
    pricing = load_pricing()
    cost = estimate_cost(system_usage, pricing)
    judge_usage = judge_collector.snapshot()
    summary = {
        "meta": {
            "version": cfg.version,
            "split": cfg.split,
            "provider": cfg.provider,
            "reps": cfg.reps,
            "n_requests": len(items),
            "started_at": started.isoformat(),
            "finished_at": now().isoformat(),
            "git": git,
            "models": {
                "configured": models,
                "observed": sorted(system_usage.by_model),
                "judge_observed": sorted(judge_usage.by_model),
            },
            "prompt_sha256": {**prompt_hashes(cfg.version), "judge_prompt.md": judge_prompt_hash()},
            "data_sha256": data_hashes(cfg.split, settings),
            "packages": package_versions(),
            "ollama_num_ctx": settings.ollama_num_ctx if cfg.provider == "ollama" else None,
            # 서버 쪽 값은 알 수 없고, 이 실행의 환경 변수에 있으면 남긴다. 1이면 병렬 LLM 호출이 줄을 선다.
            "ollama_num_parallel": os.environ.get("OLLAMA_NUM_PARALLEL") if cfg.provider == "ollama" else None,
            "timeout_s": cfg.timeout,
            "warmup": "first_request",
            "warmup_error": warmup_error,
            "langsmith": {"enabled": cfg.langsmith, "project": project},
            "judge": {
                "enabled": cfg.judge,
                "model": models["judge"],
                "independent": judge_independent(settings),
            },
        },
        "metrics": metrics.model_dump(),
        "latency": _latency_summary(latencies),
        "usage": {
            "llm_calls_total": system_usage.llm_calls,
            "llm_calls_per_run": system_usage.llm_calls / n_runs if n_runs else None,
            "llm_errors": system_usage.llm_errors,
            "input_tokens_total": system_usage.input_tokens,
            "output_tokens_total": system_usage.output_tokens,
            "reasoning_tokens_total": system_usage.reasoning_tokens,
            "input_tokens_per_run": system_usage.input_tokens / n_runs if n_runs else None,
            "output_tokens_per_run": system_usage.output_tokens / n_runs if n_runs else None,
            "tokens_per_run": (system_usage.input_tokens + system_usage.output_tokens) / n_runs if n_runs else None,
            "by_model": {model: used.model_dump() for model, used in system_usage.by_model.items()},
        },
        "cost": {
            "unit": pricing.get("unit"),
            "total_usd": cost,
            "per_run_usd": None if cost is None or not n_runs else cost / n_runs,
        },
        "judge_usage": {
            "llm_calls": judge_usage.llm_calls,
            "input_tokens": judge_usage.input_tokens,
            "output_tokens": judge_usage.output_tokens,
            "errors": judge_errors,
        },
        "counters": counters,
        "flags": {"possible_truncation_runs": truncated_runs},
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return EvalOutcome(summary=summary, jsonl_path=jsonl_path, summary_path=summary_path)


# ---------------------------------------------------------------------------
# 터미널 요약
# ---------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def format_summary(summary: dict[str, Any]) -> str:
    """집계표. eval에서도 그대로 출력할 수 있게 요청별 정보는 담지 않는다."""
    meta, m, lat, use = summary["meta"], summary["metrics"], summary["latency"], summary["usage"]
    cost = summary["cost"]["per_run_usd"]
    rows = [
        ("판정 정확도", _pct(m["verdict_accuracy"])),
        ("위반 정밀도 / 재현율 / F1", f"{_pct(m['violation_precision'])} / {_pct(m['violation_recall'])} / {_pct(m['violation_f1'])}"),
        ("구조화 정확도", _pct(m["structured_accuracy"])),
        ("환각 / 오인용 / 오인용(엄격)", f"{m['hallucinated_count']} / {m['miscited_count']} / {m['miscited_strict_count']}"),
        ("위험도 일치", f"{_pct(m['risk_accuracy'])} (n={m['risk_n']})"),
        ("긴급 판정", f"{_pct(m['emergency_accuracy'])} (n={m['emergency_n']})"),
        ("판정-위반 자기모순", f"{_pct(m['self_contradiction_rate'])} ({m['self_contradiction_count']}/{m['self_contradiction_n']})"),
        ("RAG 재현율@3", _pct(m["rag_recall_at_3"])),
        ("일관성", _pct(m["consistency"])),
        ("대안 품질 (1~5)", f"{_num(m['alternative_score'], 2)} (n={m['alternative_n']})"),
        ("지연 평균 / p95 (초)", f"{_num(lat['mean_s'])} / {_num(lat['p95_s'])}"),
        ("토큰/실행 (입력 + 출력)", f"{_num(use['input_tokens_per_run'], 0)} + {_num(use['output_tokens_per_run'], 0)}"),
        ("LLM 호출/실행", _num(use["llm_calls_per_run"], 2)),
        ("예상 비용/실행 (USD)", "-" if cost is None else f"{cost:.5f}"),
        ("오류 실행", f"{m['n_errors']}/{m['n_runs']}"),
    ]
    judge = meta["judge"]
    notes = []
    if judge["enabled"] and not judge["independent"]:
        notes.append("Judge가 시스템과 같은 모델이라 대안 품질은 비독립 평가다.")
    if summary["flags"]["possible_truncation_runs"]:
        notes.append(f"입력이 컨텍스트 한계에 가까운 실행 {summary['flags']['possible_truncation_runs']}건 (잘림 의심).")
    lines = [f"== {meta['version']} / {meta['split']} / {meta['provider']} (요청 {meta['n_requests']}건 x {meta['reps']}회) =="]
    lines += [f"{name:<28} {value}" for name, value in rows]
    lines += [f"* {note}" for note in notes]
    return "\n".join(lines)
