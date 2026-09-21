"""dashboard 집계 쿼리. meals·qqs_evaluations 를 걸쳐 읽기만 한다."""

import uuid
from datetime import datetime

from sqlalchemy import Row, func, select
from sqlalchemy.orm import Session

from app.crud.meal import kst_day, kst_month
from app.models.evaluation import QQSEvaluation
from app.models.meal import Meal
from app.models.medication import MedicationRecord
from app.models.user import UserState


def get_daily_scores(
    db: Session,
    *,
    user_id: uuid.UUID,
    range_start: datetime | None,
    range_end: datetime,
) -> list[Row]:
    """KST 기준 날짜별 평균 Q/Q/S 점수. 평가된 식사가 하루도 없으면 그 날은 안 나온다."""
    day = kst_day(Meal.eaten_at)
    stmt = (
        select(
            day.label("day"),
            func.avg(QQSEvaluation.quantity_score).label("avg_quantity"),
            func.avg(QQSEvaluation.quality_score).label("avg_quality"),
            func.avg(QQSEvaluation.satiety_score).label("avg_satiety"),
        )
        .select_from(Meal)
        .join(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(Meal.user_id == user_id, Meal.deleted_at.is_(None), Meal.eaten_at < range_end)
        .group_by(day)
        .order_by(day)
    )
    if range_start is not None:
        stmt = stmt.where(Meal.eaten_at >= range_start)
    return list(db.execute(stmt).all())


def get_meal_type_averages(
    db: Session,
    *,
    user_id: uuid.UUID,
    range_start: datetime | None,
    range_end: datetime,
) -> list[Row]:
    """끼니 종류별 평균 Q/Q/S. 그 종류로 평가된 식사가 하나도 없으면 그 타입은 안 나온다."""
    stmt = (
        select(
            Meal.meal_type,
            func.avg(QQSEvaluation.quantity_score).label("avg_quantity"),
            func.avg(QQSEvaluation.quality_score).label("avg_quality"),
            func.avg(QQSEvaluation.satiety_score).label("avg_satiety"),
        )
        .select_from(Meal)
        .join(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(Meal.user_id == user_id, Meal.deleted_at.is_(None), Meal.eaten_at < range_end)
        .group_by(Meal.meal_type)
    )
    if range_start is not None:
        stmt = stmt.where(Meal.eaten_at >= range_start)
    return list(db.execute(stmt).all())


def get_monthly_averages(
    db: Session,
    *,
    user_id: uuid.UUID,
    range_start: datetime,
    range_end: datetime,
) -> list[Row]:
    """KST 기준 월별 평균 Q/Q/S. range_start 는 항상 값이 있다 — "최근 N개월" 은 하한이 고정."""
    month = kst_month(Meal.eaten_at)
    stmt = (
        select(
            month.label("month"),
            func.avg(QQSEvaluation.quantity_score).label("avg_quantity"),
            func.avg(QQSEvaluation.quality_score).label("avg_quality"),
            func.avg(QQSEvaluation.satiety_score).label("avg_satiety"),
        )
        .select_from(Meal)
        .join(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= range_start,
            Meal.eaten_at < range_end,
        )
        .group_by(month)
        .order_by(month)
    )
    return list(db.execute(stmt).all())


def get_period_averages(
    db: Session,
    *,
    user_id: uuid.UUID,
    range_start: datetime | None,
    range_end: datetime,
) -> Row:
    """기간 전체 평균 Q/Q/S. 평가된 식사가 하나도 없으면 전부 NULL."""
    stmt = (
        select(
            func.avg(QQSEvaluation.quantity_score).label("avg_quantity"),
            func.avg(QQSEvaluation.quality_score).label("avg_quality"),
            func.avg(QQSEvaluation.satiety_score).label("avg_satiety"),
        )
        .select_from(Meal)
        .join(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(Meal.user_id == user_id, Meal.deleted_at.is_(None), Meal.eaten_at < range_end)
    )
    if range_start is not None:
        stmt = stmt.where(Meal.eaten_at >= range_start)
    return db.execute(stmt).one()


def get_weight_series(
    db: Session,
    *,
    user_id: uuid.UUID,
    range_start: datetime | None,
    range_end: datetime,
) -> list[Row]:
    """KST 기준 날짜별 대표 체중 — 그날 가장 마지막에 기록된 값 기준."""
    day = kst_day(UserState.recorded_at)
    stmt = (
        select(day.label("day"), UserState.weight_kg)
        .where(
            UserState.user_id == user_id,
            UserState.weight_kg.is_not(None),
            UserState.recorded_at < range_end,
        )
        .distinct(day)
        .order_by(day, UserState.recorded_at.desc())
    )
    if range_start is not None:
        stmt = stmt.where(UserState.recorded_at >= range_start)
    return list(db.execute(stmt).all())


def get_medication_records(db: Session, *, user_id: uuid.UUID) -> list[Row]:
    """user_id 의 투약 기록 전체를 effective_from 순으로 조회한다 (stage 변경 이력 계산용).

    기간으로 미리 자르지 않는다 — 기간 시작 "직전"에 바뀐 stage까지 알아야
    "기간 안에서 뭐로 바뀌었는지" 를 정확히 계산할 수 있다.
    """
    stmt = (
        select(MedicationRecord.stage, MedicationRecord.effective_from)
        .where(MedicationRecord.user_id == user_id)
        .order_by(MedicationRecord.effective_from)
    )
    return list(db.execute(stmt).all())
