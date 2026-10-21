"""마스킹 미들웨어 (CLAUDE.md 절대 규칙 5, docs/SPEC.md 6장).

요청 본문의 비밀값(password=, secret, token, AWS 키, PEM 블록 등)을 핸들러가 보기 전에 ****로 바꾼다. 원문이 핸들러,
그래프 상태, LangSmith, 감사 로그 어디에도 도달하지 않으므로 서버 안에는 마스킹된 본문만 있다(상태에 raw_text를 두지 않는
이유다). 치환 규칙은 app/masking.py의 순수 함수를 그대로 쓴다.

대상은 /api/ 아래의 본문이 있는 JSON 요청이다. JSON이 아니거나 파싱이 안 되면 그대로 두고, 파싱 실패는 핸들러가 422로
돌려준다. 문자열 값만 가리고 키 이름은 건드리지 않는다.
"""

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import VALIDATION_MESSAGE, error_response
from app.masking import mask_secrets

MAX_BODY_BYTES = 64 * 1024
_BODY_METHODS = {"POST", "PUT", "PATCH"}


def mask_json(value: Any) -> Any:
    """JSON 값 안의 모든 문자열을 마스킹한다. 목록과 객체는 안으로 들어가고 키는 그대로 둔다."""
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, list):
        return [mask_json(item) for item in value]
    if isinstance(value, dict):
        return {key: mask_json(item) for key, item in value.items()}
    return value


class MaskingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return

        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > MAX_BODY_BYTES:
                await error_response(422, "validation_error", VALIDATION_MESSAGE)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        masked = self._mask(body)
        headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(masked)).encode()))
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": masked, "more_body": False}
            return await receive()  # 본문 뒤에는 연결 끊김 같은 원래 메시지를 그대로 전달한다

        await self.app({**scope, "headers": headers}, replay, send)

    @staticmethod
    def _applies(scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] not in _BODY_METHODS or not scope["path"].startswith("/api/"):
            return False
        content_type = dict(scope["headers"]).get(b"content-type", b"").decode("latin-1").lower()
        return content_type.startswith("application/json")

    @staticmethod
    def _mask(body: bytes) -> bytes:
        try:
            parsed = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return body  # 파싱이 안 되면 그대로 둔다. 핸들러가 422로 돌려준다
        return json.dumps(mask_json(parsed), ensure_ascii=False).encode("utf-8")
