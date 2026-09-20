"""요청이 바깥 세계와 만나는 이음새들 — 사용자 식별과 작업 큐.

둘 다 "테스트에서 갈아끼울 수 있어야 하는 의존성" 이라 여기 모여 있다.
아래 설명은 그중 인증 이음새에 대한 것이다.

## 인증 이음새

지금은 X-User-Id 헤더를 그대로 믿는다. **인증이 아니다.** 타인의 UUID 를 헤더에
넣으면 그 사용자의 프로필(민감 건강정보)을 읽고 쓸 수 있다 — 그래서 APP_ENV 가
production 이면 아래 get_current_user_id 가 기동 자체를 막는다 (README "절대
규칙 6" 예외 블록 참고).

JWT 가 붙으면 이 파일의 get_current_user_id 본문이 바뀌고, **이 의존성을 쓰는**
`GET`/`PATCH /users/me` 와 `GET`/`DELETE /meals` 는 바뀌지 않는다. 하지만
`POST /users/profile` 은 이 의존성을 **쓰지 않는다** — 프로필 생성이 곧 유저
생성이라 식별할 기존 사용자가 없기 때문이다. JWT 가 붙으면 "프로필 생성" 의 의미 자체가 바뀐다(카카오 콜백이
먼저 유저 행을 만들고, 이 엔드포인트는 그 행을 채우는 걸로 바뀐다) — 그래서 이
엔드포인트·`services/user.py::create_profile`·crud·테스트가 함께 바뀐다. 다음
사람이 JWT 작업량을 "이 파일만 고치면 된다" 로 과소산정하지 않도록 남겨둔다.
"""

import uuid
from functools import lru_cache

from fastapi import Header

from app.core.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.infra.queue import TaskQueue, build_task_queue


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


@lru_cache
def _task_queue() -> TaskQueue:
    """프로세스당 하나. boto3 클라이언트 생성이 요청마다 일어나면 커넥션이 낭비된다."""
    return build_task_queue()


def get_task_queue() -> TaskQueue:
    """비동기 작업 큐 — 테스트가 갈아끼우는 이음새.

    엔드포인트가 `build_task_queue()` 를 직접 부르면 테스트에서 진짜 SQS 설정이
    필요해진다. 의존성으로 받아야 `app.dependency_overrides` 로 대체할 수 있다.
    """
    return _task_queue()
