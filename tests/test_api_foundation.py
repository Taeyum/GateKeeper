"""API 기반: 오류 응답 모양, 역할 Depends, 마스킹 미들웨어.

실제 앱(app/api/main.py)이 아니라 같은 부품을 단 작은 FastAPI 앱으로 확인한다. 부품이 서로를 몰라도 되게 만들었기 때문에
라우트 하나씩 단 앱이면 충분하다.
"""

import json
import logging
from urllib.parse import quote

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.api.deps import Container, User, any_role, approver_only, decode_name, requester_only
from app.api.errors import conflict, install_error_handlers, not_found, validation_error
from app.api.middleware import MAX_BODY_BYTES, MaskingMiddleware, mask_json
from app.api.store import RequestStore
from app.audit import AuditLog

KOREAN_NAME = "%EA%B9%80%EC%9A%B4%EC%98%81"  # 김운영
SECRET = "hunter2-비밀"


class Body(BaseModel):
    text: str = Field(min_length=1, max_length=20)


def make_app(tmp_path, make_settings, *, masking: bool = True) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    if masking:
        app.add_middleware(MaskingMiddleware)
    # 이 부품들은 그래프나 저장소를 쓰지 않는다. 역할 확인과 감사 로그만 본다.
    app.state.container = Container(
        settings=make_settings(), audit=AuditLog(tmp_path / "audit.jsonl"), graph=None, store=RequestStore(), runner=None  # type: ignore[arg-type]
    )

    @app.post("/api/echo")
    async def echo(payload: dict):
        return payload

    @app.post("/api/body")
    async def body(item: Body):
        return {"text": item.text}

    @app.post("/other/echo")
    async def other(payload: dict):
        return payload

    @app.get("/api/requester")
    async def requester(user: User = Depends(requester_only)):
        return {"role": user.role, "name": user.name}

    @app.get("/api/approver")
    async def approver(user: User = Depends(approver_only)):
        return {"role": user.role, "name": user.name}

    @app.get("/api/any")
    async def anyone(user: User = Depends(any_role)):
        return {"role": user.role, "name": user.name}

    @app.get("/api/conflict")
    async def raise_conflict():
        raise conflict()

    @app.get("/api/missing")
    async def raise_missing():
        raise not_found()

    @app.get("/api/invalid")
    async def raise_invalid():
        raise validation_error()

    @app.get("/api/boom")
    async def boom():
        raise RuntimeError(f"내부 오류 password={SECRET}")

    return app


@pytest.fixture
def client(tmp_path, make_settings):
    return TestClient(make_app(tmp_path, make_settings), raise_server_exceptions=False)


def headers(role: str | None, name: str | None = KOREAN_NAME) -> dict:
    return {k: v for k, v in {"X-User-Role": role, "X-User-Name": name}.items() if v is not None}


def audit_lines(tmp_path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ---------------------------------------------------------------------------
# 오류 응답
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [("/api/conflict", 409, "conflict"), ("/api/missing", 404, "not_found"), ("/api/invalid", 422, "validation_error")],
)
def test_계약의_오류_코드는_한_모양으로_나간다(client, path, status, code):
    response = client.get(path)
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code and body["error"]["message"]


def test_없는_경로와_허용되지_않는_메서드도_같은_모양이다(client):
    assert client.get("/api/nope").json()["error"]["code"] == "not_found"
    not_allowed = client.post("/api/any")
    assert not_allowed.status_code == 405 and not_allowed.json()["error"]["code"] == "validation_error"


def test_본문_검증_실패는_입력값을_되돌려_주지_않는다(tmp_path, make_settings):
    unmasked = TestClient(make_app(tmp_path, make_settings, masking=False), raise_server_exceptions=False)
    response = unmasked.post("/api/body", json={"text": "비밀입니다 token=abc123 " * 5})  # 길이 초과
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "abc123" not in response.text and "비밀입니다" not in response.text  # FastAPI 기본 422는 input을 싣는다


def test_예상하지_못한_오류는_500_internal이고_예외_내용은_응답과_로그에_없다(client, caplog):
    caplog.set_level(logging.ERROR)
    response = client.get("/api/boom")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal", "message": "자동 검토를 완료하지 못해 수동 검토로 넘깁니다."}}
    assert SECRET not in response.text
    assert [r.getMessage() for r in caplog.records if "RuntimeError" in r.getMessage()]  # 타입 이름은 남긴다
    assert SECRET not in caplog.text


# ---------------------------------------------------------------------------
# 역할 Depends
# ---------------------------------------------------------------------------


def test_퍼센트_인코딩된_한글_이름을_푼다(client):
    response = client.get("/api/requester", headers=headers("requester"))
    assert response.status_code == 200 and response.json() == {"role": "requester", "name": "김운영"}


@pytest.mark.parametrize(
    ("path", "role", "allowed"),
    [
        ("/api/requester", "requester", True),
        ("/api/requester", "approver", False),
        ("/api/approver", "approver", True),
        ("/api/approver", "requester", False),
        ("/api/any", "requester", True),
        ("/api/any", "approver", True),
    ],
)
def test_라우트가_허용한_역할만_통과한다(client, path, role, allowed):
    assert (client.get(path, headers=headers(role)).status_code == 200) is allowed


@pytest.mark.parametrize(
    "bad",
    [
        headers(None),  # 역할 없음
        headers("admin"),  # 모르는 역할
        headers("requester", None),  # 이름 없음
        headers("requester", "%20%20"),  # 공백뿐인 이름
        headers("requester", quote("가" * 101)),  # 너무 긴 이름 (헤더는 ASCII라 화면처럼 퍼센트 인코딩해서 보낸다)
        headers("requester", "a%0Ab"),  # 제어 문자
    ],
)
def test_역할이나_이름이_없거나_이상하면_403이다(client, bad):
    response = client.get("/api/any", headers=bad)
    assert response.status_code == 403
    assert response.json() == {"error": {"code": "forbidden", "message": "이 작업을 할 수 있는 권한이 없어요."}}


def test_권한_없는_호출은_감사_로그에_남는다(client, tmp_path):
    client.post("/api/echo", json={})  # 역할 확인이 없는 라우트는 기록하지 않는다
    assert client.get("/api/approver", headers=headers("requester")).status_code == 403
    assert client.get("/api/any?secret=1", headers=headers("admin")).status_code == 403
    records = audit_lines(tmp_path)
    assert [r["event"] for r in records] == ["forbidden", "forbidden"]
    assert records[0] | {"ts": None} == {"ts": None, "event": "forbidden", "role": "requester", "name": "김운영", "method": "GET", "path": "/api/approver"}
    assert (records[1]["role"], records[1]["path"]) == ("admin", "/api/any")  # 쿼리 문자열은 남기지 않는다


def test_감사_로그를_못_써도_403은_그대로_나간다(tmp_path, make_settings):
    app = make_app(tmp_path, make_settings)
    app.state.container.audit = AuditLog(tmp_path)  # 폴더라서 쓰기가 실패한다
    response = TestClient(app, raise_server_exceptions=False).get("/api/any", headers=headers("admin"))
    assert response.status_code == 403


def test_decode_name은_빈_값을_신원_없음으로_본다():
    assert decode_name(None) == "" and decode_name("") == "" and decode_name(KOREAN_NAME) == "김운영"


# ---------------------------------------------------------------------------
# 마스킹 미들웨어
# ---------------------------------------------------------------------------


def test_JSON_본문의_비밀값을_핸들러가_보기_전에_가린다(client):
    payload = {"text": f"배포해 주세요 password={SECRET} 그리고 api_key: sk-abc", "comment": "token=xyz 확인", "n": 3}
    echoed = client.post("/api/echo", json=payload).json()
    assert echoed["text"] == "배포해 주세요 password=**** 그리고 api_key: ****"
    assert echoed["comment"] == "token=**** 확인" and echoed["n"] == 3
    assert SECRET not in json.dumps(echoed, ensure_ascii=False)


def test_AWS_키와_PEM_블록도_가리고_중첩된_값과_목록도_가린다(client):
    pem = "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"
    echoed = client.post("/api/echo", json={"a": {"b": [f"키 AKIAABCDEFGHIJKLMNOP 입니다", pem]}}).json()
    assert echoed == {"a": {"b": ["키 **** 입니다", "****"]}}


def test_키_이름은_가리지_않는다():
    assert mask_json({"password": "password=abc", "n": [1, None, True]}) == {"password": "password=****", "n": [1, None, True]}


def test_본문이_짧아져도_길이와_한글이_맞게_전달된다(client):
    response = client.post("/api/body", json={"text": "한글 password=abcdefghijk"})  # 마스킹으로 길이가 줄어든다
    assert response.status_code == 200 and response.json() == {"text": "한글 password=****"}


def test_JSON이_아니거나_api_경로_밖이면_건드리지_않는다(client):
    other = client.post("/other/echo", json={"text": "password=abc"})
    assert other.json() == {"text": "password=abc"}  # /api/ 밖
    plain = client.post("/api/echo", content=b'{"text": "password=abc"}', headers={"Content-Type": "text/plain"})
    assert plain.status_code == 422  # JSON으로 받는 라우트가 거절한다. 미들웨어는 손대지 않는다


def test_깨진_JSON은_그대로_통과시켜_핸들러가_422로_돌려준다(client):
    response = client.post("/api/body", content=b'{"text": ', headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


def test_너무_큰_본문은_422다(client):
    response = client.post("/api/echo", content=b'{"text": "' + b"a" * MAX_BODY_BYTES + b'"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"
