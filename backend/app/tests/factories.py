"""테스트용 행 생성 헬퍼.

`conftest.py` 는 픽스처(세션·클라이언트) 전용이라 여기에 둔다. 아직 `POST /meals` 가
없어서 meal 을 만드는 crud 함수도 없다 — 그래서 모델을 직접 조립한다. 해당 crud 가
생기면 이 함수들이 그걸 부르도록 바꾼다.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.crud import user as user_crud
from app.models.enums import MealType, MedicationStage
from app.models.meal import Meal
from app.models.medication import MedicationSnapshot
from app.models.user import User

EATEN_AT = datetime(2026, 8, 22, 12, 30, tzinfo=timezone.utc)


def make_user(db: Session, nickname: str = "종호") -> User:
    return user_crud.create(
        db,
        nickname=nickname,
        height_cm=Decimal("174.0"),
        baseline_meal_kcal=Decimal("700.00"),
    )


def make_meal(db: Session, *, user_id: uuid.UUID, eaten_at: datetime = EATEN_AT) -> Meal:
    """식사 하나와 거기 딸린 투약 스냅샷을 만든다. flush 까지만 하고 커밋하지 않는다."""
    snapshot = MedicationSnapshot(user_id=user_id, stage=MedicationStage.MAINTENANCE)
    db.add(snapshot)
    db.flush()

    meal = Meal(
        user_id=user_id,
        medication_snapshot_id=snapshot.id,
        meal_type=MealType.LUNCH,
        raw_text="김치찌개",
        eaten_at=eaten_at,
    )
    db.add(meal)
    db.flush()
    return meal
