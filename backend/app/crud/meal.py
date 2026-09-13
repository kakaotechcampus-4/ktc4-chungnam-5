"""`meals` · `meal_items` 접근.

`services/` 는 세션을 직접 다루지 않는다(규칙 5). DB 를 만지는 건 여기뿐이다.
커밋은 호출하는 쪽(worker · api)이 한다 — 한 작업이 여러 crud 를 묶어 쓰기 때문이다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MealItemSource, MealStatus
from app.models.meal import Meal, MealItem, SatietyLog, UserCorrection


def get(db: Session, meal_id: uuid.UUID | str) -> Meal | None:
    return db.get(Meal, meal_id)


def list_for_user(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    limit: int | None = None,
) -> list[Meal]:
    """기간 식사 목록. 홈 화면과 기간 조회가 같은 인덱스를 탄다."""
    stmt = select(Meal).where(Meal.user_id == user_id)
    if since is not None:
        stmt = stmt.where(Meal.eaten_at >= since)
    if until is not None:
        stmt = stmt.where(Meal.eaten_at <= until)
    stmt = stmt.order_by(Meal.eaten_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def list_recent(db: Session, user_id: uuid.UUID | str, *, limit: int = 5) -> list[Meal]:
    """최근 식사. AI 의 `get_recent_meals` tool 이 /internal/v1 로 되물을 때 쓴다."""
    return list_for_user(db, user_id, limit=limit)


def create(
    db: Session,
    *,
    user_id: uuid.UUID | str,
    medication_snapshot_id: uuid.UUID | str,
    meal_type,
    eaten_at: dt.datetime,
    image_key: str | None = None,
    raw_text: str | None = None,
) -> Meal:
    """`POST /meals` 가 부른다. 상태는 ANALYZING 으로 시작한다(서버 기본값).

    `image_key` · `raw_text` 중 최소 하나는 있어야 한다 — 둘 다 없으면 CHECK 제약에 걸린다.
    """
    meal = Meal(
        user_id=user_id,
        medication_snapshot_id=medication_snapshot_id,
        meal_type=meal_type,
        eaten_at=eaten_at,
        image_key=image_key,
        raw_text=raw_text,
    )
    db.add(meal)
    return meal


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

    # 지운 뒤에도 meal.items 는 로드된 옛 목록을 들고 있다. 만료시켜 두지 않으면
    # 같은 세션에서 이어 읽는 쪽이 이미 사라진 항목을 본다.
    db.expire(meal, ["items"])
    return len(removed)


def get_item(db: Session, item_id: uuid.UUID | str) -> MealItem | None:
    return db.get(MealItem, item_id)


def confirm_item(
    db: Session,
    item: MealItem,
    *,
    display_name: str | None = None,
    confirmed_amount_g: Decimal | None = None,
) -> UserCorrection | None:
    """사용자가 인식 결과를 확인·수정한다.

    값이 실제로 바뀌었을 때만 `user_corrections` 에 남긴다. 확인만 하고 그대로 둔 것을
    '수정' 으로 기록하면 AI 성능 지표가 왜곡된다.

    `source` 는 MODEL 로 둔다 — 이 항목을 처음 만든 건 AI 다. USER 는 사용자가 직접
    추가한 항목에만 쓴다.
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

    correction = UserCorrection(
        meal_item_id=item.id, original_value=before, corrected_value=after
    )
    db.add(correction)
    return correction


def add_user_item(
    db: Session,
    meal: Meal,
    *,
    display_name: str,
    confirmed_amount_g: Decimal | None = None,
    food_ref_id: str | None = None,
) -> MealItem:
    """사용자가 직접 추가한 항목. 재분석 때 지우지 않는다(`delete_model_items` 참고)."""
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


def list_corrections(db: Session, meal_item_id: uuid.UUID | str) -> list[UserCorrection]:
    return list(
        db.scalars(
            select(UserCorrection)
            .where(UserCorrection.meal_item_id == meal_item_id)
            .order_by(UserCorrection.corrected_at)
        ).all()
    )


def get_satiety(db: Session, meal_id: uuid.UUID | str) -> SatietyLog | None:
    return db.scalar(select(SatietyLog).where(SatietyLog.meal_id == meal_id))


def upsert_satiety(
    db: Session,
    meal_id: uuid.UUID | str,
    *,
    logged_at: dt.datetime,
    satiety_before: int | None = None,
    satiety_after: int | None = None,
    hunger_return_minutes: int | None = None,
    user_comment: str | None = None,
) -> SatietyLog:
    """식사당 1행이다(`meal_id` UNIQUE). 식전에 한 번, 식후에 한 번 채워진다.

    `None` 인 인자는 건드리지 않는다 — 식후 입력이 식전 값을 지우면 안 된다.
    """
    row = get_satiety(db, meal_id)
    if row is None:
        row = SatietyLog(meal_id=meal_id, logged_at=logged_at)
        db.add(row)

    row.logged_at = logged_at
    if satiety_before is not None:
        row.satiety_before = satiety_before
    if satiety_after is not None:
        row.satiety_after = satiety_after
    if hunger_return_minutes is not None:
        row.hunger_return_minutes = hunger_return_minutes
    if user_comment is not None:
        row.user_comment = user_comment
    return row


def _num(value: Decimal | None) -> float | None:
    """JSONB 에 넣을 수 있게 바꾼다. Decimal 은 직렬화되지 않는다."""
    return None if value is None else float(value)
