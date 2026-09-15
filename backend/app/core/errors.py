"""API 에러 코드와 예외.

FE 는 HTTP status 가 아니라 `error.code` 로 분기한다 (contracts/API.md 규약).
그래서 status 는 예외가 함께 들고 다니고, 분기의 진실은 code 다.

`UNAUTHORIZED` · `USER_NOT_FOUND` · `NOT_FOUND` · `VALIDATION_ERROR` · `INTERNAL_ERROR` 는
contracts/API.md 의 에러 코드 표에 없다. 엔드포인트를 구현하는 데 필요해서
여기서 정의했고, 명세에 역반영이 필요하다.
"""

import enum


class ErrorCode(str, enum.Enum):
    # contracts/API.md 에 있는 것
    PROFILE_REQUIRED = "PROFILE_REQUIRED"
    STAGE_NOT_SET = "STAGE_NOT_SET"

    # 이번 작업에서 새로 정의한 것
    UNAUTHORIZED = "UNAUTHORIZED"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    """핸들러가 응답 래퍼로 바꿔 내보내는 도메인 예외."""

    def __init__(self, code: ErrorCode, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
