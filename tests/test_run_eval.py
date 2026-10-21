"""평가 하네스 테스트 (fake provider, dev와 대역 eval만 쓴다)."""

import json
from datetime import datetime

import pytest

from app import data_loader
from app.agent.mcp_client import MCPClient
from app.agent.nodes.classify import RouteGuess
from app.agent.nodes.decide import DecideDraft
from app.agent.nodes.impact_agent import ImpactNote
from app.agent.nodes.policy_agent import PolicyNotes
from app.config import get_settings
from app.data_loader import load_requests
from app.llm.fake import set_fake_responses, set_fake_responses_for
from app.llm.usage import ModelUsage, UsageSummary
from app.mcp_server.server import build_server
from app.schemas import KST, ChangeRequest
from eval.harness import (
    RunConfig,
    RunConfigError,
    check_provider_models,
    estimate_cost,
    format_summary,
    run_evaluation,
)
from eval.run_eval import main, parse_args
from tests._eval_guard import data_dir_with_stand_in_eval, real_eval_files_read, watch_reads

FIXED = datetime(2026, 10, 21, 9, 5, 7, tzinfo=KST)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # apply_run_env가 os.environ을 직접 바꾸므로 원래 값 복원을 monkeypatch에 맡긴다.
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("GK_VERSION", "v0")
    data_loader.clear_caches()


def _v0_response(item) -> dict:
    """gold를 그대로 옮긴 v0 출력 (fake 응답)."""
    expected = item.expected
    structured = expected.structured.model_dump(mode="json", exclude={"acceptable_change_types", "time_note"})
    return {
        "structured": structured,
        "recommendation": {
            "violations": [{"rule_id": r, "severity": "forbidden", "evidence": "근거"} for r in expected.violations],
            "verdict": expected.verdict,
            "risk": expected.risk,
            "emergency_valid": expected.emergency_valid,
            "post_report_required": expected.post_report_required,
            "explanation": "설명",
            "conditional_alternative": expected.conditional_alternative,
            "citations": list(expected.violations),
        },
    }


def _queue(items, *, reps=1, judge=True):
    """warm-up 1건 + 요청 x rep 순서대로 main 응답을, 대안이 필요한 건마다 judge 응답을 넣는다."""
    main_responses = [_v0_response(items[0])]
    judge_responses = []
    for _ in range(reps):
        for item in items:
            main_responses.append(_v0_response(item))
            if judge and item.expected.verdict in ("반려", "조건부 승인"):
                judge_responses.append({"score": 4, "reason": "근거"})
    set_fake_responses("main", main_responses)
    set_fake_responses("judge", judge_responses)


def _queue_v1(items, *, reps=1):
    """v1 실행에 줄 fake 응답. 구조화는 라벨 그대로, 설명과 대안은 일반적인 응답이다.

    PolicyNotes 등은 요청마다 달라야 할 이유가 없다(조항 ID가 안 맞으면 엔진 결과에 없는 note로 버려질 뿐이라
    판정과 위반은 그대로다). 모두 넉넉히 넣고 쓰이는 만큼만 꺼낸다.
    """
    runs = [items[0]] + [item for _ in range(reps) for item in items]  # warm-up 1건 + 요청 x rep
    structured = [
        ChangeRequest.model_validate(i.expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"}))
        for i in runs
    ]
    set_fake_responses_for("main", ChangeRequest, structured)
    set_fake_responses_for(
        "small", RouteGuess,
        [RouteGuess(change_type=r.change_type, is_emergency=r.is_emergency) for r in structured],
    )  # fmt: skip
    set_fake_responses_for("main", PolicyNotes, [PolicyNotes(items=[], summary="정책 요약")] * len(runs))
    set_fake_responses_for("main", ImpactNote, [ImpactNote(summary="영향도 요약")] * len(runs))
    draft = DecideDraft(explanation="설명", conditions=["조건을 충족한다"], alternative_start=None, alternative_end=None)
    set_fake_responses_for("main", DecideDraft, [draft] * (2 * len(runs)))
    judged = sum(1 for item in items if item.expected.verdict in ("반려", "조건부 승인")) * reps
    set_fake_responses("judge", [{"score": 4, "reason": "근거"}] * judged)


def _cfg(tmp_path, **kwargs) -> RunConfig:
    defaults = {"version": "v0", "split": "dev", "provider": "fake", "limit": 3, "results_dir": tmp_path}
    return RunConfig(**{**defaults, **kwargs})


async def test_v1_실행은_MCP_서버를_한_번_띄워_재사용하고_끝나면_닫는다(tmp_path):
    items = load_requests("dev")[:3]  # D01 반려, D02 승인, D03 조건부
    _queue_v1(items, reps=2)
    clients: list[MCPClient] = []

    def factory() -> MCPClient:
        clients.append(MCPClient(build_server()))
        return clients[-1]

    lines: list[str] = []
    outcome = await run_evaluation(_cfg(tmp_path, version="v1", reps=2), now=lambda: FIXED, out=lines.append, mcp_factory=factory)

    assert len(clients) == 1 and not clients[0].is_connected  # 한 번 만들어 모든 요청이 쓰고, 끝나면 닫는다
    summary, metrics = outcome.summary, outcome.summary["metrics"]
    assert summary["meta"]["version"] == "v1" and metrics["n_runs"] == 6 and metrics["n_errors"] == 0
    assert metrics["verdict_accuracy"] == 1.0 and metrics["consistency"] == 1.0
    assert metrics["self_contradiction_n"] == 6 and metrics["self_contradiction_count"] == 0
    assert set(summary["meta"]["prompt_sha256"]) >= {"parse_request.md", "classify.md", "policy_agent.md", "impact_agent.md", "decide.md"}
    assert summary["usage"]["llm_calls_per_run"] >= 4  # parse, classify, decide에 정책 설명과 영향도 요약이 더해진다
    assert "alternative_fallbacks" in summary["counters"] and "parse_retries" in summary["counters"]
    records = [json.loads(line) for line in outcome.jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 6 and all(r["self_contradiction"] is False for r in records)


async def test_v1이_판정을_못_만들어도_warm_up은_멈추지_않고_오류_실행으로_기록한다(tmp_path):
    """ReviewIncomplete(대상을 못 뽑음 등)는 측정할 시스템 행동이다. 설정 오류로 멈추면 첫 요청이 우연히 실패한 것만으로 평가가 시작되지 못한다."""
    items = load_requests("dev")[:3]
    _queue_v1(items)
    wrong = ChangeRequest(target="payment-service", change_type="deploy")  # CMDB에 없는 대상: 다시 물어도 같다
    set_fake_responses_for("main", ChangeRequest, [wrong] * 8)  # warm-up 1건 + 요청 3건, 건마다 2회 호출
    lines: list[str] = []
    outcome = await run_evaluation(
        _cfg(tmp_path, version="v1"), now=lambda: FIXED, out=lines.append, mcp_factory=lambda: MCPClient(build_server())
    )
    metrics = outcome.summary["metrics"]
    assert outcome.summary["meta"]["warmup_error"] == "ReviewIncomplete"
    assert (metrics["n_runs"], metrics["n_errors"], metrics["verdict_accuracy"]) == (3, 3, 0.0)
    assert metrics["self_contradiction_n"] == 0  # 오류 난 실행은 자기모순을 따질 수 없다
    records = [json.loads(line) for line in outcome.jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert {r["error"] for r in records} == {"ReviewIncomplete"} and {r["verdict_pred"] for r in records} == {"ERROR"}
    # dev는 판정을 못 만든 이유를 남긴다 (N1)
    assert {r["detail"]["error_detail"] for r in records} == {"parse_request: target='payment-service'이(가) CMDB에 없다"}
    assert any("error=ReviewIncomplete (parse_request: target='payment-service'" in line for line in lines)


async def test_v1_실행이_중간에_실패해도_MCP_서버는_닫힌다(tmp_path):
    clients: list[MCPClient] = []

    def factory() -> MCPClient:
        clients.append(MCPClient(build_server()))
        return clients[-1]

    # 응답을 하나도 넣지 않아 warm-up이 설정 오류로 멈춘다
    with pytest.raises(RunConfigError, match="warm-up"):
        await run_evaluation(_cfg(tmp_path, version="v1"), now=lambda: FIXED, out=lambda _: None, mcp_factory=factory)
    assert not clients[0].is_connected


async def test_v1이_아닌_버전은_MCP_서버를_띄우지_않는다(tmp_path):
    items = load_requests("dev")[:3]
    _queue(items)
    called: list[int] = []
    await run_evaluation(_cfg(tmp_path), now=lambda: FIXED, out=lambda _: None, mcp_factory=lambda: called.append(1))  # type: ignore[arg-type,return-value]
    assert called == []


async def test_지원하지_않는_버전은_서버를_띄우기_전에_거절된다(tmp_path):
    started: list[int] = []

    class Probe(MCPClient):
        async def start(self) -> None:
            started.append(1)

    with pytest.raises(ValueError):  # 알 수 없는 버전은 설정 검증(GK_VERSION)에서 걸린다
        await run_evaluation(_cfg(tmp_path, version="v9"), now=lambda: FIXED, out=lambda _: None, mcp_factory=Probe)  # type: ignore[arg-type]
    assert started == []


async def test_dev_실행이_jsonl과_summary를_만든다(tmp_path):
    items = load_requests("dev")[:3]  # D01 반려, D02 승인, D03 조건부
    _queue(items)
    lines: list[str] = []
    outcome = await run_evaluation(_cfg(tmp_path), now=lambda: FIXED, out=lines.append)

    assert outcome.jsonl_path.name == "20261021-090507_v0_dev.jsonl"
    assert outcome.summary_path.name == "20261021-090507_v0_dev.summary.json"
    records = [json.loads(line) for line in outcome.jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert [r["request_id"] for r in records] == ["REQ-D01", "REQ-D02", "REQ-D03"]
    assert all(r["verdict_correct"] and r["llm_calls"] == 1 for r in records)
    assert records[0]["detail"]["recommendation"]["verdict"] == "반려"  # dev는 상세를 남긴다
    assert json.loads(outcome.summary_path.read_text(encoding="utf-8")) == outcome.summary

    metrics = outcome.summary["metrics"]
    assert (metrics["n_requests"], metrics["n_runs"], metrics["n_errors"]) == (3, 3, 0)
    assert metrics["verdict_accuracy"] == 1.0 and metrics["violation_f1"] == 1.0
    assert metrics["alternative_n"] == 2 and metrics["alternative_score"] == 4.0  # D01, D03 (D02는 승인)
    assert any("warm-up 완료" in line for line in lines)


async def test_warmup_1건은_결과에_들어가지_않는다(tmp_path):
    items = load_requests("dev")[:2]
    _queue(items)
    outcome = await run_evaluation(_cfg(tmp_path, limit=2), now=lambda: FIXED, out=lambda _: None)
    assert outcome.summary["metrics"]["n_runs"] == 2
    assert outcome.summary["usage"]["llm_calls_total"] == 2  # warm-up 호출은 세지 않는다
    assert outcome.summary["meta"]["warmup"] == "first_request"
    assert len(outcome.jsonl_path.read_text(encoding="utf-8").splitlines()) == 2


async def test_메타데이터와_사용량_집계(tmp_path):
    items = load_requests("dev")[:2]
    _queue(items)
    summary = (await run_evaluation(_cfg(tmp_path, limit=2), now=lambda: FIXED, out=lambda _: None)).summary
    meta = summary["meta"]
    assert (meta["version"], meta["split"], meta["provider"], meta["reps"]) == ("v0", "dev", "fake", 1)
    assert set(meta["prompt_sha256"]) == {"baseline_v0.md", "structure_rules.md", "judge_prompt.md"}
    assert {"change_policy.md", "cmdb.json", "requests_dev.json"} <= set(meta["data_sha256"])
    assert meta["git"]["commit"] is None or len(meta["git"]["commit"]) == 40
    assert meta["models"]["observed"] == ["fake-main"] and meta["models"]["judge_observed"] == ["fake-judge"]
    assert meta["langsmith"] == {"enabled": False, "project": None}
    assert meta["judge"] == {"enabled": True, "model": "fake-judge", "independent": True}
    usage = summary["usage"]
    assert usage["llm_calls_per_run"] == 1.0 and usage["input_tokens_total"] > 0
    assert summary["judge_usage"]["llm_calls"] == 1  # D01만 대안이 필요 (D02는 승인)
    assert summary["cost"]["total_usd"] is None  # fake 모델은 단가표에 없다
    assert summary["counters"] == {"parse_retries": 0}
    assert summary["latency"]["n"] == 2 and summary["latency"]["p95_s"] == summary["latency"]["max_s"]


async def test_반복_실행이면_일관성을_낸다(tmp_path):
    items = load_requests("dev")[:2]
    _queue(items, reps=2)
    summary = (await run_evaluation(_cfg(tmp_path, limit=2, reps=2), now=lambda: FIXED, out=lambda _: None)).summary
    assert summary["metrics"]["n_runs"] == 4 and summary["metrics"]["consistency"] == 1.0


async def test_no_judge면_Judge를_부르지_않는다(tmp_path):
    items = load_requests("dev")[:3]
    _queue(items, judge=False)
    summary = (await run_evaluation(_cfg(tmp_path, judge=False), now=lambda: FIXED, out=lambda _: None)).summary
    assert summary["judge_usage"]["llm_calls"] == 0 and summary["metrics"]["alternative_score"] is None


async def test_실행_오류는_타입만_기록하고_다음_요청으로_간다(tmp_path):
    items = load_requests("dev")[:2]
    # warm-up과 첫 요청은 정상, 둘째 요청은 응답 큐가 비어 FakeResponsesExhausted
    set_fake_responses("main", [_v0_response(items[0]), _v0_response(items[0])])
    set_fake_responses("judge", [{"score": 5, "reason": "근거"}])
    outcome = await run_evaluation(_cfg(tmp_path, limit=2), now=lambda: FIXED, out=lambda _: None)
    records = [json.loads(line) for line in outcome.jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["error"] is None and records[1]["error"] == "FakeResponsesExhausted"
    assert records[1]["verdict_pred"] == "ERROR" and outcome.summary["metrics"]["n_errors"] == 1


async def test_warmup이_연결_같은_문제로_실패하면_멈춘다(tmp_path):
    set_fake_responses("main", [])  # 첫 호출부터 FakeResponsesExhausted(RuntimeError)
    with pytest.raises(RunConfigError, match="warm-up"):
        await run_evaluation(_cfg(tmp_path), now=lambda: FIXED, out=lambda _: None)


async def test_warmup의_출력_형식_실패는_측정_대상이라_넘어간다(tmp_path):
    items = load_requests("dev")[:2]
    set_fake_responses("main", [{"structured": {}}, {"structured": {}}, _v0_response(items[0]), _v0_response(items[1])])
    set_fake_responses("judge", [{"score": 3, "reason": "근거"}])
    outcome = await run_evaluation(_cfg(tmp_path, limit=2), now=lambda: FIXED, out=lambda _: None)
    assert outcome.summary["meta"]["warmup_error"] == "ValidationError"
    assert outcome.summary["metrics"]["n_errors"] == 0


# ---------------------------------------------------------------------------
# 실행 전 점검 (I3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "env", "fragment"),
    [
        ("ollama", {"MODEL_MAIN": "claude-sonnet-5-5"}, "main=claude-sonnet-5-5"),
        ("ollama", {"MODEL_JUDGE": "gpt-4o"}, "judge=gpt-4o"),
        ("anthropic", {"MODEL_MAIN": "qwen3:8b"}, "main=qwen3:8b"),
        ("openai", {"MODEL_SMALL": "qwen3:0.6b"}, "small=qwen3:0.6b"),
    ],
)
def test_provider와_모델_이름이_안_맞으면_멈춘다(make_settings, provider, env, fragment):
    overrides = {key.lower(): value for key, value in env.items()}
    with pytest.raises(RunConfigError, match="비우면 provider 기본값") as caught:
        check_provider_models(make_settings(llm_provider=provider, **overrides))
    assert fragment in str(caught.value)


@pytest.mark.parametrize("provider", ["fake", "ollama", "anthropic", "openai"])
def test_provider_기본_모델은_통과한다(make_settings, provider):
    check_provider_models(make_settings(llm_provider=provider))


async def test_맞지_않는_모델이면_LLM을_부르기_전에_멈춘다(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_MAIN", "claude-sonnet-5-5")
    cfg = _cfg(tmp_path, provider="ollama")
    with pytest.raises(RunConfigError, match="ollama인데 다른 provider의 모델 이름"):
        await run_evaluation(cfg, now=lambda: FIXED, out=lambda _: None)
    assert list(tmp_path.iterdir()) == []  # 결과 파일도 만들지 않았다


async def test_langsmith는_키가_없으면_멈춘다(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    with pytest.raises(RunConfigError, match="LANGCHAIN_API_KEY"):
        await run_evaluation(_cfg(tmp_path, langsmith=True), now=lambda: FIXED, out=lambda _: None)


async def test_langsmith_없이는_env가_켜져_있어도_추적을_끈다(tmp_path, monkeypatch):
    from langchain_core.tracers.context import _tracing_v2_is_enabled

    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    items = load_requests("dev")[:1]
    _queue(items)
    seen: list[bool] = []
    import eval.harness as harness

    real = harness.run_once

    async def spy(*args, **kwargs):
        seen.append(_tracing_v2_is_enabled())
        return await real(*args, **kwargs)

    monkeypatch.setattr(harness, "run_once", spy)
    await run_evaluation(_cfg(tmp_path, limit=1), now=lambda: FIXED, out=lambda _: None)
    assert seen == [False]


async def test_langsmith를_주면_측정_중_추적이_켜지고_프로젝트_이름이_남는다(tmp_path, monkeypatch):
    from langchain_core.tracers.context import _tracing_v2_is_enabled

    monkeypatch.setenv("LANGCHAIN_API_KEY", "dummy-key")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    items = load_requests("dev")[:1]
    _queue(items)
    seen: list[bool] = []
    import eval.harness as harness

    real = harness.run_once

    async def spy(*args, **kwargs):
        seen.append(_tracing_v2_is_enabled())
        # 실제 전송은 하지 않고 켜졌는지만 본다 (키가 가짜라 전송하면 401이 난다)
        return await real(*args, **kwargs)

    monkeypatch.setattr(harness, "run_once", spy)
    monkeypatch.setattr(harness, "tracing", _no_send_tracing(harness.tracing))
    summary = (await run_evaluation(_cfg(tmp_path, limit=1, langsmith=True), now=lambda: FIXED, out=lambda _: None)).summary
    assert summary["meta"]["langsmith"] == {"enabled": True, "project": "gk-v0-dev-20261021-090507"}
    assert seen == [True]


def _no_send_tracing(real_tracing):
    """추적 컨텍스트는 그대로 쓰되 전송 클라이언트만 막는다."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    @contextmanager
    def wrapped(enabled, project=None, metadata=None):
        from langsmith import tracing_context

        if enabled:
            with tracing_context(enabled=True, project_name=project, metadata=metadata, client=MagicMock()):
                yield
        else:
            with real_tracing(False):
                yield

    return wrapped


# ---------------------------------------------------------------------------
# CLI, eval 출력 정책
# ---------------------------------------------------------------------------


def test_CLI_인자():
    args = parse_args(["--version", "v0", "--split", "dev", "--reps", "3", "--provider", "ollama", "--langsmith"])
    assert (args.version, args.split, args.reps, args.provider, args.langsmith, args.no_judge) == (
        "v0", "dev", 3, "ollama", True, False,
    )  # fmt: skip


def test_eval에_limit을_주면_파일을_열기_전에_거절한다(tmp_path, monkeypatch, capsys):
    opened = watch_reads(monkeypatch)
    code = main(["--version", "v0", "--split", "eval", "--limit", "2", "--provider", "fake", "--results-dir", str(tmp_path)])
    assert code == 2 and "--limit은 dev에서만" in capsys.readouterr().err
    assert not any(path.name in ("requests_eval.json", "label_review.md") for path in opened)


async def test_eval_실행은_본문과_상세를_내지_않고_실제_정답_파일은_열지_않는다(tmp_path, monkeypatch):
    source = get_settings().data_dir
    stand_in = data_dir_with_stand_in_eval(source, tmp_path / "data")
    monkeypatch.setenv("GK_DATA_DIR", str(stand_in))
    data_loader.clear_caches()
    items = load_requests("eval", allow_eval=True)
    _queue(items)
    opened = watch_reads(monkeypatch)
    lines: list[str] = []
    cfg = _cfg(tmp_path / "out", split="eval", limit=None)
    outcome = await run_evaluation(cfg, now=lambda: FIXED, out=lines.append)

    assert real_eval_files_read(opened, source) == []
    printed = "\n".join(lines) + "\n" + format_summary(outcome.summary)
    jsonl = outcome.jsonl_path.read_text(encoding="utf-8")
    for item in items:
        assert item.text not in printed and item.text not in jsonl
        assert item.expected.rationale not in printed and item.expected.rationale not in jsonl
    assert "REQ-E01" not in printed  # 요청 ID와 정오답도 터미널에 내지 않는다 (집계만)
    assert all("detail" not in json.loads(line) for line in jsonl.splitlines())
    assert "설명" not in jsonl and "근거" not in jsonl  # explanation, evidence, Judge 근거
    summary_text = json.dumps(outcome.summary, ensure_ascii=False)
    assert "REQ-E" not in summary_text  # summary에 요청별 목록이 없다
    assert outcome.summary["metrics"]["n_requests"] == 10


async def test_eval_실행은_판정을_못_만든_이유도_내지_않고_예외_타입만_남긴다(tmp_path, monkeypatch):
    """N1의 진단 정보에는 대상 값과 작업 구간이 들어 있어 dev에서만 남긴다 (절대 규칙 1)."""
    source = get_settings().data_dir
    stand_in = data_dir_with_stand_in_eval(source, tmp_path / "data")
    monkeypatch.setenv("GK_DATA_DIR", str(stand_in))
    data_loader.clear_caches()
    items = load_requests("eval", allow_eval=True)
    _queue_v1(items)
    wrong = ChangeRequest(target="payment-service", change_type="deploy")  # 다시 물어도 같다
    set_fake_responses_for("main", ChangeRequest, [wrong] * (2 * (len(items) + 1)))  # warm-up 1건 + 요청 10건
    opened = watch_reads(monkeypatch)
    lines: list[str] = []
    outcome = await run_evaluation(
        _cfg(tmp_path / "out", version="v1", split="eval", limit=None),
        now=lambda: FIXED,
        out=lines.append,
        mcp_factory=lambda: MCPClient(build_server()),
    )

    assert real_eval_files_read(opened, source) == []
    jsonl = outcome.jsonl_path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in jsonl.splitlines()]
    assert {r["error"] for r in records} == {"ReviewIncomplete"}  # 타입 이름만
    printed = "\n".join(lines) + "\n" + format_summary(outcome.summary) + jsonl
    for leaked in ("payment-service", "CMDB에 없다", "parse_request", "error_detail"):
        assert leaked not in printed, leaked
    assert all("detail" not in r for r in records)


# ---------------------------------------------------------------------------
# 비용, 터미널 요약
# ---------------------------------------------------------------------------


def test_비용은_모델별_토큰_x_단가이고_단가가_비면_None이다():
    usage = UsageSummary(by_model={"a": ModelUsage(input_tokens=2_000_000, output_tokens=500_000)})
    pricing = {"models": {"a": {"input": 3.0, "output": 15.0}}}
    assert estimate_cost(usage, pricing) == pytest.approx(2 * 3.0 + 0.5 * 15.0)
    assert estimate_cost(usage, {"models": {"a": {"input": 3.0, "output": None}}}) is None
    assert estimate_cost(usage, {"models": {}}) is None
    free = {"models": {"a": {"input": 0, "output": 0}}}
    assert estimate_cost(usage, free) == 0.0
    two = UsageSummary(by_model={"a": ModelUsage(input_tokens=1_000_000), "b": ModelUsage(output_tokens=1_000_000)})
    assert estimate_cost(two, {"models": {"a": {"input": 1, "output": 1}}}) is None  # 한 모델이라도 비면 None
    assert estimate_cost(UsageSummary(), pricing) == 0.0


async def test_터미널_요약표(tmp_path):
    items = load_requests("dev")[:3]
    _queue(items)
    summary = (await run_evaluation(_cfg(tmp_path), now=lambda: FIXED, out=lambda _: None)).summary
    text = format_summary(summary)
    assert "판정 정확도" in text and "100.0%" in text and "일관성" in text
    assert "RAG 재현율@3" in text and "예상 비용/실행 (USD)" in text
    assert "판정-위반 자기모순" in text and "0.0% (0/3)" in text  # 라벨 그대로 낸 응답이라 모순이 없다
    assert "비독립" not in text  # fake의 judge 모델은 main과 다르다
