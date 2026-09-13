"""식사(meal) 관련 비즈니스 로직. DB 세션은 직접 다루지 않고 crud 를 통해서만 접근한다."""

import base64
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.crud import meal as meal_crud
from app.schemas.meal import MealListItem, MealListResponse

_CURSOR_SEPARATOR = "|"


def encode_cursor(eaten_at: datetime, meal_id: uuid.UUID) -> str:
    """(eaten_at, id) 를 클라이언트가 그대로 들고 있다가 돌려줄 불투명한 문자열로 감싼다."""
    raw = f"{eaten_at.isoformat()}{_CURSOR_SEPARATOR}{meal_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """encode_cursor 로 만든 문자열을 (eaten_at, id) 로 되돌린다.

    클라이언트가 조작했거나 잘못된 cursor 를 보내면 ValueError.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        eaten_at_str, meal_id_str = raw.split(_CURSOR_SEPARATOR)
        return datetime.fromisoformat(eaten_at_str), uuid.UUID(meal_id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("잘못된 cursor 입니다.") from exc


def list_meals(
    db: Session,
    *,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int,
) -> MealListResponse:
    """cursor 이후의 식사를 최신순으로 limit 개 조회해 응답 스키마로 조립한다."""
    before_eaten_at, before_id = (None, None)
    if cursor is not None:
        before_eaten_at, before_id = decode_cursor(cursor)

    meals = meal_crud.list_meals(
        db,
        user_id=user_id,
        limit=limit + 1,  # 1개 더 가져와서 "다음 페이지 있음"을 판단한다
        before_eaten_at=before_eaten_at,
        before_id=before_id,
    )

    has_more = len(meals) > limit
    meals = meals[:limit]  # 판단용으로 더 가져온 1개는 응답에서 잘라낸다

    next_cursor = None
    if has_more and meals:
        last = meals[-1]
        next_cursor = encode_cursor(last.eaten_at, last.id)

    items = [MealListItem.model_validate(meal) for meal in meals]
    return MealListResponse(items=items, next_cursor=next_cursor, has_more=has_more)
