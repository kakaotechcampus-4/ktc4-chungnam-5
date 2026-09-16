"""요청에서 사용자를 식별한다 — 인증 이음새.

지금은 X-User-Id 헤더를 그대로 믿는다. **인증이 아니다.** 타인의 UUID 를 헤더에
넣으면 그 사용자의 프로필(민감 건강정보)을 읽고 쓸 수 있다 — 그래서 APP_ENV 가
production 이면 아래 get_current_user_id 가 기동 자체를 막는다 (README "절대
규칙 6" 예외 블록 참고).

JWT 가 붙으면 이 파일의 get_current_user_id 본문이 바뀌고, **이 의존성을 쓰는**
`GET`/`PATCH /users/me` 는 바뀌지 않는다. 하지만 `POST /users/profile` 은 이
의존성을 **쓰지 않는다** — 프로필 생성이 곧 유저 생성이라 식별할 기존 사용자가
없기 때문이다. JWT 가 붙으면 "프로필 생성" 의 의미 자체가 바뀐다(카카오 콜백이
먼저 유저 행을 만들고, 이 엔드포인트는 그 행을 채우는 걸로 바뀐다) — 그래서 이
엔드포인트·`services/user.py::create_profile`·crud·테스트가 함께 바뀐다. 다음
사람이 JWT 작업량을 "이 파일만 고치면 된다" 로 과소산정하지 않도록 남겨둔다.

api/v1/endpoints/meals.py 의 user_id 쿼리 파라미터도 나중에 여기로 옮긴다.
"""

import uuid

from fastapi import Header

from app.core.config import get_settings
from app.core.errors import ApiError, ErrorCode


def get_current_user_id(x_user_id: str | None = Header(default=None)) -> uuid.UUID:
    if get_settings().APP_ENV == "production":
        # X-User-Id 는 인증이 아니다 — 조용히 동작하는 것보다 기동/요청 시점에
        # 명확히 죽는 게 낫다 (core/config.py 의 설정 철학과 동일).
        raise RuntimeError(
            "APP_ENV=production 에서 X-User-Id 인증 이음새를 쓸 수 없습니다. "
            "JWT 가 병합되기 전까지 /users/* 를 production 에 배포하지 마세요."
        )
    if x_user_id is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "X-User-Id 헤더가 필요합니다.", 401)
    try:
        return uuid.UUID(x_user_id)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.UNAUTHORIZED, "X-User-Id 형식이 올바르지 않습니다.", 401
        ) from exc
