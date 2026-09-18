"""API 에러 코드와 예외.

FE 는 HTTP status 가 아니라 `error.code` 로 분기한다. 그래서 status 는 예외가
함께 들고 다니고, 분기의 진실은 code 다.

**진실의 출처는 리포 밖의 API 명세서**이고, 이 enum 은 그걸 코드 쪽에 옮겨둔
것이다. 둘이 어긋나면 `app/tests/test_error_code_contract.py` 가 깨진다.
코드를 추가·변경하면 FE 가 분기를 고쳐야 하므로 합의 없이 건드리지 않는다.
"""

import enum


class ErrorCode(str, enum.Enum):
    # 온보딩 선행조건 미충족 — 도메인 코드 (409)
    PROFILE_REQUIRED = "PROFILE_REQUIRED"
    STAGE_NOT_SET = "STAGE_NOT_SET"
    NOT_CONFIRMED = "NOT_CONFIRMED"

    # 식사 분석 실패 — 도메인 코드
    FOOD_NOT_RECOGNIZED = "FOOD_NOT_RECOGNIZED"  # 422
    
    # 504 다. `_HTTP_STATUS_TO_ERROR_CODE` 의 "5xx → INTERNAL_ERROR" 규칙은 맨
    # HTTPException 에만 적용된다 — 이 코드는 ApiError 로 status 를 직접 들고
    # 나가므로 그 규칙에 걸리지 않는다. INTERNAL_ERROR 로 바꾸지 말 것.
    ANALYSIS_TIMEOUT = "ANALYSIS_TIMEOUT"

    # 결과는 있는데 단서가 붙는 경우 — HTTP 200 으로 나간다.
    # 실패가 아니므로 ApiError 로 던지지 않는다. `ok_with_code()` 로 data 와
    # 함께 실어 보낸다 — 던지면 success=false·data=null 이 되어 결과가 사라진다.
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NUTRITION_NOT_MATCHED = "NUTRITION_NOT_MATCHED"
    MEDICAL_QUESTION_DETECTED = "MEDICAL_QUESTION_DETECTED"
    FEEDBACK_GENERATION_FAILED = "FEEDBACK_GENERATION_FAILED"

    # 인증 · 검증 · 서버 오류 — 전 엔드포인트 공통
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    """핸들러가 응답 래퍼로 바꿔 내보내는 도메인 예외."""

    def __init__(self, code: ErrorCode, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
