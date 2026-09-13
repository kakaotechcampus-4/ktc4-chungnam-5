"""`meal_feedbacks` · `daily_feedbacks` · `long_term_feedbacks` 접근.

셋 다 UNIQUE 제약이 있어 **재생성이 아니라 덮어쓰기**다.

    meal_feedbacks       UNIQUE (meal_id)
    daily_feedbacks      UNIQUE (user_id, feedback_date)
    long_term_feedbacks  UNIQUE (user_id, period_type, period_start)

근거 링크(`*_sources`)는 부모의 `sources` 관계로만 쓴다 — 단독으로 다룰 일이 없다.

`safety_status` 의 기본값은 `REVIEW_REQUIRED` 다. 가드레일을 통과해야만 `SAFE` 가 되므로,
호출하는 쪽이 값을 빠뜨려도 피드백이 그대로 노출되지 않는다(규칙 1).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import FeedbackPeriodType, SafetyStatus
from app.models.feedback import (
    DailyFeedback,
    DailyFeedbackSource,
    LongTermFeedback,
    LongTermFeedbackSource,
    MealFeedback,
)


# ─────────────────────────── meal_feedbacks ───────────────────────────


def get_by_meal(db: Session, meal_id: uuid.UUID | str) -> MealFeedback | None:
    return db.scalar(select(MealFeedback).where(MealFeedback.meal_id == meal_id))


def upsert_meal_feedback(
    db: Session,
    *,
    user_id: uuid.UUID | str,
    meal_id: uuid.UUID | str,
    body: str | None,
    suggestions: str | None,
    reasoning: str | None,
    model_version: str | None,
    safety_status: SafetyStatus = SafetyStatus.REVIEW_REQUIRED,
) -> MealFeedback:
    row = get_by_meal(db, meal_id)
    if row is None:
        row = MealFeedback(user_id=user_id, meal_id=meal_id)
        db.add(row)

    row.body = body
    row.suggestions = suggestions
    row.reasoning = reasoning
    row.model_version = model_version
    row.safety_status = safety_status
    return row


def list_meal_feedbacks_on(
    db: Session, user_id: uuid.UUID | str, day: dt.date
) -> list[MealFeedback]:
    """그날 만들어진 끼니 피드백. 일일 피드백의 재료다."""
    start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    return list(
        db.scalars(
            select(MealFeedback)
            .where(
                MealFeedback.user_id == user_id,
                MealFeedback.created_at >= start,
                MealFeedback.created_at < end,
            )
            .order_by(MealFeedback.created_at)
        ).all()
    )


# ─────────────────────────── daily_feedbacks ───────────────────────────


def get_daily(db: Session, user_id: uuid.UUID | str, day: dt.date) -> DailyFeedback | None:
    return db.scalar(
        select(DailyFeedback).where(
            DailyFeedback.user_id == user_id,
            DailyFeedback.feedback_date == day,
        )
    )


def upsert_daily_feedback(
    db: Session,
    *,
    user_id: uuid.UUID | str,
    feedback_date: dt.date,
    summary: str | None,
    quantity_score: Decimal | None,
    quality_score: Decimal | None,
    satiety_score: Decimal | None,
    model_version: str | None,
    safety_status: SafetyStatus = SafetyStatus.REVIEW_REQUIRED,
    sources: list[MealFeedback] | None = None,
) -> DailyFeedback:
    """점수 셋은 AI 가 아니라 BE 가 집계한 값이다 — 규칙 2."""
    row = get_daily(db, user_id, feedback_date)
    if row is None:
        row = DailyFeedback(user_id=user_id, feedback_date=feedback_date)
        db.add(row)

    row.summary = summary
    row.quantity_score = quantity_score
    row.quality_score = quality_score
    row.satiety_score = satiety_score
    row.model_version = model_version
    row.safety_status = safety_status

    if sources is not None:
        # 재생성이면 근거를 통째로 갈아 끼운다. 옛 근거가 섞이면 추적이 틀린다.
        row.sources = [DailyFeedbackSource(meal_feedback_id=src.id) for src in sources]
    return row


def list_daily_feedbacks(
    db: Session, user_id: uuid.UUID | str, *, since: dt.date, until: dt.date
) -> list[DailyFeedback]:
    """기간 일일 피드백. 장기 피드백의 재료다."""
    return list(
        db.scalars(
            select(DailyFeedback)
            .where(
                DailyFeedback.user_id == user_id,
                DailyFeedback.feedback_date >= since,
                DailyFeedback.feedback_date <= until,
            )
            .order_by(DailyFeedback.feedback_date)
        ).all()
    )


# ─────────────────────────── long_term_feedbacks ───────────────────────────


def get_long_term(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    period_type: FeedbackPeriodType,
    period_start: dt.date,
) -> LongTermFeedback | None:
    return db.scalar(
        select(LongTermFeedback).where(
            LongTermFeedback.user_id == user_id,
            LongTermFeedback.period_type == period_type,
            LongTermFeedback.period_start == period_start,
        )
    )


def upsert_long_term_feedback(
    db: Session,
    *,
    user_id: uuid.UUID | str,
    period_type: FeedbackPeriodType,
    period_start: dt.date,
    period_end: dt.date,
    trend_summary: str | None,
    recommendation: str | None,
    chart_data: dict | None,
    model_version: str | None,
    safety_status: SafetyStatus = SafetyStatus.REVIEW_REQUIRED,
    sources: list[DailyFeedback] | None = None,
) -> LongTermFeedback:
    """`chart_data` 는 AI 가 아니라 BE 가 `qqs_evaluations` 를 집계해 만든다 — 규칙 2."""
    row = get_long_term(db, user_id, period_type=period_type, period_start=period_start)
    if row is None:
        row = LongTermFeedback(
            user_id=user_id, period_type=period_type, period_start=period_start
        )
        db.add(row)

    row.period_end = period_end
    row.trend_summary = trend_summary
    row.recommendation = recommendation
    row.chart_data = chart_data
    row.model_version = model_version
    row.safety_status = safety_status

    if sources is not None:
        row.sources = [LongTermFeedbackSource(daily_feedback_id=src.id) for src in sources]
    return row


def get_latest_long_term(
    db: Session, user_id: uuid.UUID | str, period_type: FeedbackPeriodType
) -> LongTermFeedback | None:
    return db.scalar(
        select(LongTermFeedback)
        .where(
            LongTermFeedback.user_id == user_id,
            LongTermFeedback.period_type == period_type,
        )
        .order_by(LongTermFeedback.period_start.desc())
        .limit(1)
    )
