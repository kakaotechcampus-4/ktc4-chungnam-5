"""공통 응답 래퍼 `{ success, data, error }` 와 예외 핸들러.

성공 응답은 각 엔드포인트가 `response_model=ApiResponse[X]` 를 선언하고 `ok()` 로
감싼다. 미들웨어로 자동 래핑하지 않는다 — OpenAPI 문서가 실제 응답과 어긋나기 때문이다.
"""

import logging
from typing import Generic, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from app.core.errors import ApiError, ErrorCode

T = TypeVar("T")

logger = logging.getLogger(__name__)


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


# HTTPException 의 status_code(< 500) → ErrorCode 명시 매핑. 여기 없는 4xx(405
# method mismatch 등)는 호출부에서 ErrorCode.BAD_REQUEST 로 흡수한다.
_HTTP_STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
}


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

    Status code → ErrorCode 매핑 규칙 (명시 dict, `app/core/response.py` 의
    `_HTTP_STATUS_TO_ERROR_CODE` 가 실제 정의다):
    - 401 (Unauthorized) → ErrorCode.UNAUTHORIZED
    - 403 (Forbidden) → ErrorCode.FORBIDDEN
    - 404 (Not Found) → ErrorCode.NOT_FOUND
    - 409 (Conflict) → ErrorCode.CONFLICT (예: PROFILE_REQUIRED·STAGE_NOT_SET 처럼
      이미 구체적인 code 로 ApiError 를 던지지 않고 HTTPException(409) 를 던진 경우)
    - 422 (Unprocessable Entity) → ErrorCode.VALIDATION_ERROR (요청 본문 필드 오류)
    - 그 외 4xx (401/403/404/409/422 가 아닌 모든 < 500, 405 method mismatch 포함)
      → ErrorCode.BAD_REQUEST
    - 5xx (status_code >= 500) → ErrorCode.INTERNAL_ERROR (서버 에러 전용)

    **중요 규칙: INTERNAL_ERROR 는 5xx 전용이다.** 4xx 는 클라이언트 책임이므로
    BAD_REQUEST/VALIDATION_ERROR/CONFLICT/FORBIDDEN 중 하나로 처리한다. 이를 어기면
    FE 의 에러 분기와 재시도 로직이 깨진다.

    VALIDATION_ERROR 는 "요청 본문 필드가 틀렸다"는 구체적 의미를 가진다. 405 같은
    "메서드 자체가 없다" 류를 여기 섞으면 FE 가 "입력값을 고치라"는 화면을 잘못
    띄운다 — 그래서 405 등은 BAD_REQUEST 로 따로 뺀다.

    이 설계는 FE 가 error.code 로만 분기한다는 점을 기준으로 했다.
    """

    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.code, exc.message, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 필드별 상세는 응답에서 삼킨다. FE 는 code 로 분기하고, 상세를 그대로
        # 내보내면 내부 스키마 구조가 노출된다.
        #
        # 그래도 로그에는 남긴다 — 안 남기면 FE 개발자가 잘못된 페이로드를 보냈을 때
        # 서버 쪽에 아무 단서도 없다. 단, exc.errors() 를 통째로 찍지 않는다:
        # pydantic v2 의 errors()는 "input" 키에 사용자가 보낸 실제 값(체중·키 등
        # 민감 건강정보)을 담고 있어 README 절대 규칙 6(로그에 민감정보를 남기지
        # 않는다) 위반이 된다. 어느 필드가 어떤 종류로 틀렸는지(loc, type)만 남긴다.
        logger.warning(
            "요청 검증 실패: path=%s errors=%s",
            request.url.path,
            [(e["loc"], e["type"]) for e in exc.errors()],
        )
        return _error_response(
            ErrorCode.VALIDATION_ERROR, "요청 형식이 올바르지 않습니다.", 422
        )

    @app.exception_handler(HTTPException)
    async def _handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        """라우트 미스매치(404), 권한 부재(401), 코드에서 직접 raise 한 HTTPException.

        Status code 에 따라 ErrorCode 를 결정한다. 명시 매핑에 없는 4xx 는 전부
        BAD_REQUEST 로 흡수한다(405 등) — "모르는 4xx 는 VALIDATION_ERROR" 처럼
        뭉뚱그리면 FE 가 필드 오류로 오해한다.
        """
        if exc.status_code >= 500:
            code = ErrorCode.INTERNAL_ERROR
        else:
            code = _HTTP_STATUS_TO_ERROR_CODE.get(
                exc.status_code, ErrorCode.BAD_REQUEST
            )

        message = exc.detail if exc.detail else "요청 처리 중 오류가 발생했습니다."
        return _error_response(code, message, exc.status_code)

    @app.exception_handler(Exception)
    async def _handle_generic_exception(_: Request, exc: Exception) -> JSONResponse:
        """처리되지 않은 예외(버그, 프레임워크 에러 등). 항상 500 으로 응답한다."""
        # 예외 내용을 응답에 노출하지 않는다. 내부 구조 정보 유출 방지.
        return _error_response(
            ErrorCode.INTERNAL_ERROR, "서버 오류가 발생했습니다.", 500
        )
