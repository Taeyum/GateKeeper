"""③ policy_agent 서브그래프 테스트 (메모리 내 MCP 서버 + fake LLM)."""

import pytest

from app import data_loader, rules_engine
from app.agent.mcp_client import MCPClient, MCPNotFoundError, MCPTimeoutError
from app.agent.nodes import policy_agent as node
from app.agent.nodes.policy_agent import (
    PolicyNotes,
    RuleNoteItem,
    build_policy_subgraph,
    describe_check,
    make_policy_agent,
    merge_notes,
    render_policy_input,
    rule_ids_of,
)
from app.agent.results import RuleNote
from app.data_loader import load_requests
from app.llm.fake import set_fake_responses
from app.llm.usage import UsageCollector
from app.mcp_server.server import build_server
from app.schemas import ChangeRequest, PolicyCheckResult, Violation


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


@pytest.fixture
async def mcp():
    async with MCPClient(build_server(), base_delay=0.01) as client:
        yield client


def gold(request_id: str):
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def request_of(request_id: str, **overrides) -> ChangeRequest:
    data = gold(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


def state_of(request: ChangeRequest, request_id: str = "REQ-D01") -> dict:
    return {"structured": request, "submitted_at": gold(request_id).submitted_at.isoformat(), "route": "general"}


def engine_check(request: ChangeRequest, request_id: str) -> PolicyCheckResult:
    return rules_engine.check_all(request, gold(request_id).submitted_at)


def notes_for(check: PolicyCheckResult, *, summary: str = "요약") -> PolicyNotes:
    return PolicyNotes(
        items=[RuleNoteItem(rule_id=rule_id, note=f"{rule_id} 설명") for rule_id in rule_ids_of(check)], summary=summary
    )


class Recorder:
    def __init__(self, chain):
        self.chain, self.calls = chain, []

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return await self.chain.ainvoke(messages, config)


@pytest.fixture
def recorder(monkeypatch):
    holder: list[Recorder] = []
    real = node.structured_llm

    def wrapped(llm, schema, settings=None):
        holder.append(Recorder(real(llm, schema, settings)))
        return holder[-1]

    monkeypatch.setattr(node, "structured_llm", wrapped)
    return holder


async def run(mcp, request: ChangeRequest, request_id: str, responses: list):
    set_fake_responses("main", responses)
    collector = UsageCollector()
    out = await make_policy_agent(mcp)(state_of(request, request_id), {"callbacks": [collector]})
    return out, collector


# ---------------------------------------------------------------------------
# 구조
# ---------------------------------------------------------------------------


def test_컴파일된_서브그래프이고_노드_세_개를_가진다(mcp):
    graph = build_policy_subgraph(mcp)
    assert set(graph.get_graph().nodes) >= {"check_policy", "read_rules", "write_notes"}
    assert graph.name == "policy_agent"
    # 자기 상태 스키마를 가진다: 부모 GKState에 없는 내부 키(check, rule_texts)와 입출력 키
    keys = set(graph.get_input_jsonschema()["properties"])
    assert {"structured", "submitted_at", "route", "check", "rule_texts", "result"} <= keys


# ---------------------------------------------------------------------------
# 위반이 있을 때
# ---------------------------------------------------------------------------


async def test_위반_원본은_엔진_결과이고_evidence는_LLM_문장으로_바뀌지_않는다(mcp):
    request = request_of("REQ-D01")
    expected = engine_check(request, "REQ-D01")
    assert len(expected.violations) == 5
    liar = PolicyNotes(
        items=[RuleNoteItem(rule_id=v.rule_id, note="전혀 다른 문장 99:99") for v in expected.violations], summary="요약"
    )
    out, _ = await run(mcp, request, "REQ-D01", [liar])

    result = out["policy"]
    assert result.status == "ok"
    assert result.tool_output == expected.model_dump(mode="json")  # 엔진이 낸 값 그대로 (evidence 포함)
    assert [v["evidence"] for v in result.tool_output["violations"]] == [v.evidence for v in expected.violations]
    assert all(n.note == "전혀 다른 문장 99:99" for n in result.notes)  # 설명은 notes에만 간다


async def test_notes는_엔진이_낸_조항만_받고_더한_ID는_버리고_빠진_조항은_note가_없다(mcp):
    request = request_of("REQ-D01")
    check = engine_check(request, "REQ-D01")
    ids = rule_ids_of(check)
    written = PolicyNotes(
        items=[
            RuleNoteItem(rule_id=ids[1], note="두 번째 조항 설명"),
            RuleNoteItem(rule_id="CHG-99", note="지어낸 조항"),
            RuleNoteItem(rule_id=ids[0].lower(), note=" 첫 번째 조항 설명 "),
        ],
        summary="  전체 요약  ",
    )
    out, _ = await run(mcp, request, "REQ-D01", [written])
    result = out["policy"]
    assert result.notes == [RuleNote(rule_id=ids[0], note="첫 번째 조항 설명"), RuleNote(rule_id=ids[1], note="두 번째 조항 설명")]
    assert result.summary == "전체 요약"
    assert out["errors"] == [] and out["metrics"] == {}


async def test_조항_원문은_위반과_면제된_조항만_Resource로_읽는다(mcp, monkeypatch, recorder):
    request = request_of("REQ-D01")
    check = engine_check(request, "REQ-D01")
    read = []
    original = mcp.read_rule

    async def spy(rule_id, **kwargs):
        read.append(rule_id)
        return await original(rule_id, **kwargs)

    monkeypatch.setattr(mcp, "read_rule", spy)
    await run(mcp, request, "REQ-D01", [notes_for(check)])
    assert sorted(read) == sorted(rule_ids_of(check))
    system, user = recorder[0].calls[0]
    assert "규칙 엔진이 이미 위반 조항을 판정했다" in system.content
    assert "## 규정 원문" in user.content and "CHG-01 배포 금지 시간대" in user.content


async def test_유효한_긴급_변경은_면제_조항에도_설명을_쓴다(mcp):
    request = request_of("REQ-D07")
    check = engine_check(request, "REQ-D07")
    assert check.violations == [] and check.exempted and check.emergency_valid is True
    out, _ = await run(mcp, request, "REQ-D07", [notes_for(check)])
    result = out["policy"]
    assert result.tool_output["exempted"] and {n.rule_id for n in result.notes} == {v.rule_id for v in check.exempted}


# ---------------------------------------------------------------------------
# 위반도 면제도 없을 때
# ---------------------------------------------------------------------------


async def test_위반도_면제도_없으면_원문을_읽지_않고_LLM을_부르지_않는다(mcp, monkeypatch):
    request = request_of("REQ-D06")
    assert rule_ids_of(engine_check(request, "REQ-D06")) == []
    read = []

    async def spy(rule_id, **kwargs):
        read.append(rule_id)
        return ""

    monkeypatch.setattr(mcp, "read_rule", spy)
    out, collector = await run(mcp, request, "REQ-D06", [])  # main 큐가 비어 있어 불렀다면 예외가 났을 것이다
    assert read == [] and collector.snapshot().llm_calls == 0
    result = out["policy"]
    assert result.status == "ok" and result.notes == [] and result.summary == "위반 없음."
    assert out["errors"] == []


# ---------------------------------------------------------------------------
# 실패
# ---------------------------------------------------------------------------


async def test_규정_검사가_실패하면_확인_불가이고_LLM을_부르지_않는다(mcp, monkeypatch):
    async def boom(*args, **kwargs):
        raise MCPTimeoutError("시간 초과")

    monkeypatch.setattr(mcp, "check_policy", boom)
    out, collector = await run(mcp, request_of("REQ-D01"), "REQ-D01", [])
    assert out["policy"].status == "unavailable" and out["policy"].tool_output is None
    assert out["policy"].summary == "규정 검사: 확인 불가"
    assert out["errors"] == ["policy_agent: 규정 검사 실패 (MCPTimeoutError)"]
    assert out["metrics"]["partial_failures"] == 1 and collector.snapshot().llm_calls == 0


async def test_CMDB에_없는_대상도_확인_불가로_둔다(mcp):
    request = ChangeRequest(target="nope-db", change_type="deploy")
    out, _ = await run(mcp, request, "REQ-D01", [])
    assert out["policy"].status == "unavailable"
    assert out["errors"] == ["policy_agent: 규정 검사 실패 (MCPNotFoundError)"]


async def test_원문_하나를_못_읽어도_나머지로_설명을_쓰고_엔진_결과는_그대로다(mcp, monkeypatch, recorder):
    request = request_of("REQ-D01")
    check = engine_check(request, "REQ-D01")
    original = mcp.read_rule

    async def flaky(rule_id, **kwargs):
        if rule_id == "CHG-02":
            raise MCPNotFoundError("NOT_FOUND: rules://CHG-02")
        return await original(rule_id, **kwargs)

    monkeypatch.setattr(mcp, "read_rule", flaky)
    out, _ = await run(mcp, request, "REQ-D01", [notes_for(check)])
    assert out["policy"].status == "ok" and out["policy"].tool_output == check.model_dump(mode="json")
    assert out["errors"] == ["policy_agent: CHG-02 원문 읽기 실패 (MCPNotFoundError)"]
    assert out["metrics"]["partial_failures"] == 1
    user = recorder[0].calls[0][1].content
    assert "### CHG-01" in user and "### CHG-02" not in user


async def test_설명_작성이_실패하면_notes만_비고_요약은_코드가_채운다(mcp):
    request = request_of("REQ-D01")
    check = engine_check(request, "REQ-D01")
    out, _ = await run(mcp, request, "REQ-D01", [])  # main 응답 큐가 비어 있다
    result = out["policy"]
    assert result.status == "ok" and result.notes == [] and result.summary == describe_check(check)
    assert result.tool_output == check.model_dump(mode="json")
    assert out["errors"] == ["policy_agent: 설명 작성 실패 (FakeResponsesExhausted)"]
    assert out["metrics"] == {"partial_failures": 1}


async def test_설명_응답이_검증에_실패해도_엔진_결과는_그대로다(mcp):
    out, _ = await run(mcp, request_of("REQ-D01"), "REQ-D01", [{"summary": "항목 없음"}])
    assert out["policy"].status == "ok" and out["policy"].notes == []
    assert out["errors"][0].startswith("policy_agent: 설명 작성 실패")


# ---------------------------------------------------------------------------
# 래퍼와 순수 함수
# ---------------------------------------------------------------------------


async def test_래퍼는_config를_넘겨서_서브그래프_안의_LLM_호출도_집계된다(mcp):
    request = request_of("REQ-D01")
    out, collector = await run(mcp, request, "REQ-D01", [notes_for(engine_check(request, "REQ-D01"))])
    assert collector.snapshot().llm_calls == 1 and set(out) == {"policy", "errors", "metrics"}


def test_순수_함수():
    one = Violation(rule_id="CHG-01", severity="forbidden", evidence="가")
    two = Violation(rule_id="CHG-05", severity="fixable", evidence="나")
    check = PolicyCheckResult(violations=[one, one], exempted=[two], emergency_valid=True)
    assert rule_ids_of(check) == ["CHG-01", "CHG-05"]  # 중복은 뺀다
    assert describe_check(check) == "위반 2건(CHG-01, CHG-01), 유효한 긴급 변경으로 면제된 조항(CHG-05)."
    assert describe_check(PolicyCheckResult()) == "위반 없음."
    text = render_policy_input(ChangeRequest(target="payments-db", change_type="deploy"), check, {"CHG-01": "원문"})
    assert "- CHG-01 (금지): 가" in text and "- CHG-05 (보완): 나" in text and "### CHG-01\n원문" in text
    assert merge_notes(PolicyNotes(items=[RuleNoteItem(rule_id="CHG-01", note="  ")], summary=""), ["CHG-01"]) == []
    dup = PolicyNotes(items=[RuleNoteItem(rule_id="CHG-01", note="첫째"), RuleNoteItem(rule_id="CHG-01", note="둘째")], summary="")
    assert merge_notes(dup, ["CHG-01"]) == [RuleNote(rule_id="CHG-01", note="첫째")]
