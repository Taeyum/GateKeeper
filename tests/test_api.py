"""API 통합 테스트 (docs/API_CONTRACT.md). TestClient, fake LLM, 메모리 내 MCP, 실제 규칙 엔진, 실제 SQLite 체크포인터.

그래프는 TestClient가 띄운 이벤트 루프에서 백그라운드로 돈다. 테스트는 상세 응답을 폴링해서 상태가 바뀌기를 기다린다.
서버 재시작은 같은 SQLite 파일로 앱을 새로 만들어 연다. dev 라벨만 쓴다.
"""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import data_loader
from app.agent import graph as graph_module
from app.agent.mcp_client import MCPClient
from app.agent.nodes import parse_request as parse_module
from app.agent.nodes.classify import MISMATCH_WARNING
from app.api import events as events_module
from app.api.main import create_app
from app.api.runner import REVIEW_INCOMPLETE_MESSAGES
from app.llm.fake import set_fake_responses_for
from app.mcp_server.server import build_server
from app.schemas import ChangeRequest
from tests._graph_helpers import BAD, OK, draft, gold, label, queue
from tests._history import make_index

KIM = quote("김운영")
LEE = quote("이개발")
PARK = quote("박CAB")


def as_requester(name: str = KIM) -> dict:
    return {"X-User-Role": "requester", "X-User-Name": name}


def as_approver(name: str = PARK) -> dict:
    return {"X-User-Role": "approver", "X-User-Name": name}


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    data_loader.clear_caches()


@pytest.fixture
def new_app(tmp_path, make_settings):
    """같은 SQLite 파일과 감사 로그를 쓰는 앱을 만든다. 두 번 부르면 서버를 다시 띄운 것과 같다."""

    def build(**overrides):
        return create_app(
            mcp=MCPClient(build_server(history_index=make_index()), base_delay=0.01),
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            audit_path=tmp_path / "audit.jsonl",
            settings=make_settings(**overrides),
        )

    return build


@pytest.fixture
def client(new_app):
    with TestClient(new_app(), raise_server_exceptions=False) as running:
        yield running


def audit_lines(tmp_path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def submit(client, dev_id: str, *, headers=None, text: str | None = None, **extra) -> dict:
    response = client.post("/api/requests", json={"text": text or gold(dev_id).text, **extra}, headers=headers or as_requester())
    assert response.status_code == 202, response.text
    return response.json()


def wait_for(client, request_id: str, statuses: set[str], *, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/requests/{request_id}", headers=as_requester()).json()
        if body.get("status") in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"{request_id}가 {statuses}가 되지 않았다: {body.get('status')}")


def parse_sse(text: str) -> tuple[list[tuple[str, dict]], int]:
    """(이벤트 목록, ping 주석 줄 수)."""
    events, pings = [], 0
    for block in text.strip().split("\n\n"):
        if block.startswith(":"):
            pings += 1
            continue
        lines = block.splitlines()
        name = lines[0].removeprefix("event: ")
        events.append((name, json.loads(lines[1].removeprefix("data: "))))
    return events, pings


def read_stream(client, request_id: str) -> list[tuple[str, dict]]:
    with client.stream("GET", f"/api/requests/{request_id}/stream") as response:
        assert response.status_code == 200
        return parse_sse("".join(response.iter_text()))[0]


class Spy:
    """parse_request가 LLM에 넘긴 메시지를 기록하고 실제 fake 체인에 위임한다."""

    def __init__(self, chain, calls):
        self.chain, self.calls = chain, calls

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return await self.chain.ainvoke(messages, config)


@pytest.fixture
def parse_calls(monkeypatch):
    calls: list = []
    real = parse_module.structured_llm
    monkeypatch.setattr(parse_module, "structured_llm", lambda llm, schema, settings=None: Spy(real(llm, schema, settings), calls))
    return calls


def human_text(messages) -> str:
    return "\n".join(str(m.content) for m in messages)


# ---------------------------------------------------------------------------
# 요청 -> 승인 대기 -> 승인자 결정 -> 끝
# ---------------------------------------------------------------------------


def test_요청하면_202로_바로_돌아오고_승인_대기까지_진행한다(client):
    queue("REQ-D01")
    accepted = client.post("/api/requests", json={"text": gold("REQ-D01").text}, headers=as_requester())
    assert accepted.status_code == 202
    assert accepted.json() == {"request_id": "REQ-0001", "parent_request_id": None, "status": "reviewing"}

    body = wait_for(client, "REQ-0001", {"awaiting_approval"})
    assert set(body) == {
        "request_id", "parent_request_id", "status", "version", "requester", "submitted_at", "text", "route", "nodes",
        "structured", "recommendation", "alternative_diff", "impact", "similar_incidents", "has_similar_incident", "approval", "errors",
    }  # fmt: skip
    assert (body["version"], body["requester"], body["route"], body["text"]) == ("v2", "김운영", "general", gold("REQ-D01").text)
    assert body["structured"]["target"] == "payments-db" and body["recommendation"]["verdict"] == "반려"
    assert [item["field"] for item in body["alternative_diff"]] == ["start", "source", "expires_days"]
    assert body["impact"]["id"] == "payments-db" and body["approval"] is None and body["errors"] == []
    assert {run["node"] for run in body["nodes"]} >= {"parse_request", "classify", "decide", "validate", "approval_gate"}
    assert [run for run in body["nodes"] if run["node"] == "approval_gate"][0]["status"] == "running"


def test_승인자가_승인하면_끝나고_결재함에서_빠지고_감사_로그에_남는다(client, tmp_path):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})

    waiting = client.get("/api/approvals", headers=as_approver()).json()["items"]
    assert [item["request_id"] for item in waiting] == ["REQ-0001"] and waiting[0]["status"] == "awaiting_approval"

    decided = client.post("/api/requests/REQ-0001/decision", json={"decision": "approve", "comment": "확인했습니다"}, headers=as_approver())
    assert decided.status_code == 200
    body = decided.json()
    assert (body["request_id"], body["status"]) == ("REQ-0001", "approved")
    assert body["approval"]["approver"] == "박CAB" and body["approval"]["decision"] == "approve" and body["approval"]["comment"] == "확인했습니다"
    assert body["approval"]["at"].endswith("+09:00")

    detail = client.get("/api/requests/REQ-0001", headers=as_requester()).json()
    assert detail["status"] == "approved" and detail["approval"] == body["approval"]
    assert detail["recommendation"]["verdict"] == "반려"  # 사람의 결정이 시스템 권고를 바꾸지 않는다
    assert client.get("/api/approvals", headers=as_approver()).json()["items"] == []

    (line,) = [x for x in audit_lines(tmp_path) if x["event"] == "decision"]
    assert (line["request_id"], line["approver"], line["outcome"], line["verdict"]) == ("REQ-0001", "박CAB", "approved", "반려")


def test_반려_결정도_처리된다(client):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})
    response = client.post("/api/requests/REQ-0001/decision", json={"decision": "reject", "comment": "동결 기간입니다"}, headers=as_approver())
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    assert client.get("/api/requests/REQ-0001", headers=as_approver()).json()["status"] == "rejected"


def test_본문의_approver는_무시하고_헤더의_이름을_쓴다(client):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})
    response = client.post(
        "/api/requests/REQ-0001/decision",
        json={"decision": "approve", "comment": "", "approver": "가짜 승인자", "at": "1999-01-01T00:00:00+09:00"},
        headers=as_approver(),
    )
    assert response.json()["approval"]["approver"] == "박CAB" and response.json()["approval"]["at"] != "1999-01-01T00:00:00+09:00"


def test_두_승인자가_동시에_결정하면_하나만_받아들인다(client):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})

    def decide(name: str):
        return client.post("/api/requests/REQ-0001/decision", json={"decision": "approve", "comment": ""}, headers=as_approver(quote(name)))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ["박CAB", "최CAB"]))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert [r for r in results if r.status_code == 409][0].json()["error"]["code"] == "conflict"


# ---------------------------------------------------------------------------
# 권한과 오류 모양
# ---------------------------------------------------------------------------


def test_요청자가_decision을_호출하면_403이고_감사_로그에_남고_요청은_그대로다(client, tmp_path):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})

    response = client.post("/api/requests/REQ-0001/decision", json={"decision": "approve", "comment": ""}, headers=as_requester())
    assert response.status_code == 403
    assert response.json() == {"error": {"code": "forbidden", "message": "이 작업을 할 수 있는 권한이 없어요."}}
    assert client.get("/api/requests/REQ-0001", headers=as_requester()).json()["status"] == "awaiting_approval"
    (line,) = [x for x in audit_lines(tmp_path) if x["event"] == "forbidden"]
    assert (line["role"], line["name"], line["method"], line["path"]) == ("requester", "김운영", "POST", "/api/requests/REQ-0001/decision")


@pytest.mark.parametrize(
    ("method", "path", "body", "allowed_role"),
    [
        ("POST", "/api/requests", {"text": "x"}, "requester"),
        ("GET", "/api/requests", None, "both"),
        ("GET", "/api/requests/REQ-0001", None, "both"),
        ("GET", "/api/approvals", None, "approver"),
        ("POST", "/api/requests/REQ-0001/decision", {"decision": "approve"}, "approver"),
    ],
)
def test_엔드포인트마다_계약의_역할만_통과한다(client, method, path, body, allowed_role):
    def call(headers):
        return client.request(method, path, json=body, headers=headers)

    assert call({}).status_code == 403  # 헤더가 없다
    wrong = {"requester": as_approver(), "approver": as_requester(), "both": {"X-User-Role": "admin", "X-User-Name": KIM}}[allowed_role]
    assert call(wrong).status_code == 403


def test_stream은_헤더_없이_읽을_수_있다(client):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})
    assert client.get("/api/requests/REQ-0001/stream").status_code == 200  # EventSource는 헤더를 못 붙인다


def test_없는_요청_번호와_잘못된_입력은_계약의_오류_모양이다(client):
    assert client.get("/api/requests/REQ-9999", headers=as_requester()).json()["error"]["code"] == "not_found"
    assert client.get("/api/requests/REQ-9999/stream").status_code == 404
    assert client.post("/api/requests/REQ-9999/decision", json={"decision": "approve"}, headers=as_approver()).status_code == 404

    for body in ({"text": ""}, {"text": "   "}, {"text": "가" * 4001}, {}):
        response = client.post("/api/requests", json=body, headers=as_requester())
        assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error", body
    assert client.get("/api/approvals?status=bogus", headers=as_approver()).status_code == 422
    assert client.post("/api/requests/REQ-9999/decision", json={"decision": "maybe"}, headers=as_approver()).status_code == 422


def test_승인_대기가_아닌_요청에_결정하면_409다(client):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})
    client.post("/api/requests/REQ-0001/decision", json={"decision": "approve", "comment": ""}, headers=as_approver())

    again = client.post("/api/requests/REQ-0001/decision", json={"decision": "reject", "comment": ""}, headers=as_approver())
    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"

    queue("REQ-D04")
    submit(client, "REQ-D04")
    wait_for(client, "REQ-0002", {"auto_approved"})
    assert client.post("/api/requests/REQ-0002/decision", json={"decision": "approve", "comment": ""}, headers=as_approver()).status_code == 409


def test_화면_주소만_CORS로_허용한다(client):
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-user-role,x-user-name,content-type"}
    ok = client.options("/api/requests", headers={"Origin": "http://127.0.0.1:5173", **preflight})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    other = client.options("/api/requests", headers={"Origin": "http://evil.example", **preflight})
    assert "access-control-allow-origin" not in other.headers


# ---------------------------------------------------------------------------
# 마스킹
# ---------------------------------------------------------------------------


def test_비밀값은_LLM_상태_응답_이벤트_감사_로그_어디에도_없다(client, tmp_path, parse_calls):
    queue("REQ-D01")
    text = f"{gold('REQ-D01').text} 접속 password=hunter2-비밀 입니다"
    submit(client, "REQ-D01", text=text)
    wait_for(client, "REQ-0001", {"awaiting_approval"})
    client.post("/api/requests/REQ-0001/decision", json={"decision": "approve", "comment": "임시 api_key=sk-secret-123 확인"}, headers=as_approver())

    detail = client.get("/api/requests/REQ-0001", headers=as_approver()).json()
    assert "password=****" in detail["text"] and detail["approval"]["comment"] == "임시 api_key=**** 확인"
    llm_input = "\n".join(human_text(call) for call in parse_calls)
    assert "password=****" in llm_input  # LLM에는 마스킹한 본문이 갔다
    everything = (
        llm_input
        + json.dumps(detail, ensure_ascii=False)
        + json.dumps(read_stream(client, "REQ-0001"), ensure_ascii=False)
        + (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    )
    for secret in ("hunter2", "sk-secret-123"):
        assert secret not in everything


# ---------------------------------------------------------------------------
# 서버를 다시 띄워도 이어진다 (체크포인터)
# ---------------------------------------------------------------------------


def test_서버를_다시_띄워도_승인_대기_요청이_남고_이어서_승인할_수_있다(new_app):
    queue("REQ-D01")
    with TestClient(new_app(), raise_server_exceptions=False) as first:
        submit(first, "REQ-D01")
        before = wait_for(first, "REQ-0001", {"awaiting_approval"})
    # first가 닫혔다: lifespan이 끝나고 체크포인터와 MCP가 닫혔다

    with TestClient(new_app(), raise_server_exceptions=False) as second:
        after = second.get("/api/requests/REQ-0001", headers=as_requester()).json()
        assert after["status"] == "awaiting_approval"
        assert after["recommendation"] == before["recommendation"] and after["structured"] == before["structured"]
        assert after["text"] == before["text"] and after["requester"] == "김운영" and after["nodes"] == []  # 진행 기록은 복구하지 않는다
        assert [i["request_id"] for i in second.get("/api/approvals", headers=as_approver()).json()["items"]] == ["REQ-0001"]

        # 이벤트 기록이 없으니 종료 이벤트 하나만 보내고 닫는다
        events = read_stream(second, "REQ-0001")
        assert [name for name, _ in events] == ["awaiting_approval"] and events[0][1]["status"] == "awaiting_approval"

        decided = second.post("/api/requests/REQ-0001/decision", json={"decision": "reject", "comment": "재시작 뒤 결정"}, headers=as_approver())
        assert decided.status_code == 200 and decided.json()["status"] == "rejected"

        queue("REQ-D06")  # 요청 번호는 이어서 매긴다
        assert submit(second, "REQ-D06")["request_id"] == "REQ-0002"
        assert [name for name, _ in read_stream(second, "REQ-0001")] == ["done"]  # 결정 뒤의 재연결은 done 하나


def test_수동_검토로_넘긴_요청은_서버를_다시_띄워도_수동_검토로_보인다(new_app):
    queue("REQ-D01")
    wrong = label("REQ-D01", target="없는-서비스")
    set_fake_responses_for("main", ChangeRequest, [wrong, wrong])
    with TestClient(new_app(), raise_server_exceptions=False) as first:
        submit(first, "REQ-D01")
        wait_for(first, "REQ-0001", {"manual_review"})
    with TestClient(new_app(), raise_server_exceptions=False) as second:
        assert second.get("/api/requests/REQ-0001", headers=as_requester()).json()["status"] == "manual_review"
        (name, data) = read_stream(second, "REQ-0001")[0]
        assert name == "done" and data["status"] == "manual_review" and data["message"] == REVIEW_INCOMPLETE_MESSAGES["parse_request"]
        assert second.get("/api/approvals", headers=as_approver()).json()["items"] == []


# ---------------------------------------------------------------------------
# 표준 변경, 분류 불일치
# ---------------------------------------------------------------------------


def test_표준_변경은_사람_없이_자동_승인되고_기록에서_볼_수_있다(client, tmp_path):
    queue("REQ-D04")
    submit(client, "REQ-D04")
    body = wait_for(client, "REQ-0001", {"auto_approved"})

    assert body["route"] == "standard" and body["approval"] is None and body["recommendation"]["verdict"] == "자동 승인"
    assert body["impact"] is None and body["similar_incidents"] == [] and body["alternative_diff"] is None
    assert client.get("/api/approvals", headers=as_approver()).json()["items"] == []  # 승인 대기가 아니다

    (item,) = client.get("/api/approvals?status=auto_approved", headers=as_approver()).json()["items"]
    assert (item["request_id"], item["status"], item["standard_change_id"], item["risk"]) == ("REQ-0001", "auto_approved", "STD-01", None)
    assert item["decided_at"].endswith("+09:00")

    names = [name for name, _ in read_stream(client, "REQ-0001")]
    assert "awaiting_approval" not in names and names[-1] == "done"
    assert [x["event"] for x in audit_lines(tmp_path)] == ["auto_approved"]


def test_분류가_어긋난_표준_변경은_자동_승인하지_않고_사람에게_간다(client):
    queue("REQ-D04", small_change_type="deploy")  # 엔진은 표준 변경, 작은 모델은 배포라고 답한다
    submit(client, "REQ-D04")
    body = wait_for(client, "REQ-0001", {"awaiting_approval", "auto_approved"})

    assert body["status"] == "awaiting_approval"
    assert body["route"] == "general" and body["recommendation"]["verdict"] == "자동 승인"  # 판정은 엔진 그대로
    assert MISMATCH_WARNING in body["recommendation"]["warnings"]
    assert [i["request_id"] for i in client.get("/api/approvals", headers=as_approver()).json()["items"]] == ["REQ-0001"]


# ---------------------------------------------------------------------------
# 재요청
# ---------------------------------------------------------------------------


def test_재요청은_새_요청이고_부모_본문과_합쳐_검토하되_응답_text는_새_본문뿐이다(client, parse_calls):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})
    client.post("/api/requests/REQ-0001/decision", json={"decision": "reject", "comment": "다시 올려 주세요"}, headers=as_approver())

    queue("REQ-D01")
    fixed = "출발지는 B페이먼츠(203.0.113.10)로, 11/3 화요일 오후 2시에 7일만 열어주세요."
    accepted = client.post("/api/requests", json={"text": fixed, "parent_request_id": "REQ-0001"}, headers=as_requester())
    assert accepted.status_code == 202
    assert accepted.json() == {"request_id": "REQ-0002", "parent_request_id": "REQ-0001", "status": "reviewing"}
    child = wait_for(client, "REQ-0002", {"awaiting_approval"})

    assert child["parent_request_id"] == "REQ-0001" and child["text"] == fixed  # 부모 본문은 응답에 없다
    assert gold("REQ-D01").text not in child["text"]
    second_call = human_text(parse_calls[-1])  # LLM에는 합친 문장이 갔다
    assert gold("REQ-D01").text in second_call and fixed in second_call
    assert second_call.index(gold("REQ-D01").text) < second_call.index(fixed) and "이전 요청과 다르면 이번 내용이 맞다" in second_call

    parent = client.get("/api/requests/REQ-0001", headers=as_requester()).json()
    assert parent["status"] == "rejected" and parent["parent_request_id"] is None  # 부모는 그대로 남는다
    items = client.get("/api/requests", headers=as_requester()).json()["items"]
    assert [(i["request_id"], i["parent_request_id"]) for i in items] == [("REQ-0002", "REQ-0001"), ("REQ-0001", None)]


def test_재요청의_재요청도_원_요청_본문을_parse_입력에_담고_응답_text는_새_본문뿐이다(client, parse_calls):
    original = gold("REQ-D01").text
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})
    client.post("/api/requests/REQ-0001/decision", json={"decision": "reject", "comment": "다시"}, headers=as_approver())

    first_fix = "출발지는 B페이먼츠(203.0.113.10)입니다."
    queue("REQ-D01")
    client.post("/api/requests", json={"text": first_fix, "parent_request_id": "REQ-0001"}, headers=as_requester())
    wait_for(client, "REQ-0002", {"awaiting_approval"})
    client.post("/api/requests/REQ-0002/decision", json={"decision": "reject", "comment": "한 번 더"}, headers=as_approver())

    second_fix = "11/3 화요일 오후 2시에 7일만 열어주세요."
    queue("REQ-D01")
    accepted = client.post("/api/requests", json={"text": second_fix, "parent_request_id": "REQ-0002"}, headers=as_requester())
    assert accepted.json() == {"request_id": "REQ-0003", "parent_request_id": "REQ-0002", "status": "reviewing"}
    grandchild = wait_for(client, "REQ-0003", {"awaiting_approval"})

    third_call = human_text(parse_calls[-1])  # 부모(REQ-0002)가 이미 재요청이어도 원 요청의 대상이 입력에 남는다
    assert original in third_call and first_fix in third_call and second_fix in third_call
    assert third_call.index(original) < third_call.index(first_fix) < third_call.index(second_fix)
    assert grandchild["text"] == second_fix  # 응답에는 이번에 새로 쓴 본문뿐이다
    assert original not in grandchild["text"] and first_fix not in grandchild["text"]


def test_없는_부모로_재요청하면_404다(client):
    response = client.post("/api/requests", json={"text": "고쳐서 다시", "parent_request_id": "REQ-9999"}, headers=as_requester())
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    assert client.get("/api/requests", headers=as_requester()).json()["items"] == []  # 요청이 만들어지지 않았다


# ---------------------------------------------------------------------------
# 목록
# ---------------------------------------------------------------------------


def test_요청자는_본인_요청만_승인자는_전부_최신순으로_본다(client):
    for number, (dev_id, name) in enumerate((("REQ-D01", KIM), ("REQ-D06", LEE), ("REQ-D05", KIM)), start=1):
        queue(dev_id)  # fake LLM 응답 큐는 전역이라 앞 요청이 끝난 뒤에 다음 응답을 넣는다
        submit(client, dev_id, headers=as_requester(name))
        wait_for(client, f"REQ-{number:04d}", {"awaiting_approval"})

    mine = client.get("/api/requests", headers=as_requester(KIM)).json()["items"]
    assert [i["request_id"] for i in mine] == ["REQ-0003", "REQ-0001"] and {i["requester"] for i in mine} == {"김운영"}
    assert [i["request_id"] for i in client.get("/api/requests", headers=as_requester(LEE)).json()["items"]] == ["REQ-0002"]
    everyone = client.get("/api/requests", headers=as_approver()).json()["items"]
    assert [i["request_id"] for i in everyone] == ["REQ-0003", "REQ-0002", "REQ-0001"]
    assert set(everyone[0]) == {
        "request_id", "parent_request_id", "requester", "submitted_at", "target", "change_type", "start",
        "status", "verdict", "risk", "has_similar_incident", "standard_change_id", "decided_at",
    }  # fmt: skip


def test_결재함은_위험도가_높은_순이고_같으면_유사_장애가_먼저다(client):
    for number, dev_id in enumerate(("REQ-D05", "REQ-D06", "REQ-D01", "REQ-D03", "REQ-D08"), start=1):
        queue(dev_id)  # fake LLM 응답 큐는 전역이라 앞 요청이 끝난 뒤에 다음 응답을 넣는다
        submit(client, dev_id)
        wait_for(client, f"REQ-{number:04d}", {"awaiting_approval"})

    items = client.get("/api/approvals", headers=as_approver()).json()["items"]
    assert len(items) == 5
    rank = {"높음": 0, "중간": 1, "낮음": 2}
    keys = [(rank[i["risk"]], not i["has_similar_incident"]) for i in items]
    assert keys == sorted(keys)  # 위험도 -> 유사 장애 있는 것 먼저
    assert items[0]["risk"] == "높음" and items[-1]["risk"] == "낮음"


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


def test_SSE는_계약의_순서와_모양으로_replay되고_두_번_연결해도_같다(client):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})

    with client.stream("GET", "/api/requests/REQ-0001/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache" and response.headers["x-accel-buffering"] == "no"
        events = parse_sse("".join(response.iter_text()))[0]
    assert read_stream(client, "REQ-0001") == events  # replay는 같은 순서로 같은 이벤트다

    names = [name for name, _ in events]
    assert set(names) <= {"node_started", "node_completed", "recommendation", "awaiting_approval"}
    assert names[0] == "node_started" and names[-1] == "awaiting_approval"
    assert names.index("recommendation") < len(names) - 2 and names.count("recommendation") == 1
    started = [d["node"] for n, d in events if n == "node_started"]
    assert started[:2] == ["parse_request", "classify"] and started[-1] == "approval_gate"
    assert set(started[2:5]) == {"policy_agent", "impact_agent", "history_agent"}  # 병렬 세 개

    for name, data in events:
        if name == "node_started":
            assert set(data) == {"node", "at"}
        elif name == "node_completed":
            assert {"node", "at", "error"} <= set(data) and data["error"] is None
        elif name == "recommendation":
            assert set(data) == {"recommendation", "alternative_diff", "impact", "similar_incidents", "has_similar_incident"}
        else:
            assert data["status"] == "awaiting_approval" and set(data) == {"request_id", "status", "at"}
    by_node = {d["node"]: d for n, d in events if n == "node_completed"}
    assert by_node["parse_request"]["structured"]["target"] == "payments-db" and by_node["classify"]["route"] == "general"


def test_결정_뒤에_연결하면_done까지_받는다(client):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    wait_for(client, "REQ-0001", {"awaiting_approval"})
    client.post("/api/requests/REQ-0001/decision", json={"decision": "approve", "comment": "확인"}, headers=as_approver())

    events = read_stream(client, "REQ-0001")
    names = [name for name, _ in events]
    assert names.count("awaiting_approval") == 1 and names[-1] == "done"
    assert names.index("awaiting_approval") < names.index("done")
    done = events[-1][1]
    assert (done["request_id"], done["status"], done["message"]) == ("REQ-0001", "approved", None)
    assert done["approval"]["approver"] == "박CAB" and set(done) == {"request_id", "status", "at", "approval", "message"}


def test_검토_불가는_done_manual_review로_보내고_예상_못_한_오류만_error다(new_app, monkeypatch):
    queue("REQ-D01")
    wrong = label("REQ-D01", target="없는-서비스")
    set_fake_responses_for("main", ChangeRequest, [wrong, wrong])
    with TestClient(new_app(), raise_server_exceptions=False) as client:
        submit(client, "REQ-D01")
        wait_for(client, "REQ-0001", {"manual_review"})
        names = [name for name, _ in read_stream(client, "REQ-0001")]
        assert names[-1] == "done" and "error" not in names
        assert client.get("/api/requests/REQ-0001", headers=as_requester()).json()["status"] == "manual_review"

    async def boom(state, config):
        raise RuntimeError("내부 상세 secret-token=abc123")

    monkeypatch.setattr(graph_module, "decide", boom)
    queue("REQ-D06")
    with TestClient(new_app(), raise_server_exceptions=False) as client:
        submit(client, "REQ-D06")
        wait_for(client, "REQ-0002", {"manual_review"})
        events = read_stream(client, "REQ-0002")
        assert events[-1] == ("error", {"code": "internal", "message": "자동 검토를 완료하지 못해 수동 검토로 넘깁니다."})
        assert "abc123" not in json.dumps(events, ensure_ascii=False)


def test_이벤트가_없는_동안에는_ping을_보낸다(new_app, monkeypatch):
    real_decide = graph_module.decide

    async def slow(state, config):
        await asyncio.sleep(0.6)
        return await real_decide(state, config)

    monkeypatch.setattr(graph_module, "decide", slow)
    monkeypatch.setattr(events_module, "PING_SECONDS", 0.05)
    queue("REQ-D06")
    with TestClient(new_app(), raise_server_exceptions=False) as client:
        submit(client, "REQ-D06")
        with client.stream("GET", "/api/requests/REQ-0001/stream") as response:
            events, pings = parse_sse("".join(response.iter_text()))
        assert pings >= 1 and events[-1][0] == "awaiting_approval"  # 라이브로 받다가 ping이 끼고, 승인 대기에서 닫힌다


def test_스트림을_끊어도_그래프_실행은_계속된다(client):
    queue("REQ-D01")
    submit(client, "REQ-D01")
    with client.stream("GET", "/api/requests/REQ-0001/stream") as response:
        next(response.iter_text())  # 첫 조각만 받고 연결을 끊는다
    assert wait_for(client, "REQ-0001", {"awaiting_approval"})["recommendation"]["verdict"] == "반려"


# ---------------------------------------------------------------------------
# 상세 응답의 나머지
# ---------------------------------------------------------------------------


def test_재작성이_있어도_상세의_권고는_마지막_것이고_진행_기록은_노드당_한_항목이다(client):
    queue("REQ-D06", drafts=[draft("처음 설명"), draft("고친 설명")], checks=[BAD, OK])
    submit(client, "REQ-D06")
    body = wait_for(client, "REQ-0001", {"awaiting_approval"})
    assert body["recommendation"]["explanation"] == "고친 설명"
    assert [run["node"] for run in body["nodes"]].count("decide") == 1
    assert [name for name, _ in read_stream(client, "REQ-0001")].count("recommendation") == 1
