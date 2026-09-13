"""`meal_items` · `user_corrections` 접근.

수정 이력은 항목과 한 몸이라 같은 파일에 둔다 — `user_corrections` 를 따로 다룰 일이 없다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import MealItemSource
from app.models.meal import Meal, MealItem, UserCorrection


def get(db: Session, item_id: uuid.UUID | str) -> MealItem | None:
    return db.get(MealItem, item_id)


def add(
    db: Session,
    meal: Meal,
    *,
    original_food_name: str,
    estimated_amount_g: Decimal | None,
    confidence: Decimal | None,
    food_ref_id: str | None = None,
    raw_ai_result: dict[str, Any] | None = None,
) -> MealItem:
    """AI 인식 결과 1건.

    `display_name` 은 최초에 `original_food_name` 을 복사한다 — 사용자가 고치면 갱신된다.
    `confirmed_amount_g` 는 사용자 확인 전이라 NULL 이다.
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


def add_by_user(
    db: Session,
    meal: Meal,
    *,
    display_name: str,
    confirmed_amount_g: Decimal | None = None,
    food_ref_id: str | None = None,
) -> MealItem:
    """사용자가 직접 추가한 항목. 재분석 때 지우지 않는다."""
    item = MealItem(
        meal_id=meal.id,
        food_ref_id=food_ref_id,
        original_food_name=display_name,
        display_name=display_name,
        confirmed_amount_g=confirmed_amount_g,
        source=MealItemSource.USER,
    )
    db.add(item)
    return item


def confirm(
    db: Session,
    item: MealItem,
    *,
    display_name: str | None = None,
    confirmed_amount_g: Decimal | None = None,
) -> UserCorrection | None:
    """사용자가 인식 결과를 확인·수정한다. 바뀐 게 없으면 None.

    **값이 실제로 바뀐 때만** `user_corrections` 에 남긴다. 확인만 하고 그대로 둔 것을
    '수정' 으로 기록하면 AI 성능 지표가 왜곡된다.

    `source` 는 MODEL 로 둔다 — 이 항목을 처음 만든 건 AI 다.
    """
    before = {"displayName": item.display_name, "confirmedAmountG": _num(item.confirmed_amount_g)}

    if display_name is not None:
        item.display_name = display_name
    if confirmed_amount_g is not None:
        item.confirmed_amount_g = confirmed_amount_g
    db.add(item)

    after = {"displayName": item.display_name, "confirmedAmountG": _num(item.confirmed_amount_g)}
    if before == after:
        return None

    correction = UserCorrection(meal_item_id=item.id, original_value=before, corrected_value=after)
    db.add(correction)
    return correction


def delete_model_items(db: Session, meal: Meal) -> int:
    """AI 가 만든 항목만 지운다. 재분석 때 쓴다.

    사용자가 직접 추가한 항목(`source=USER`)은 남긴다 — 재분석은 AI 인식을 다시
    하는 것이지 사용자 입력을 되돌리는 게 아니다.
    """
    removed = [item for item in meal.items if item.source is MealItemSource.MODEL]
    for item in removed:
        db.delete(item)

    # 지운 뒤에도 meal.items 는 로드된 옛 목록을 들고 있다. 만료시켜 두지 않으면
    # 같은 세션에서 이어 읽는 쪽이 이미 사라진 항목을 본다.
    db.expire(meal, ["items"])
    return len(removed)


def _num(value: Decimal | None) -> float | None:
    """JSONB 에 넣을 수 있게 바꾼다. Decimal 은 직렬화되지 않는다."""
    return None if value is None else float(value)
