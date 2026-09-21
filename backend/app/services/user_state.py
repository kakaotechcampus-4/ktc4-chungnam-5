"""컨디션 기록(user_states) 도메인 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 커밋은 응답을 만들어 return 하기 직전에
여기서 한다. 다른 services/ 모듈을 import 하지 않는다.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.schemas.user_state import UserStateCreateRequest, UserStateResponse


def create_user_state(
    db: Session, *, user_id: uuid.UUID, request: UserStateCreateRequest
) -> UserStateResponse:
    """컨디션 기록 1건을 저장하고 지난주 대비 체중 변화량을 붙여 돌려준다."""
    raise NotImplementedError("create_user_state 미구현")
