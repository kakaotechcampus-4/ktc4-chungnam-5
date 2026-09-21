"""meals 테이블 접근. 여기 말고는 아무도 Meal 을 직접 쿼리하지 않는다."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Row, and_, or_, select
from sqlalchemy.orm import Session

from app.models.enums import MealStatus
from app.models.evaluation import QQSEvaluation
from app.models.meal import Meal, MealItem
from app.models.medication import MedicationSnapshot


def list_meals(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int,
    before_eaten_at: datetime | None = None,
    before_id: uuid.UUID | None = None,
) -> list[Row]:
    """user_id 의 식사를 eaten_at 최신순으로 최대 limit 개 조회한다.

    각 행은 (Meal, stage, quantity_score, quality_score, satiety_score) 튜플이다.
    (before_eaten_at, before_id) 보다 "이전" 항목만 대상으로 한다 — 커서 페이지네이션.
    """
    stmt = (
        select(
            Meal,
            MedicationSnapshot.stage,
            QQSEvaluation.quantity_score,
            QQSEvaluation.quality_score,
            QQSEvaluation.satiety_score,
        )
        .join(MedicationSnapshot, Meal.medication_snapshot_id == MedicationSnapshot.id)
        .outerjoin(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(Meal.user_id == user_id, Meal.deleted_at.is_(None))
    )

    if before_eaten_at is not None:
        stmt = stmt.where(
            or_(
                Meal.eaten_at < before_eaten_at,
                and_(Meal.eaten_at == before_eaten_at, Meal.id < before_id),
            )
        )

    stmt = stmt.order_by(Meal.eaten_at.desc(), Meal.id.desc()).limit(limit)

    return list(db.execute(stmt).all())


def get_display_names(db: Session, meal_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """meal_id 별로 meal_items.display_name 을 쉼표로 이어붙인 값을 돌려준다."""
    if not meal_ids:
        return {}

    stmt = (
        select(MealItem.meal_id, MealItem.display_name)
        .where(MealItem.meal_id.in_(meal_ids))
        .order_by(MealItem.meal_id, MealItem.id)
    )

    names_by_meal: dict[uuid.UUID, list[str]] = {}
    for meal_id, display_name in db.execute(stmt).all():
        names_by_meal.setdefault(meal_id, []).append(display_name)

    return {meal_id: ", ".join(names) for meal_id, names in names_by_meal.items()}


def get_owned_meal(db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID) -> Meal | None:
    """user_id 소유의 살아있는 meal_id 를 가져온다.

    없거나, 남의 것이거나, 이미 삭제됐으면 None — 셋을 구분하지 않는다.
    호출부가 전부 404 로 뭉개야 "남의 mealId 는 존재한다" 는 정보가 새지 않는다.

    **이 WHERE 가 소유권 누출을 막는 유일한 지점이다.** 같은 조건을 다른 곳에
    복사하지 말고 이 함수를 부를 것 — 복사본은 조용히 드리프트한다.

    이름이 "owned" 인 것에 주의: 존재·소유·미삭제만 본다. 지금 고칠 수 있는
    상태인지(`status`)는 보지 않는다 — 그건 `services/` 의 판단이다.
    """
    stmt = select(Meal).where(
        Meal.id == meal_id,
        Meal.user_id == user_id,
        Meal.deleted_at.is_(None),
    )
    return db.execute(stmt).scalar_one_or_none()


def soft_delete_meal(db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID) -> Meal | None:
    """user_id 소유의 meal_id 를 soft delete 한다.

    없거나, 남의 것이거나, 이미 삭제됐으면 None (셋 다 "지울 수 있는 게 없다"로 동일 취급).
    """
    meal = get_owned_meal(db, user_id=user_id, meal_id=meal_id)
    if meal is None:
        return None

    meal.deleted_at = datetime.now(UTC)
    return meal


def set_status(db: Session, meal: Meal, status: MealStatus) -> None:
    """식사 상태를 바꾼다. add/flush 까지만 — 커밋은 services 가 한다.

    `db` 를 받지만 쓰지 않는다 — 이미 세션에 붙어 있는 객체라 대입만으로 UPDATE 가
    나간다. 시그니처를 맞춰 두는 건 "상태를 바꾸는 일은 crud 를 거친다"(절대 규칙 5)
    를 호출부에서 눈에 보이게 하려는 것이다. `crud/__init__.py` 의 Worker 예시가
    이 이름을 그대로 쓴다.
    """
    meal.status = status
