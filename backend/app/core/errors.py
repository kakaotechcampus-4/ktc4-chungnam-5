"""API 에러 코드와 예외.

FE 는 HTTP status 가 아니라 `error.code` 로 분기한다. 그래서 status 는 예외가
함께 들고 다니고, 분기의 진실은 code 다.

별도의 API 명세 파일이 없으므로 **이 enum 이 에러 코드의 진실의 출처**다.
코드를 추가·변경하면 FE 가 분기를 고쳐야 하므로 합의 없이 건드리지 않는다.
"""

import enum


class ErrorCode(str, enum.Enum):
    # 온보딩 선행조건 미충족 — 도메인 코드
    PROFILE_REQUIRED = "PROFILE_REQUIRED"
    STAGE_NOT_SET = "STAGE_NOT_SET"

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
