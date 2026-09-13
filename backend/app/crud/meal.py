"""`meals` · `meal_items` 접근.

`services/` 는 세션을 직접 다루지 않는다(규칙 5). DB 를 만지는 건 여기뿐이다.
커밋은 호출하는 쪽(worker · api)이 한다 — 한 작업이 여러 crud 를 묶어 쓰기 때문이다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import MealItemSource, MealStatus
from app.models.meal import Meal, MealItem


def get(db: Session, meal_id: uuid.UUID | str) -> Meal | None:
    return db.get(Meal, meal_id)


def set_status(db: Session, meal: Meal, status: MealStatus) -> None:
    meal.status = status
    db.add(meal)


def add_item(
    db: Session,
    meal: Meal,
    *,
    original_food_name: str,
    estimated_amount_g: Decimal | None,
    confidence: Decimal | None,
    food_ref_id: str | None = None,
    raw_ai_result: dict[str, Any] | None = None,
) -> MealItem:
    """인식 결과 1건을 저장한다.

    `display_name` 은 최초에 `original_food_name` 을 복사한다 — 사용자가 고치면
    그때 갱신된다. `confirmed_amount_g` 는 사용자 확인 전이라 NULL 로 둔다.
    """
    item = MealItem(
        meal_id=meal.id,
        food_ref_id=food_ref_id,
        original_food_name=original_food_name,
        display_name=original_food_name,
        estimated_amount_g=estimated_amount_g,
        confidence=confidence,
        source=MealItemSource.MODEL,
        raw_ai_result=raw_ai_result,
    )
    db.add(item)
    return item


def delete_model_items(db: Session, meal: Meal) -> int:
    """AI 가 만든 항목만 지운다. 재분석 때 쓴다.

    사용자가 직접 추가한 항목(`source=USER`)은 남긴다 — 재분석은 AI 인식을 다시
    하는 것이지 사용자 입력을 되돌리는 게 아니다.
    """
    removed = [item for item in meal.items if item.source is MealItemSource.MODEL]
    for item in removed:
        db.delete(item)
    return len(removed)
