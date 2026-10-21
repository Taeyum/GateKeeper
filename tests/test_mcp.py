"""MCP 서버(Resource와 Tool) 테스트.

메모리 내 연결(Client(FastMCP 인스턴스))로 서버를 직접 호출한다. 실제 stdio 서브프로세스 경로는
tests/test_mcp_client.py에서 확인한다.

engine 마커가 붙은 테스트는 2단계 규칙 엔진 본문이 필요하다. tests/conftest.py의 ENGINE_READY를
True로 바꾸면 실행된다.
"""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from app import rules_engine
from app.data_loader import load_gk_data, load_requests
from app.mcp_server.server import build_server
from app.schemas import PolicyCheckResult, Violation
from tests._engine import dev_case, requires_engine

RULE_URIS = [f"rules://CHG-{i:02d}" for i in range(1, 11)]
EXPECTED_URIS = {"rules://index", *RULE_URIS, "rules://standard"}
EXPECTED_TOOLS = {"check_policy", "check_window", "get_service_info", "get_calendar", "get_incident"}


@pytest.fixture
async def client():
    async with Client(build_server()) as c:
        yield c


def _payload(request, submitted_at) -> dict:
    return {"request": request.model_dump(mode="json"), "submitted_at": submitted_at.isoformat()}


# ---------------------------------------------------------------- Resource


async def test_리소스_목록은_12개다(client):
    resources = await client.list_resources()
    assert {str(r.uri) for r in resources} == EXPECTED_URIS and len(resources) == 12
    assert all(r.mime_type == "text/markdown" for r in resources)


async def test_템플릿이_아니라_개별_리소스로_등록한다(client):
    # 템플릿은 list_resources에 나오지 않아 에이전트가 조항을 찾을 수 없다
    assert await client.list_resource_templates() == []


async def test_CHG_03_읽기(client):
    contents = await client.read_resource("rules://CHG-03")
    text = contents[0].text
    assert text.startswith("### CHG-03 DB 포트 외부 노출 금지")
    assert "5432" in text and "파트너" in text
    assert "CHG-04 롤백" not in text


async def test_모든_조항_리소스는_자기_조항만_돌려준다(client):
    for uri in RULE_URIS:
        text = (await client.read_resource(uri))[0].text
        assert text.startswith(f"### {uri.removeprefix('rules://')} ")


async def test_index와_standard(client):
    index = (await client.read_resource("rules://index"))[0].text
    assert all(uri in index for uri in RULE_URIS) and "rules://standard" in index
    standard = (await client.read_resource("rules://standard"))[0].text
    assert "STD-01" in standard and "STD-02" in standard


async def test_없는_조항은_오류다(client):
    with pytest.raises(MCPError, match="not found"):
        await client.read_resource("rules://CHG-99")


# ---------------------------------------------------------------- Tool 목록과 조회 Tool


async def test_Tool은_5개이고_search_history는_아직_없다(client):
    # 6단계에서 search_history를 추가하면 EXPECTED_TOOLS에 넣고 6개로 바꾼다
    tools = await client.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all(t.description for t in tools)
    assert all(t.annotations.read_only_hint for t in tools)


async def test_서비스_정보_조회(client):
    result = await client.call_tool("get_service_info", {"service_id": "payments-db"})
    info = result.structured_content
    assert (info["tier"], info["owner_team"], info["default_port"]) == ("Tier-1", "DBA팀", 5432)
    assert "payments-service" in info["dependents"]


async def test_없는_서비스는_NOT_FOUND_오류다(client):
    with pytest.raises(ToolError, match="NOT_FOUND"):
        await client.call_tool("get_service_info", {"service_id": "payment-db"})


async def test_캘린더_조회는_시간대_없는_입력을_KST로_본다(client):
    result = await client.call_tool(
        "get_calendar", {"target": "payments-db", "start": "2026-10-30T20:00:00", "end": "2026-10-30T21:00:00"}
    )
    assert [f["freeze_id"] for f in result.structured_content["freeze_periods"]] == ["FRZ-01"]
    assert result.structured_content["start"] == "2026-10-30T20:00:00+09:00"


async def test_캘린더_조회_오류(client):
    window = {"start": "2026-10-30T20:00:00+09:00", "end": "2026-10-30T21:00:00+09:00"}
    with pytest.raises(ToolError, match="NOT_FOUND"):
        await client.call_tool("get_calendar", {"target": "payment-db", **window})
    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await client.call_tool("get_calendar", {"target": "payments-db", "start": window["end"], "end": window["start"]})


async def test_티켓_조회(client):
    ticket = (await client.call_tool("get_incident", {"ticket": "INC-2026-0412"})).structured_content
    assert ticket["status"] == "open" and "payments-db" in ticket["services"]
    with pytest.raises(ToolError, match="NOT_FOUND"):
        await client.call_tool("get_incident", {"ticket": "INC-0000-0000"})


async def test_주입한_데이터로_서버를_만든다():
    data = load_gk_data()
    no_tickets = data.model_copy(update={"incidents": data.incidents.model_copy(update={"incidents": []})})
    async with Client(build_server(data=no_tickets)) as client:
        with pytest.raises(ToolError, match="NOT_FOUND"):
            await client.call_tool("get_incident", {"ticket": "INC-2026-0412"})


# ---------------------------------------------------------------- 규칙 엔진 Tool (배선)
# 엔진 본문이 없어도 Tool이 인자를 올바르게 넘기고 결과를 직렬화하는지 확인한다.


async def test_check_policy는_엔진에_인자를_그대로_넘긴다(client, monkeypatch):
    request, submitted_at, _ = dev_case("REQ-D01")
    seen = {}

    def fake_check_all(req, at, data=None):
        seen.update(request=req, submitted_at=at, data=data)
        return PolicyCheckResult(
            violations=[Violation(rule_id="CHG-99", severity="forbidden", evidence="가짜 위반")],
            standard_change_id=None,
        )

    monkeypatch.setattr(rules_engine, "check_all", fake_check_all)
    result = await client.call_tool("check_policy", _payload(request, submitted_at))

    assert seen["request"] == request and seen["submitted_at"] == submitted_at and seen["data"] is None
    assert result.structured_content["violations"][0]["rule_id"] == "CHG-99"
    assert set(result.structured_content) == {"violations", "exempted", "emergency_valid", "standard_change_id"}


async def test_check_policy는_주입한_데이터를_엔진에_넘긴다(monkeypatch):
    request, submitted_at, _ = dev_case("REQ-D01")
    data = load_gk_data()
    seen = {}
    monkeypatch.setattr(rules_engine, "check_all", lambda req, at, d=None: seen.update(data=d) or PolicyCheckResult())
    async with Client(build_server(data=data)) as client:
        await client.call_tool("check_policy", _payload(request, submitted_at))
    assert seen["data"] is data


async def test_check_policy는_라벨_전용_필드를_받지_않는다(client):
    request, submitted_at, _ = dev_case("REQ-D01")
    payload = _payload(request, submitted_at)
    payload["request"]["acceptable_change_types"] = ["firewall"]
    with pytest.raises(ToolError):
        await client.call_tool("check_policy", payload)


async def test_check_window는_엔진_결과를_violations로_감싼다(client, monkeypatch):
    seen = {}

    def fake_check_window(target, change_type, start, end, submitted_at, data=None):
        seen.update(target=target, change_type=change_type, start=start, end=end, submitted_at=submitted_at)
        return [Violation(rule_id="CHG-01", severity="forbidden", evidence="가짜")]

    monkeypatch.setattr(rules_engine, "check_window", fake_check_window)
    result = await client.call_tool(
        "check_window",
        {
            "target": "payments-db",
            "change_type": "firewall",
            "start": "2026-11-03T14:00:00",
            "end": "2026-11-03T15:00:00",
            "submitted_at": "2026-10-26T10:00:00+09:00",
        },
    )
    assert result.structured_content == {
        "violations": [{"rule_id": "CHG-01", "severity": "forbidden", "evidence": "가짜"}]
    }
    assert seen["target"] == "payments-db" and seen["change_type"] == "firewall"
    assert seen["start"].utcoffset().total_seconds() == 9 * 3600  # 시간대 없는 입력은 KST


async def test_check_window는_끝이_시작보다_늦지_않으면_거부한다(client):
    with pytest.raises(ToolError, match="INVALID_ARGUMENT"):
        await client.call_tool(
            "check_window",
            {
                "target": "payments-db",
                "change_type": "firewall",
                "start": "2026-11-03T15:00:00+09:00",
                "end": "2026-11-03T14:00:00+09:00",
                "submitted_at": "2026-10-26T10:00:00+09:00",
            },
        )


async def test_엔진의_내부_오류_상세는_응답에_싣지_않는다(client, monkeypatch):
    request, submitted_at, _ = dev_case("REQ-D01")

    def boom(*args, **kwargs):
        raise RuntimeError("내부 경로 /secret/path 와 비밀값")

    monkeypatch.setattr(rules_engine, "check_all", boom)
    with pytest.raises(ToolError) as excinfo:
        await client.call_tool("check_policy", _payload(request, submitted_at))
    assert "/secret/path" not in str(excinfo.value) and "비밀값" not in str(excinfo.value)


# ---------------------------------------------------------------- dev 라벨과 데이터의 일치 (엔진 불필요)


async def test_캘린더_겹침은_dev_라벨의_CHG_10_CHG_06과_일치한다(client):
    """get_calendar가 보여 주는 겹침이 라벨의 위반과 같은 기준인지 확인한다.

    CHG-10은 유효한 긴급 변경이면 면제되므로 긴급 요청은 동결 겹침이 있어도 위반이 아니다.
    """
    for case in load_requests("dev"):
        s = case.expected.structured
        result = (
            await client.call_tool(
                "get_calendar", {"target": s.target, "start": s.start.isoformat(), "end": s.end.isoformat()}
            )
        ).structured_content
        violations = set(case.expected.violations)
        has_freeze = bool(result["freeze_periods"])
        if case.expected.emergency_valid:
            assert has_freeze and "CHG-10" not in violations, case.request_id  # 동결에 걸리지만 면제
        else:
            assert has_freeze == ("CHG-10" in violations), case.request_id
        assert bool(result["scheduled_changes"]) == ("CHG-06" in violations), case.request_id


# ---------------------------------------------------------------- 규칙 엔진 필요 (2단계 머지 후)


@requires_engine
async def test_REQ_D01의_위반_5개(client):
    request, submitted_at, _ = dev_case("REQ-D01")
    result = (await client.call_tool("check_policy", _payload(request, submitted_at))).structured_content
    assert {v["rule_id"] for v in result["violations"]} == {"CHG-01", "CHG-02", "CHG-03", "CHG-09", "CHG-10"}


@requires_engine
@pytest.mark.parametrize("request_id", [f"REQ-D{i:02d}" for i in range(1, 11)])
async def test_check_policy는_dev_라벨의_위반과_같다(client, request_id):
    request, submitted_at, case = dev_case(request_id)
    result = (await client.call_tool("check_policy", _payload(request, submitted_at))).structured_content
    assert {v["rule_id"] for v in result["violations"]} == set(case.expected.violations)


@requires_engine
async def test_check_window는_동결_기간을_잡아낸다(client):
    result = await client.call_tool(
        "check_window",
        {
            "target": "payments-db",
            "change_type": "firewall",
            "start": "2026-10-30T20:00:00+09:00",
            "end": "2026-10-30T21:00:00+09:00",
            "submitted_at": "2026-10-29T16:00:00+09:00",
        },
    )
    assert {v["rule_id"] for v in result.structured_content["violations"]} >= {"CHG-01", "CHG-10"}
