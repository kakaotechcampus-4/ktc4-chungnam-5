"""공통 응답 래퍼 `{ success, data, error }` 와 예외 핸들러.

성공 응답은 각 엔드포인트가 `response_model=ApiResponse[X]` 를 선언하고 `ok()` 로
감싼다. 미들웨어로 자동 래핑하지 않는다 — OpenAPI 문서가 실제 응답과 어긋나기 때문이다.
"""

from typing import Generic, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from app.core.errors import ApiError, ErrorCode

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: ErrorBody | None = None


def ok(data: T) -> ApiResponse[T]:
    # ApiResponse[T] 로 subscript 하지 않는다 — T 가 바인딩되지 않은 TypeVar 라
    # 런타임에 의미가 없다. 실제 직렬화는 라우트의 response_model 이 한다.
    return ApiResponse(success=True, data=data, error=None)


def _error_response(code: ErrorCode, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={
            "success": False,
            "data": None,
            "error": {"code": code.value, "message": message},
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """앱에 예외 → 응답 래퍼 변환을 붙인다. main.py 에서 한 번 부른다.

    모든 응답이 { success, data, error } 래퍼를 갖도록 보장한다. HTTP status 는
    error.code 의 맥락이고, 분기의 진실은 code 다 (contracts/API.md 규약).

    Status code → ErrorCode 매핑 규칙:
    - 401 (Unauthorized) → ErrorCode.UNAUTHORIZED
    - 404 (Not Found) → ErrorCode.NOT_FOUND
    - 4xx (status_code < 500) → ErrorCode.VALIDATION_ERROR (잘못된 요청 일반)
    - 5xx (status_code >= 500) → ErrorCode.INTERNAL_ERROR (서버 에러 전용)

    **중요 규칙: INTERNAL_ERROR 는 5xx 전용이다.** 4xx 는 클라이언트 책임이므로
    VALIDATION_ERROR 로 처리한다. 이를 어기면 FE 의 에러 분기와 재시도 로직이 깨진다.

    이 설계는 FE 가 error.code 로만 분기한다는 점을 기준으로 했다.
    """

    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.code, exc.message, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 필드별 상세는 삼킨다. FE 는 code 로 분기하고, 상세를 그대로 내보내면
        # 내부 스키마 구조가 노출된다.
        return _error_response(
            ErrorCode.VALIDATION_ERROR, "요청 형식이 올바르지 않습니다.", 422
        )

    @app.exception_handler(HTTPException)
    async def _handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        """라우트 미스매치(404), 권한 부재(401), 코드에서 직접 raise 한 HTTPException.

        Status code 에 따라 ErrorCode 를 결정한다.
        """
        if exc.status_code == 401:
            code = ErrorCode.UNAUTHORIZED
        elif exc.status_code == 404:
            code = ErrorCode.NOT_FOUND
        elif exc.status_code < 500:
            # 4xx 는 클라이언트 오류 (잘못된 요청, 잘못된 입력, 405 method mismatch 등)
            code = ErrorCode.VALIDATION_ERROR
        else:
            # 5xx 는 서버 오류
            code = ErrorCode.INTERNAL_ERROR

        message = exc.detail if exc.detail else "요청 처리 중 오류가 발생했습니다."
        return _error_response(code, message, exc.status_code)

    @app.exception_handler(Exception)
    async def _handle_generic_exception(_: Request, exc: Exception) -> JSONResponse:
        """처리되지 않은 예외(버그, 프레임워크 에러 등). 항상 500 으로 응답한다."""
        # 예외 내용을 응답에 노출하지 않는다. 내부 구조 정보 유출 방지.
        return _error_response(
            ErrorCode.INTERNAL_ERROR, "서버 오류가 발생했습니다.", 500
        )
