"""Anthropic 와이어 스키마 테스트 (리뷰 J2).

공식 한도는 요청 전체 기준 선택 필드 24개, union(anyOf, nullable) 필드 16개이지만, ChangeRequest 단독(16/14)도
실제로는 거절된다 (app/agent/wire.py). 와이어는 선택 필드 0개로 만들어 통과한 것만 쓴다.
"""

from datetime import date, datetime

import pytest

from app import data_loader, rules_engine
from app.agent.graph import build_graph
from app.agent.nodes import baseline_v0
from app.agent.nodes.classify import RouteGuess
from app.agent.nodes.decide import DecideDraft
from app.agent.nodes.impact_agent import ImpactNote
from app.agent.nodes.policy_agent import PolicyNotes
from app.agent.nodes.baseline_v0 import V0Output, output_schema, to_output
from app.agent.wire import V0Wire, WireChangeRequest, WireRecommendation
from app.data_loader import load_requests
from app.llm.fake import set_fake_responses
from app.schemas import KST, ChangeRequest, Recommendation

MAX_OPTIONAL, MAX_UNION = 24, 16


def complexity(schema: dict) -> tuple[int, int]:
    """(선택 필드 수, union 필드 수). $ref는 펼쳐서 센다."""
    defs = schema.get("$defs", {})

    def walk(node: dict) -> tuple[int, int]:
        if "$ref" in node:
            node = defs[node["$ref"].split("/")[-1]]
        optional = union = 0
        required = set(node.get("required", []))
        for name, sub in node.get("properties", {}).items():
            optional += name not in required
            union += "anyOf" in sub or isinstance(sub.get("type"), list)
            o, u = walk(sub)
            optional, union = optional + o, union + u
        for child in [node["items"]] if "items" in node else []:
            o, u = walk(child)
            optional, union = optional + o, union + u
        for branch in node.get("anyOf", []):
            o, u = walk(branch)
            optional, union = optional + o, union + u
        return optional, union

    return walk(schema)


def test_도메인_V0Output은_한도를_넘는다_그래서_와이어가_있다():
    optional, union = complexity(V0Output.model_json_schema())
    assert optional > MAX_OPTIONAL and union > MAX_UNION  # 25/24, 18/16 (실제 API가 400으로 거절)


def test_와이어_스키마는_선택_필드가_없고_union도_한도_안이다():
    optional, union = complexity(V0Wire.model_json_schema())
    assert optional == 0 and union == 12 and union <= MAX_UNION
    # v1 parse_request가 anthropic에서 쓸 단독 스키마
    assert complexity(WireChangeRequest.model_json_schema()) == (0, 10)


@pytest.mark.parametrize(
    ("schema", "unions"), [(RouteGuess, 0), (PolicyNotes, 0), (ImpactNote, 0), (DecideDraft, 2)]
)
def test_v1_노드의_LLM_출력_스키마는_선택_필드가_없다(schema, unions):
    """anthropic 구조화 출력은 선택 필드가 문법 상태 공간을 키운다. v1 스키마는 처음부터 필수 필드만 둔다 (선택 0)."""
    assert complexity(schema.model_json_schema()) == (0, unions)  # DecideDraft의 union 2개는 nullable 시각 둘


def test_도메인_ChangeRequest는_공식_한도_안이지만_실제로는_거절된_스키마다():
    """ChangeRequest 단독 16/14는 공식 한도(24/16) 안인데도 API가 'Schema is too complex'로 거절했다 (실측).
    그래서 anthropic에서는 이 스키마를 직접 보내지 않고 WireChangeRequest를 쓴다. 이 값이 바뀌면 와이어 근거를 다시 본다."""
    assert complexity(ChangeRequest.model_json_schema()) == (16, 14)
    assert complexity(Recommendation.model_json_schema()) == (9, 4)  # 이쪽은 통과했다


def test_와이어_필드가_도메인과_같다():
    # extra_changes_known은 와이어 전용이다 (빈 목록과 null을 구분하려는 필수 bool, 도메인에는 없다)
    assert set(WireChangeRequest.model_fields) - {"extra_changes_known"} == set(ChangeRequest.model_fields)
    # v0은 이력을 받지 않으므로 similar_history만 와이어에서 뺀다
    assert set(WireRecommendation.model_fields) == set(Recommendation.model_fields) - {"similar_history"}
    assert set(V0Wire.model_fields) == set(V0Output.model_fields)


def _wire_request(**overrides) -> WireChangeRequest:
    data = {
        "target": "payments-db", "change_type": "firewall", "standard_change_id": None,
        "start": datetime(2026, 10, 30, 20, 0, tzinfo=KST), "end": datetime(2026, 10, 30, 21, 0, tzinfo=KST),
        "time_precision": "exact", "source": "", "port": None, "expires_days": None, "expires_at": None,
        "has_rollback": None, "has_test_evidence": None, "is_emergency": False, "ticket": "", "reason": "",
        "restart_required": None, "extra_changes_known": False, "extra_changes": [], "cert_expires_at": None,
    }  # fmt: skip
    return WireChangeRequest.model_validate({**data, **overrides})


def test_빈_문자열은_None으로_바뀌고_extra_changes는_알_수_없으면_None이다():
    domain = _wire_request(source="  ", ticket="", reason=" ", extra_changes=[]).to_domain()
    assert (domain.source, domain.ticket, domain.reason, domain.extra_changes) == (None, None, None, None)


def test_extra_changes는_알_수_있으면_빈_목록도_그대로_남고_모르면_None이다():
    """엔진은 빈 목록이면 STD-01을 확정하고 None이면 확정하지 않는다(fail-safe). 둘을 섞으면 표준 변경이 항상 배포로 간다."""
    assert _wire_request(extra_changes_known=True, extra_changes=[]).to_domain().extra_changes == []
    assert _wire_request(extra_changes_known=True, extra_changes=["커넥션 타임아웃"]).to_domain().extra_changes == [
        "커넥션 타임아웃"
    ]
    assert _wire_request(extra_changes_known=False, extra_changes=[]).to_domain().extra_changes is None
    assert _wire_request(extra_changes_known=False, extra_changes=["무시된다"]).to_domain().extra_changes is None


def test_extra_changes_known은_필수라_빠뜨리면_검증에_실패한다():
    data = _wire_request().model_dump()
    del data["extra_changes_known"]
    with pytest.raises(ValueError, match="extra_changes_known"):
        WireChangeRequest.model_validate(data)


def test_값이_있으면_그대로_남고_세_값_필드는_null과_false를_구분한다():
    domain = _wire_request(
        source=" 203.0.113.10/32 ", ticket="INC-2026-0412", reason="장애 대응", extra_changes_known=True,
        extra_changes=["로그 레벨"], has_rollback=False, has_test_evidence=None, restart_required=True, cert_expires_at=date(2026, 11, 20),
    ).to_domain()  # fmt: skip
    assert domain.source == "203.0.113.10/32" and domain.ticket == "INC-2026-0412" and domain.reason == "장애 대응"
    assert domain.extra_changes == ["로그 레벨"] and domain.cert_expires_at == date(2026, 11, 20)
    assert (domain.has_rollback, domain.has_test_evidence, domain.restart_required) == (False, None, True)
    assert domain.start == datetime(2026, 10, 30, 20, 0, tzinfo=KST)


def _to_wire(original: ChangeRequest) -> WireChangeRequest:
    wire_data = original.model_dump()
    for name in ("source", "ticket", "reason"):
        wire_data[name] = wire_data[name] or ""
    wire_data["extra_changes_known"] = original.extra_changes is not None
    wire_data["extra_changes"] = original.extra_changes or []
    return WireChangeRequest.model_validate(wire_data)


def _dev_label(request_id: str) -> ChangeRequest:
    item = next(item for item in load_requests("dev") if item.request_id == request_id)
    return ChangeRequest.model_validate(item.expected.structured.model_dump(exclude={"acceptable_change_types", "time_note"}))


def test_dev_정답_구조화를_와이어로_옮겼다_되돌리면_extra_changes까지_똑같다():
    """빈 목록과 null을 extra_changes_known으로 구분하므로 왕복해도 잃는 것이 없다."""
    for item in load_requests("dev"):
        original = _dev_label(item.request_id)
        assert _to_wire(original).to_domain().model_dump() == original.model_dump(), item.request_id


def test_와이어를_거친_표준_변경도_엔진이_STD_01로_확정한다():
    """N3가 anthropic에서도 통하는지: 로그 레벨만 바꾸는 REQ-D04의 빈 목록이 None으로 바뀌면 배포로 간다."""
    original = _dev_label("REQ-D04")
    assert original.extra_changes == []
    confirmed = rules_engine.is_standard(_to_wire(original).to_domain())
    assert confirmed.is_standard is True and confirmed.standard_change_id == "STD-01"
    # 모른다고(false) 답하면 지어내지 않고 표준으로 확정하지 않는다
    unknown = _to_wire(original).model_copy(update={"extra_changes_known": False})
    assert rules_engine.is_standard(unknown.to_domain()).is_standard is False


def test_권고_변환은_빈_대안을_None으로_두고_similar_history는_항상_None():
    wire = WireRecommendation.model_validate(
        {
            "violations": [{"rule_id": "CHG-01", "severity": "forbidden", "evidence": "근거"}],
            "verdict": "반려", "risk": "높음", "emergency_valid": None, "post_report_required": False,
            "explanation": "설명", "conditional_alternative": "  ", "citations": ["CHG-01"], "warnings": [],
        }
    )  # fmt: skip
    rec = wire.to_domain()
    assert rec.conditional_alternative is None and rec.similar_history is None
    assert rec.violations[0].rule_id == "CHG-01" and rec.verdict == "반려" and rec.risk == "높음"
    assert wire.model_copy(update={"conditional_alternative": "14시로 옮긴다"}).to_domain().conditional_alternative == "14시로 옮긴다"


def test_provider별_스키마_선택():
    assert output_schema("anthropic") is V0Wire
    for provider in ("ollama", "openai", "fake"):
        assert output_schema(provider) is V0Output


def test_to_output은_도메인_모델은_그대로_와이어는_변환한다():
    domain = V0Output(
        structured=ChangeRequest(target="payments-db", change_type="firewall"),
        recommendation=Recommendation(verdict="승인"),
    )
    assert to_output(domain) is domain
    wire = V0Wire(
        structured=_wire_request(),
        recommendation=WireRecommendation(
            violations=[], verdict="승인", risk="높음", emergency_valid=None, post_report_required=False,
            explanation="", conditional_alternative="", citations=[], warnings=[],
        ),
    )  # fmt: skip
    converted = to_output(wire)
    assert isinstance(converted, V0Output) and converted.structured.source is None
    assert converted.recommendation.verdict == "승인" and converted.recommendation.conditional_alternative is None


async def test_노드가_와이어_응답을_도메인_상태로_돌려준다(monkeypatch):
    """fake provider로 와이어 스키마 경로를 강제해서 노드의 변환까지 확인한다."""
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()
    monkeypatch.setattr(baseline_v0, "output_schema", lambda provider: V0Wire)
    wire_response = {
        "structured": _wire_request(source="203.0.113.10", ticket="").model_dump(mode="json"),
        "recommendation": {
            "violations": [], "verdict": "승인", "risk": "중간", "emergency_valid": None,
            "post_report_required": False, "explanation": "통과", "conditional_alternative": "",
            "citations": [], "warnings": [],
        },
    }  # fmt: skip
    set_fake_responses("main", [wire_response])
    item = load_requests("dev")[1]
    state = {
        "request_id": item.request_id, "submitted_at": item.submitted_at.isoformat(),
        "requester": item.requester, "masked_text": item.text,
    }  # fmt: skip
    result = await build_graph("v0").ainvoke(state)
    assert isinstance(result["structured"], ChangeRequest) and result["structured"].source == "203.0.113.10"
    assert result["structured"].ticket is None
    assert isinstance(result["recommendation"], Recommendation) and result["recommendation"].conditional_alternative is None
    assert result["metrics"] == {"parse_retries": 0}


@pytest.mark.parametrize("bad", [{}, {"structured": {}}])
async def test_와이어_응답이_틀리면_1회_재시도한다(monkeypatch, bad):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()
    monkeypatch.setattr(baseline_v0, "output_schema", lambda provider: V0Wire)
    good = {
        "structured": _wire_request().model_dump(mode="json"),
        "recommendation": {
            "violations": [], "verdict": "승인", "risk": None, "emergency_valid": None, "post_report_required": False,
            "explanation": "", "conditional_alternative": "", "citations": [], "warnings": [],
        },
    }  # fmt: skip
    set_fake_responses("main", [bad, good])
    item = load_requests("dev")[0]
    state = {
        "request_id": item.request_id, "submitted_at": item.submitted_at.isoformat(),
        "requester": item.requester, "masked_text": item.text,
    }  # fmt: skip
    result = await build_graph("v0").ainvoke(state)
    assert result["metrics"] == {"parse_retries": 1}
