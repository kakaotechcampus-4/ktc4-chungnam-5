"""테스트용 행 생성 헬퍼.

`conftest.py` 는 픽스처(세션·클라이언트) 전용이라 여기에 둔다. 아직 `POST /meals` 가
없어서 meal 을 만드는 crud 함수도 없다 — 그래서 모델을 직접 조립한다. 해당 crud 가
생기면 이 함수들이 그걸 부르도록 바꾼다.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.crud import user as user_crud
from app.models.enums import (
    FoodCategory,
    MealItemSource,
    MealStatus,
    MealType,
    MedicationStage,
)
from app.models.food import FoodRef
from app.models.meal import Meal, MealItem
from app.models.medication import MedicationSnapshot
from app.models.user import User

EATEN_AT = datetime(2026, 8, 22, 12, 30, tzinfo=UTC)


def make_user(db: Session, nickname: str = "종호") -> User:
    return user_crud.create(
        db,
        nickname=nickname,
        height_cm=Decimal("174.0"),
        baseline_meal_kcal=Decimal("700.00"),
    )


def make_meal(
    db: Session,
    *,
    user_id: uuid.UUID,
    eaten_at: datetime = EATEN_AT,
    stage: MedicationStage = MedicationStage.MAINTENANCE,
    status: MealStatus | None = None,
) -> Meal:
    """식사 하나와 거기 딸린 투약 스냅샷을 만든다. flush 까지만 하고 커밋하지 않는다.

    `status` 를 안 주면 컬럼을 비워 DB server_default(ANALYZING)를 타게 둔다 —
    기본값이 스키마에서 오는지까지 검증할 수 있다.
    """
    snapshot = MedicationSnapshot(user_id=user_id, stage=stage)
    db.add(snapshot)
    db.flush()

    meal = Meal(
        user_id=user_id,
        medication_snapshot_id=snapshot.id,
        meal_type=MealType.LUNCH,
        raw_text="김치찌개",
        eaten_at=eaten_at,
        **({} if status is None else {"status": status}),
    )
    db.add(meal)
    db.flush()
    return meal


def make_food_ref(
    db: Session,
    *,
    food_ref_id: str = "KFD_TEST",
    name: str = "미역국",
    serving_size: str = "100",
    calories: str = "50",
    protein_g: str = "3",
    fiber_g: str = "2",
    sodium_mg: str = "400",
) -> FoodRef:
    """공공 DB 한 행. 성분은 `serving_size` 기준이다."""
    ref = FoodRef(
        id=food_ref_id,
        name=name,
        category=FoodCategory.GENERAL,
        serving_size=Decimal(serving_size),
        calories=Decimal(calories),
        protein_g=Decimal(protein_g),
        fiber_g=Decimal(fiber_g),
        sodium_mg=Decimal(sodium_mg),
        dataset_version="test",
    )
    db.add(ref)
    db.flush()
    return ref


def make_meal_item(
    db: Session,
    *,
    meal_id: uuid.UUID,
    food_ref_id: str | None = "KFD_TEST",
    display_name: str = "미역국",
    amount_g: str | None = "200",
) -> MealItem:
    """식사 구성 음식 한 건. 양은 사용자 확인값(`confirmed_amount_g`)으로 넣는다."""
    item = MealItem(
        meal_id=meal_id,
        food_ref_id=food_ref_id,
        original_food_name=display_name,
        display_name=display_name,
        confirmed_amount_g=None if amount_g is None else Decimal(amount_g),
        source=MealItemSource.USER,
    )
    db.add(item)
    db.flush()
    return item
