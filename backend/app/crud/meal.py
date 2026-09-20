"""meals 테이블 접근. 여기 말고는 아무도 Meal 을 직접 쿼리하지 않는다."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Row, and_, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.models.evaluation import QQSEvaluation
from app.models.meal import Meal, MealItem
from app.models.medication import MedicationSnapshot

_KST = "Asia/Seoul"


def _kst_day(column):
    """eaten_at(UTC 로 저장됨)을 KST 기준 '그 날' 로 자른다.

    UTC 기준으로 자르면 밤 11시(KST)에 먹은 야식이 다음날로 잘못 집계된다.
    """
    return func.date_trunc("day", func.timezone(_KST, column))


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


def soft_delete_meal(db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID) -> Meal | None:
    """user_id 소유의 meal_id 를 soft delete 한다.

    없거나, 남의 것이거나, 이미 삭제됐으면 None (셋 다 "지울 수 있는 게 없다"로 동일 취급).
    """
    stmt = select(Meal).where(
        Meal.id == meal_id,
        Meal.user_id == user_id,
        Meal.deleted_at.is_(None),
    )
    meal = db.execute(stmt).scalar_one_or_none()
    if meal is None:
        return None

    meal.deleted_at = datetime.now(timezone.utc)
    return meal


def get_calendar_days(
    db: Session,
    *,
    user_id: uuid.UUID,
    month_start: datetime,
    month_end: datetime,
) -> list[Row]:
    """KST 기준 날짜별 (day, count, meal_types) 를 조회한다."""
    day = _kst_day(Meal.eaten_at)
    stmt = (
        select(
            day.label("day"),
            func.count().label("count"),
            func.array_agg(distinct(Meal.meal_type)).label("meal_types"),
        )
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= month_start,
            Meal.eaten_at < month_end,
        )
        .group_by(day)
        .order_by(day)
    )
    return list(db.execute(stmt).all())


def get_calendar_day_stages(
    db: Session,
    *,
    user_id: uuid.UUID,
    month_start: datetime,
    month_end: datetime,
) -> list[Row]:
    """날짜별 대표 stage — 그날 가장 마지막(eaten_at 최신)에 먹은 식사 기준."""
    day = _kst_day(Meal.eaten_at)
    stmt = (
        select(day.label("day"), MedicationSnapshot.stage)
        .join(MedicationSnapshot, Meal.medication_snapshot_id == MedicationSnapshot.id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= month_start,
            Meal.eaten_at < month_end,
        )
        .distinct(day)
        .order_by(day, Meal.eaten_at.desc())
    )
    return list(db.execute(stmt).all())


def get_calendar_summary(
    db: Session,
    *,
    user_id: uuid.UUID,
    month_start: datetime,
    month_end: datetime,
) -> Row:
    """그 달 전체 요약 — 총 식사 수, 평균 Q/Q/S (미평가 식사는 평균에서 자동 제외)."""
    stmt = (
        select(
            func.count(Meal.id).label("total_meals"),
            func.avg(QQSEvaluation.quantity_score).label("avg_quantity"),
            func.avg(QQSEvaluation.quality_score).label("avg_quality"),
            func.avg(QQSEvaluation.satiety_score).label("avg_satiety"),
        )
        .select_from(Meal)
        .outerjoin(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= month_start,
            Meal.eaten_at < month_end,
        )
    )
    return db.execute(stmt).one()
