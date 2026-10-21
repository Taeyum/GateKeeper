"""API 오류 응답 (docs/API_CONTRACT.md "오류 응답").

모든 오류는 {"error": {"code", "message"}} 한 모양이다. message는 내부 정보(경로, 스택, 요청 값)를 뺀 사용자용 문장이다.
FastAPI의 기본 422는 입력값을 그대로 되돌려 주므로 쓰지 않는다. 요청 본문의 비밀값이 오류 응답으로 새지 않게 하려는 것이다
(마스킹 미들웨어가 앞에서 가리지만 오류 경로가 마지막 방어선까지 막아 준다).
"""

import logging
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

ErrorCode = Literal["forbidden", "not_found", "conflict", "validation_error", "internal"]

FORBIDDEN_MESSAGE = "이 작업을 할 수 있는 권한이 없어요."
NOT_FOUND_MESSAGE = "요청을 찾을 수 없어요."
CONFLICT_MESSAGE = "지금은 이 작업을 할 수 없는 상태예요."
VALIDATION_MESSAGE = "입력 내용을 확인해 주세요."
INTERNAL_MESSAGE = "자동 검토를 완료하지 못해 수동 검토로 넘깁니다."

# 라우팅 단계에서 FastAPI가 내는 HTTP 오류를 계약의 코드 다섯 개로 바꾼다.
_CODE_OF_STATUS: dict[int, ErrorCode] = {
    400: "validation_error",
    401: "forbidden",
    403: "forbidden",
    404: "not_found",
    405: "validation_error",
    409: "conflict",
    413: "validation_error",
    415: "validation_error",
    422: "validation_error",
}
_MESSAGE_OF_CODE: dict[ErrorCode, str] = {
    "forbidden": FORBIDDEN_MESSAGE,
    "not_found": NOT_FOUND_MESSAGE,
    "conflict": CONFLICT_MESSAGE,
    "validation_error": VALIDATION_MESSAGE,
    "internal": INTERNAL_MESSAGE,
}


class ApiError(Exception):
    """핸들러가 올리는 계약상의 오류. status_code와 code는 계약 표를 따른다."""

    def __init__(self, status_code: int, code: ErrorCode, message: str | None = None) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message or _MESSAGE_OF_CODE[code]


def forbidden() -> ApiError:
    return ApiError(403, "forbidden")


def not_found(message: str | None = None) -> ApiError:
    return ApiError(404, "not_found", message)


def conflict(message: str | None = None) -> ApiError:
    return ApiError(409, "conflict", message)


def validation_error(message: str | None = None) -> ApiError:
    return ApiError(422, "validation_error", message)


def error_response(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # exc.errors()에는 입력값(input)이 들어 있어서 응답에도 로그에도 싣지 않는다
        return error_response(422, "validation_error", VALIDATION_MESSAGE)

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODE_OF_STATUS.get(exc.status_code)
        if code is None:
            return error_response(500, "internal", INTERNAL_MESSAGE)
        return error_response(exc.status_code, code, _MESSAGE_OF_CODE[code])

    @app.exception_handler(Exception)
    async def on_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 예외 메시지에는 요청에서 온 값이 들어 있을 수 있어서 타입 이름만 남긴다
        logger.error("처리하지 못한 오류 (%s): %s %s", type(exc).__name__, request.method, request.url.path)
        return error_response(500, "internal", INTERNAL_MESSAGE)
