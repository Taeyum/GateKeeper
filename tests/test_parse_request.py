"""① parse_request 노드 테스트 (fake LLM). 필수 항목 재시도(리뷰 H1), 작업 구간 점검(N1), 프롬프트 주입(N2, N3),
anthropic 와이어를 확인한다."""

from datetime import timedelta

import pytest

from app import data_loader
from app.agent import prompting
from app.agent.errors import ReviewIncomplete
from app.agent.nodes import parse_request as node
from app.agent.nodes.parse_request import build_messages, find_problems
from app.agent.prompting import load_prompt, render_required_items
from app.agent.wire import WireChangeRequest, change_request_schema, to_change_request
from app.data_loader import load_cmdb, load_policy_text, load_requests, load_rules
from app.llm.fake import FakeChatModel, set_fake_responses
from app.llm.usage import UsageCollector
from app.schemas import ChangeRequest


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


def item(request_id: str):
    return next(r for r in load_requests("dev") if r.request_id == request_id)


def state_of(request_id: str, **extra) -> dict:
    gold = item(request_id)
    return {
        "request_id": gold.request_id,
        "submitted_at": gold.submitted_at.isoformat(),
        "requester": gold.requester,
        "masked_text": gold.text,
        **extra,
    }


def label_of(request_id: str, **overrides) -> ChangeRequest:
    data = item(request_id).expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"})
    return ChangeRequest.model_validate({**data, **overrides})


class Recorder:
    """노드가 chain에 넘긴 메시지를 기록하고 실제 fake 체인에 위임한다."""

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


async def run(state: dict, responses: list) -> tuple[dict, UsageCollector]:
    set_fake_responses("main", responses)
    collector = UsageCollector()
    result = await node.parse_request(state, {"callbacks": [collector]})
    return result, collector


# ---------------------------------------------------------------------------
# 정상 경로
# ---------------------------------------------------------------------------


async def test_구조화_결과가_상태에_들어가고_재시도는_없다():
    expected = label_of("REQ-D06")
    result, collector = await run(state_of("REQ-D06"), [expected])
    assert result["structured"] == expected
    assert result["metrics"] == {"parse_retries": 0, "required_field_retries": 0}
    assert collector.snapshot().llm_calls == 1


async def test_dev_10건의_라벨_구조화는_모두_재시도_없이_통과한다():
    for gold in load_requests("dev"):
        result, collector = await run(state_of(gold.request_id), [label_of(gold.request_id)])
        assert collector.snapshot().llm_calls == 1, gold.request_id
        assert result["metrics"] == {"parse_retries": 0, "required_field_retries": 0}


# ---------------------------------------------------------------------------
# 필수 항목 재시도 (H1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("request_id", ["REQ-D06", "REQ-D03", "REQ-D04"])  # 배포, DB 변경, 표준 변경
async def test_작업_시각이_필수인_유형은_비어_있으면_피드백을_붙여_1회_다시_묻는다(request_id, recorder):
    first = label_of(request_id, start=None, end=None)
    fixed = label_of(request_id)
    result, collector = await run(state_of(request_id), [first, fixed])

    assert result["structured"].start == fixed.start
    assert result["metrics"] == {"parse_retries": 0, "required_field_retries": 1}
    assert collector.snapshot().llm_calls == 2
    first_call, second_call = recorder[0].calls
    assert second_call[:2] == first_call  # 같은 시스템/사용자 메시지에 피드백이 덧붙는다
    feedback = second_call[2].content
    assert "작업 시작 시각(start)" in feedback and "필수 항목" in feedback
    assert first.model_dump_json() in feedback  # 앞선 출력을 같이 보여 준다


async def test_다시_물어도_비어_있으면_그대로_진행한다_시각을_지어내지_않는다():
    empty = label_of("REQ-D06", start=None, end=None)
    result, collector = await run(state_of("REQ-D06"), [empty, empty])
    assert result["structured"].start is None and result["structured"].end is None
    assert result["metrics"]["required_field_retries"] == 1
    assert collector.snapshot().llm_calls == 2  # 세 번째 호출은 없다


async def test_방화벽은_작업_시각이_필수가_아니라_다시_묻지_않는다():
    empty = label_of("REQ-D01", start=None, end=None)
    result, collector = await run(state_of("REQ-D01"), [empty])
    assert result["structured"].start is None
    assert result["metrics"] == {"parse_retries": 0, "required_field_retries": 0}
    assert collector.snapshot().llm_calls == 1


def test_필수_항목_판단은_rules_json의_required_fields에서_읽는다():
    cmdb, rules = load_cmdb(), load_rules()
    assert "start" not in rules.required_fields["firewall"]
    for change_type in ("deploy", "db_change", "standard"):
        assert "start" in rules.required_fields[change_type]
    no_start = label_of("REQ-D06", start=None, end=None)
    assert len(find_problems(no_start, cmdb, rules)) == 1
    assert find_problems(label_of("REQ-D06"), cmdb, rules) == []
    # rules.json에서 start 조건을 빼면 재시도 대상에서도 빠진다 (코드에 유형 목록이 없다)
    relaxed = rules.model_copy(update={"required_fields": {**rules.required_fields, "deploy": ["target"]}})
    assert find_problems(no_start, cmdb, relaxed) == []


# ---------------------------------------------------------------------------
# 대상 확인
# ---------------------------------------------------------------------------


async def test_CMDB에_없는_대상은_서비스_목록을_알려_주고_다시_묻는다(recorder):
    wrong = label_of("REQ-D06", target="payment-service")
    fixed = label_of("REQ-D06")
    result, collector = await run(state_of("REQ-D06"), [wrong, fixed])
    assert result["structured"].target == fixed.target
    assert result["metrics"]["required_field_retries"] == 1 and collector.snapshot().llm_calls == 2
    assert "서비스 목록에 없다" in recorder[0].calls[1][2].content


async def test_다시_물어도_대상이_없으면_ReviewIncomplete다_판정을_만들지_않는다():
    wrong = label_of("REQ-D06", target="payment-service")
    with pytest.raises(ReviewIncomplete) as error:
        await run(state_of("REQ-D06"), [wrong, wrong])
    assert "payment-service" not in str(error.value)  # 메시지에 요청에서 온 값을 싣지 않는다
    # 값은 진단용 detail에만 있다. 평가 하네스가 dev 기록에만 남긴다 (N1)
    assert error.value.stage == "parse_request" and error.value.detail == "target='payment-service'이(가) CMDB에 없다"


async def test_재시도가_첫_결과의_대상을_망가뜨리면_첫_결과를_쓴다():
    first = label_of("REQ-D06", start=None, end=None)
    worse = label_of("REQ-D06", target="payment-service")
    result, _ = await run(state_of("REQ-D06"), [first, worse])
    assert result["structured"].target == first.target and result["structured"].start is None


async def test_재시도_응답이_검증에_실패해도_유효한_첫_결과를_쓴다():
    first = label_of("REQ-D06", start=None, end=None)
    result, collector = await run(state_of("REQ-D06"), [first, {"change_type": "deploy"}])  # target 없음
    assert result["structured"] == first
    assert result["metrics"]["required_field_retries"] == 1 and collector.snapshot().llm_calls == 2


# ---------------------------------------------------------------------------
# 작업 구간 (N1)
# 종료가 시작보다 늦지 않으면 규칙 엔진과 get_calendar가 INVALID_ARGUMENT로 거절해 판정을 만들 수 없다.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shift", [timedelta(0), timedelta(hours=11)], ids=["종료가_시작과_같다", "종료가_시작보다_앞선다"])
async def test_종료가_시작보다_늦지_않으면_코드가_계산한_기본값을_알려_주고_1회_다시_묻는다(shift, recorder):
    fixed = label_of("REQ-D01")
    broken = label_of("REQ-D01", end=fixed.start - shift)
    result, collector = await run(state_of("REQ-D01"), [broken, fixed])

    assert result["structured"] == fixed
    assert result["metrics"] == {"parse_retries": 0, "required_field_retries": 1}
    assert collector.snapshot().llm_calls == 2
    feedback = recorder[0].calls[1][2].content
    assert "종료 시각(end)은 시작 시각(start)보다 늦어야 한다" in feedback
    assert "60분 뒤인 2026-10-30T21:00:00+09:00" in feedback  # 시각 덧셈은 모델이 아니라 코드가 한다


def test_기본_작업_시간은_rules_json에서_읽고_종료가_없는_것은_문제가_아니다():
    cmdb, rules = load_cmdb(), load_rules()
    broken = label_of("REQ-D01", end=label_of("REQ-D01").start)
    assert len(find_problems(broken, cmdb, rules)) == 1
    short = rules.model_copy(update={"defaults": rules.defaults.model_copy(update={"work_duration_minutes_if_missing": 30})})
    assert "30분 뒤인 2026-10-30T20:30:00+09:00" in find_problems(broken, cmdb, short)[0]
    # 종료가 null이면 엔진이 기본 작업 시간을 붙이므로 다시 물을 이유가 없다
    assert find_problems(label_of("REQ-D01", end=None), cmdb, rules) == []


async def test_다시_물어도_구간이_틀리면_ReviewIncomplete다_값을_고쳐_쓰지_않는다():
    start = label_of("REQ-D01").start
    broken = label_of("REQ-D01", end=start)
    set_fake_responses("main", [broken, broken])
    collector = UsageCollector()
    with pytest.raises(ReviewIncomplete) as error:
        await node.parse_request(state_of("REQ-D01"), {"callbacks": [collector]})

    assert collector.snapshot().llm_calls == 2  # 세 번째 호출은 없다
    assert error.value.stage == "parse_request"
    assert error.value.detail == f"start={start.isoformat()}, end={start.isoformat()}"
    assert "2026" not in str(error.value)  # 메시지에 요청에서 온 값을 싣지 않는다


async def test_재시도가_작업_구간을_망가뜨리면_첫_결과를_쓴다():
    first = label_of("REQ-D06", start=None, end=None)
    worse = label_of("REQ-D06", end=label_of("REQ-D06").start)
    result, _ = await run(state_of("REQ-D06"), [first, worse])
    assert result["structured"] == first


# ---------------------------------------------------------------------------
# 검증 실패 재시도 (v0과 같은 정책)
# ---------------------------------------------------------------------------


async def test_검증에_실패하면_같은_입력으로_1회_재시도한다(recorder):
    fixed = label_of("REQ-D06")
    result, collector = await run(state_of("REQ-D06"), [{"change_type": "deploy"}, fixed])
    assert result["structured"] == fixed
    assert result["metrics"] == {"parse_retries": 1, "required_field_retries": 0}
    assert collector.snapshot().llm_calls == 2
    assert recorder[0].calls[0] == recorder[0].calls[1]


async def test_재시도_예산은_하나다_검증_재시도_뒤에는_필수_항목_재시도를_하지_않는다():
    empty = label_of("REQ-D06", start=None, end=None)
    result, collector = await run(state_of("REQ-D06"), [{"change_type": "deploy"}, empty])
    assert result["structured"].start is None
    assert result["metrics"] == {"parse_retries": 1, "required_field_retries": 0}
    assert collector.snapshot().llm_calls == 2


async def test_두_번_연속_검증에_실패하면_예외를_올린다():
    with pytest.raises(ValueError):
        await run(state_of("REQ-D06"), [{"change_type": "deploy"}, {"target": "payments-db"}])


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------


def test_시스템_프롬프트는_서비스_목록과_구조화_규칙을_담고_규정과_캘린더는_담지_않는다():
    system = build_messages(state_of("REQ-D06"))[0].content
    for service in load_cmdb().services:
        assert f"- {service.id}: {service.name}" in system
    assert "{work_duration_minutes}" not in system and "60분 뒤" in system  # rules.json 값으로 채운다
    assert "{services}" not in system and "{structure_rules}" not in system
    # 규정 판단은 policy_agent 몫이다. 구조화 단계에는 규정, 캘린더, 티켓을 주지 않는다.
    assert "CHG-01" not in system and "FRZ-01" not in system and "INC-2026-0412" not in system


def test_시스템_프롬프트에_유형별_필수_항목과_표준_변경_목록이_규정_원문에서_들어간다():  # N2
    system = build_messages(state_of("REQ-D02"))[0].content
    assert "- 방화벽: 대상과 포트, 출발지 IP, 허용 기한(출발지가 외부일 때), 사유" in system
    assert "- 배포: 작업 시간, 롤백 계획, 스테이징 테스트 결과" in system
    assert "- STD-01 로그 레벨 변경: 재시작 없이 로그 레벨만 바꾸는 경우. 다른 설정을 함께 바꾸거나 재시작이 필요하면 일반 변경(배포)이다" in system
    assert "근거 조항" not in system  # 구조화 단계는 위반을 판단하지 않는다


def test_필수_항목_표는_규정_원문을_바꾸면_따라_바뀐다_코드에_유형이_없다(monkeypatch):
    text = load_policy_text().replace("대상과 포트, 출발지 IP", "대상과 포트, 출발지 CIDR")
    monkeypatch.setattr(prompting, "load_policy_text", lambda: text)
    assert "- 방화벽: 대상과 포트, 출발지 CIDR," in build_messages(state_of("REQ-D02"))[0].content


def test_규정_원문에서_표를_못_찾으면_빈_프롬프트를_내지_않고_멈춘다(monkeypatch):
    monkeypatch.setattr(prompting, "load_policy_text", lambda: "# 규정\n\n## 1. 판정 기준\n\n내용\n")
    with pytest.raises(ValueError, match="변경 유형별 필수 항목"):
        render_required_items()
    monkeypatch.setattr(prompting, "load_policy_text", lambda: "# 규정\n\n## 4. 변경 유형별 필수 항목\n\n표가 없다\n")
    with pytest.raises(ValueError, match="표를 읽지 못했다"):
        render_required_items()


def test_허용_기한과_종료_시각을_구분하고_로그_레벨_변경의_빈_목록은_v1_프롬프트에만_있다():  # N2, N3
    system = build_messages(state_of("REQ-D02"))[0].content
    assert "일 수로 바꿔 expires_days에 쓴다. 작업 구간의 종료 시각(end)이 아니다" in system
    assert "extra_changes는 null이 아니라 빈 목록이다" in system
    # v0과 공유하는 구조화 규칙은 그대로다 (v0 기준선을 건드리지 않는다)
    assert "빈 목록" not in load_prompt("structure_rules.md")


def test_시스템_메시지는_요청과_무관하게_같고_사용자_메시지에_요일_붙은_요청_시각이_있다():
    assert build_messages(state_of("REQ-D01"))[0].content == build_messages(state_of("REQ-D07"))[0].content
    state = state_of("REQ-D01")
    user = build_messages(state)[1].content
    assert "2026-10-29 16:00 (목) Asia/Seoul" in user and state["requester"] in user and state["masked_text"] in user


def test_raw_text는_프롬프트에_들어가지_않는다():
    state = state_of("REQ-D01", raw_text="password=hunter2 로 접속")
    assert all("hunter2" not in str(m.content) for m in build_messages(state))


# ---------------------------------------------------------------------------
# anthropic 와이어 (5단계 필수)
# ---------------------------------------------------------------------------


def test_provider별_구조화_스키마_선택():
    assert change_request_schema("anthropic") is WireChangeRequest
    for provider in ("ollama", "openai", "fake"):
        assert change_request_schema(provider) is ChangeRequest


def test_도메인_모델은_그대로_와이어는_도메인으로_돌린다():
    domain = label_of("REQ-D01")
    assert to_change_request(domain) is domain
    wire_data = domain.model_dump()
    for name in ("source", "ticket", "reason"):
        wire_data[name] = wire_data[name] or ""
    wire_data["extra_changes"] = wire_data["extra_changes"] or []
    restored = to_change_request(WireChangeRequest.model_validate(wire_data))
    assert isinstance(restored, ChangeRequest) and restored.source is None and restored.target == domain.target


async def test_anthropic은_와이어로_받아_도메인으로_돌려놓는다(monkeypatch, recorder):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-not-a-real-key")
    data_loader.clear_caches()
    monkeypatch.setattr(node, "get_llm", lambda role, settings=None: FakeChatModel(role=role))
    seen = []
    real = node.change_request_schema
    monkeypatch.setattr(node, "change_request_schema", lambda provider: seen.append(provider) or real(provider))

    wire = label_of("REQ-D01").model_dump(mode="json")
    wire.update(source="", ticket="", reason="", extra_changes=[])  # 와이어는 모든 필드가 필수이고 빈 값은 빈 문자열
    result, _ = await run(state_of("REQ-D01"), [wire])

    assert seen == ["anthropic"]
    assert isinstance(result["structured"], ChangeRequest)
    assert result["structured"].source is None and result["structured"].extra_changes is None
    assert result["structured"].target == "payments-db"
