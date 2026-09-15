"""식사(meal) 도메인 로직.

DB 세션은 직접 다루지 않고 `crud/` 를 통해서만 접근한다. 상태는 `crud/` 가 들고,
조합은 `worker/` · `api/` 가 한다.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.crud import meal as meal_crud
from app.schemas.meal import MealListItem, MealListResponse, MealScores

# 그대로 g 으로 볼 수 있는 단위.
# ml 은 물 기준 1ml ≈ 1g 로 근사한다. 국·음료가 대부분이라 오차를 감수할 만하다.
_GRAM_EQUIVALENT_UNITS = {"g", "G", "그램", "ml", "mL", "ML", "밀리리터"}

_CURSOR_SEPARATOR = "|"


def to_grams(amount: float | Decimal | None, unit: str | None) -> Decimal | None:
    """AI 가 준 `amount` + `unit` 을 g 으로 옮긴다. 옮길 수 없으면 None.

    **환산표가 없다.** `food_refs.serving_size` 는 "영양성분함량기준량"(성분값이
    어느 양 기준인지, 보통 100g)이지 "1개 = 50g" 이 아니다. 즉 "계란 2개" 를 g 으로
    바꿀 근거가 DB 에 없다.

    없는 근거를 지어내지 않는다. 지어낸 g 으로 Q/Q/S 를 채점하면 점수가 조용히
    틀리고, 사용자는 어디서 틀렸는지 알 수 없다. 대신 None 을 돌려주고
    원본 단위는 `raw_ai_result` 에 남긴다 — 사용자가 `REVIEW_REQUIRED` 단계에서
    실제 양을 확인해 `confirmed_amount_g` 를 채우는 것이 이 상태의 존재 이유다.
    """
    if amount is None or unit is None:
        return None
    if unit.strip() not in _GRAM_EQUIVALENT_UNITS:
        return None

    value = Decimal(str(amount))
    if value < 0:
        return None
    # 컬럼이 Numeric(8, 2) 다
    return value.quantize(Decimal("0.01"))


def _build_scores(
    quantity: Decimal | None,
    quality: Decimal | None,
    satiety: Decimal | None,
) -> MealScores | None:
    """세 점수가 전부 없으면(아직 미평가) None, 하나라도 있으면 객체로 감싼다."""
    if quantity is None and quality is None and satiety is None:
        return None
    return MealScores(
        quantity=round(quantity) if quantity is not None else None,
        quality=round(quality) if quality is not None else None,
        satiety=round(satiety) if satiety is not None else None,
    )


def _build_thumbnail_url(image_key: str | None) -> str | None:
    """image_key 를 실제 접근 가능한 URL 로 바꾼다.

    TODO: 6번(POST /meals)에서 infra/ FileStorage 붙이면 presigned URL 로 교체.
    """
    if image_key is None:
        return None
    return f"/media/{image_key}"


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

    rows = meal_crud.list_meals(
        db,
        user_id=user_id,
        limit=limit + 1,  # 1개 더 가져와서 "다음 페이지 있음"을 판단한다
        before_eaten_at=before_eaten_at,
        before_id=before_id,
    )

    has_more = len(rows) > limit
    rows = rows[:limit]  # 판단용으로 더 가져온 1개는 응답에서 잘라낸다

    meal_ids = [meal.id for meal, *_ in rows]
    display_names = meal_crud.get_display_names(db, meal_ids)

    items = [
        MealListItem(
            meal_id=meal.id,
            meal_type=meal.meal_type,
            eaten_at=meal.eaten_at,
            stage=stage,
            display_name=display_names.get(meal.id, ""),
            thumbnail_url=_build_thumbnail_url(meal.image_key),
            scores=_build_scores(quantity, quality, satiety),
        )
        for meal, stage, quantity, quality, satiety in rows
    ]

    next_cursor = None
    if has_more and rows:
        last_meal = rows[-1][0]
        next_cursor = encode_cursor(last_meal.eaten_at, last_meal.id)

    return MealListResponse(items=items, next_cursor=next_cursor, has_more=has_more)
