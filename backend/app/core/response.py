"""공통 응답 래퍼 { success, data, error } 와 예외 핸들러.

성공 응답은 각 엔드포인트가 response_model=ApiResponse[X] 를 선언하고 ok() 로
감싼다. 명세서에서 HTTP 200 인 도메인 code(LOW_CONFIDENCE 등)는 ok_with_code() 로
감싸 success=true 와 error 가 함께 나간다 — error 가 있다고 실패가 아니다.
미들웨어로 자동 래핑하지 않는다 — OpenAPI 문서가 실제 응답과 어긋나기 때문이다.
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
    # str 이 아니라 ErrorCode 다 — 그래야 가능한 코드 목록이 /openapi.json 과
    # /docs 스키마에 전부 열거된다. FE 는 여기서 분기 대상을 확인한다. 직렬화 결과는 동일하다 (ErrorCode 가 str enum).
    code: ErrorCode
    message: str


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: ErrorBody | None = None


class ErrorResponse(BaseModel):
    """에러 응답 전용 스키마. OpenAPI 문서에만 쓰인다.

    실제 직렬화는 `_error_response` 가 dict 로 한다 — 이 모델은 그 dict 와 같은
    모양을 문서에 약속하기 위한 것이다. 둘이 어긋나면 문서가 거짓말이 된다.
    """

    success: bool = False
    data: None = None
    error: ErrorBody


# `responses=` 의 description. status 는 error.code 의 맥락이므로 그 status 로
# 나갈 수 있는 code 를 전부 적는다 — 분기의 진실은 code 다.
#
# 각 줄은 "전송계층 공통 code · 명세서의 도메인 code" 순이다. 명세서(리포 밖)에
# 적힌 도메인 code 가 빠지면 `test_error_code_contract.py` 가 깨진다 — 여기 문구가
# 곧 /docs 에 보이는 설명이라, 빠지면 FE 가 그 code 를 찾지 못한다.
_ERROR_DESCRIPTIONS: dict[int, str] = {
    400: "잘못된 요청 — BAD_REQUEST",
    401: "인증 실패 — UNAUTHORIZED",
    403: "권한 없음 — FORBIDDEN",
    404: "대상을 찾을 수 없음 — NOT_FOUND · USER_NOT_FOUND",
    409: "상태 충돌 — CONFLICT · PROFILE_REQUIRED · STAGE_NOT_SET · NOT_CONFIRMED",
    422: "요청 검증 실패 — VALIDATION_ERROR · FOOD_NOT_RECOGNIZED",
    500: "서버 오류 — INTERNAL_ERROR",
    504: "AI 분석 시간 초과 — ANALYSIS_TIMEOUT",
}


def error_responses(*statuses: int) -> dict[int | str, dict]:
    """라우트 데코레이터의 `responses=` 에 넘길 에러 응답 선언을 만든다.

    FastAPI 는 데코레이터에 선언된 것만 OpenAPI 에 넣는다. `raise HTTPException`
    은 실행 시점의 동작이라 문서에 잡히지 않으므로, 날 수 있는 status 를 여기서
    직접 적어준다.
    """
    return {
        status: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[status]}
        for status in statuses
    }


def ok(data: T) -> ApiResponse[T]:
    # ApiResponse[T] 로 subscript 하지 않는다 — T 가 바인딩되지 않은 TypeVar 라
    # 런타임에 의미가 없다. 실제 직렬화는 라우트의 response_model 이 한다.
    return ApiResponse(success=True, data=data, error=None)


def ok_with_code(data: T, code: ErrorCode, message: str) -> ApiResponse[T]:
    """성공 응답에 도메인 코드를 함께 싣는다 (HTTP 200).

    명세서에서 HTTP 200 인 코드들(LOW_CONFIDENCE · NUTRITION_NOT_MATCHED ·
    MEDICAL_QUESTION_DETECTED · FEEDBACK_GENERATION_FAILED)이 여기로 나간다.
    "결과는 있는데 단서가 붙는다" 는 뜻이라 `data` 가 살아 있어야 한다.

    `success` 는 참이다 — 요청 자체는 성공했다. `error` 가 차 있는 건 FE 의
    분기점을 `error.code` 하나로 유지하기 위해서다(`core/errors.py` 의 전제).
    실패를 뜻하는 `_error_response` 경로와 혼동하지 말 것.
    """
    return ApiResponse(
        success=True, data=data, error=ErrorBody(code=code, message=message)
    )


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
    error.code 의 맥락이고, 분기의 진실은 code 다.

    Status code → ErrorCode 매핑은 `_HTTP_STATUS_TO_ERROR_CODE` 가 실제 정의다
    (거기 없는 4xx 는 BAD_REQUEST, 5xx 는 INTERNAL_ERROR). 여기 다시 적지
    않는다 — 코드가 늘면 두 곳이 어긋난다.

    **주의: `_ERROR_DESCRIPTIONS` 와 헷갈리지 말 것.** 이 dict 는 code 가 없는
    HTTPException 에 code 를 채워주는 표다 — 런타임 동작이다. 저쪽은 /docs 에
    띄울 설명 문자열이라 응답 생성에 관여하지 않는다.

        HTTPException(504)              → INTERNAL_ERROR   (이 표 + 5xx 규칙)
        ApiError(ANALYSIS_TIMEOUT, 504) → ANALYSIS_TIMEOUT (code 를 들고 온다)

    같은 504 라도 던지는 방법에 따라 다르다. 그래서 두 dict 를 합치거나 한쪽이
    다른 쪽을 가리키게 하면 안 된다.

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
