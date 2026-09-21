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
    status: MealStatus | None = MealStatus.REVIEW_REQUIRED,
) -> Meal:
    """식사 하나와 거기 딸린 투약 스냅샷을 만든다. flush 까지만 하고 커밋하지 않는다.

    기본값이 `REVIEW_REQUIRED` 인 건 확인 화면(사용자가 실제로 식사를 만지는 상태)이
    대부분의 테스트가 필요로 하는 출발점이기 때문이다.

    `status=None` 이면 INSERT 에서 컬럼을 빼 DB 의 server_default 를 태운다 —
    기본값 자체를 검증하는 테스트가 쓴다.
    """
    snapshot = MedicationSnapshot(user_id=user_id, stage=MedicationStage.MAINTENANCE)
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
    food_ref_id: str = "KFD_TEST_01",
    name: str = "미역국",
    category: FoodCategory | None = FoodCategory.GENERAL,
    serving_size: Decimal | None = Decimal("100.000"),
    calories: Decimal | None = Decimal("50.000"),
    protein_g: Decimal | None = Decimal("3.000"),
    fat_g: Decimal | None = Decimal("1.500"),
    carbohydrate_g: Decimal | None = Decimal("4.000"),
    fiber_g: Decimal | None = Decimal("0.500"),
    sodium_mg: Decimal | None = Decimal("600.000"),
) -> FoodRef:
    """공공 영양 DB 음식 1건. flush 까지만 하고 커밋하지 않는다.

    `category` 기본값이 `GENERAL` 인 건 이름 매칭이 GENERAL 을 먼저 보기 때문이다
    (`crud.food.find_unique_by_name`). 기본값을 NULL 로 두면 대부분의 테스트가
    폴백 경로만 타게 되어 정작 주 경로를 검증하지 못한다.
    """
    food_ref = FoodRef(
        id=food_ref_id,
        name=name,
        category=category,
        serving_size=serving_size,
        calories=calories,
        protein_g=protein_g,
        fat_g=fat_g,
        carbohydrate_g=carbohydrate_g,
        fiber_g=fiber_g,
        sodium_mg=sodium_mg,
        dataset_version="test",
    )
    db.add(food_ref)
    db.flush()
    return food_ref


def make_meal_item(
    db: Session,
    *,
    meal_id: uuid.UUID,
    display_name: str = "참치김밥",
    food_ref_id: str | None = None,
    estimated_amount_g: Decimal | None = Decimal("250.00"),
    confirmed_amount_g: Decimal | None = None,
    confidence: Decimal | None = Decimal("0.620"),
    source: MealItemSource = MealItemSource.MODEL,
    raw_ai_result: dict | None = None,
) -> MealItem:
    """식사에 딸린 음식 1건. flush 까지만 하고 커밋하지 않는다.

    기본값은 **AI 가 인식한 항목**이다 — `source=MODEL`, 양은 `estimated_amount_g`
    에만 있고 `confirmed_amount_g` 는 NULL(사용자 확인 전). `PATCH` 가 고치는 것이
    주로 이 모양이라 기본값으로 뒀다. 사용자가 직접 넣은 항목을 만들려면
    `source=MealItemSource.USER, confidence=None` 을 넘긴다 — 신뢰도는 AI 인식값에만
    있는 개념이다(`crud.meal.add_item` 참고).
    """
    item = MealItem(
        meal_id=meal_id,
        food_ref_id=food_ref_id,
        original_food_name=display_name,
        display_name=display_name,
        estimated_amount_g=estimated_amount_g,
        confirmed_amount_g=confirmed_amount_g,
        confidence=confidence,
        source=source,
        raw_ai_result=raw_ai_result,
    )
    db.add(item)
    db.flush()
    return item
