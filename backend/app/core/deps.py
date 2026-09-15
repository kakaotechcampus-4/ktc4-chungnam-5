"""요청에서 사용자를 식별한다 — 인증 이음새.

지금은 X-User-Id 헤더를 그대로 믿는다. **인증이 아니다.** JWT 가 붙으면 이 파일의
get_current_user_id 본문만 바뀌고, 이 의존성을 쓰는 라우트는 하나도 바뀌지 않는다.
api/v1/endpoints/meals.py 의 user_id 쿼리 파라미터도 나중에 여기로 옮긴다.
"""

import uuid

from fastapi import Header

from app.core.errors import ApiError, ErrorCode


def get_current_user_id(x_user_id: str | None = Header(default=None)) -> uuid.UUID:
    if x_user_id is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "X-User-Id 헤더가 필요합니다.", 401)
    try:
        return uuid.UUID(x_user_id)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.UNAUTHORIZED, "X-User-Id 형식이 올바르지 않습니다.", 401
        ) from exc
