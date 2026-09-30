"""daily_feedbacks 접근 + 하루 피드백의 근거(그날 SAFE 끼니 피드백) 조회.

`daily_feedbacks` 는 `(user_id, feedback_date)` UNIQUE 라 create 가 아니라 **upsert** 다
(`crud/__init__.py` 의 목록 참고). 링크 테이블 `daily_feedback_sources` 는 따로 파일을
두지 않고 여기서 부모와 함께 다룬다.

전부 flush 까지만 한다 — 커밋은 부르는 쪽(워커는 큐)이 한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import Row, delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.enums import SafetyStatus
from app.models.evaluation import QQSEvaluation
from app.models.feedback import DailyFeedback, DailyFeedbackSource, MealFeedback
from app.models.meal import Meal
from app.models.task import Task

REFRESH_TASK_TYPE = "feedback.daily"


def get_latest_refresh_task(
    db: Session, *, user_id: uuid.UUID, feedback_date: date
) -> Task | None:
    """이 사용자·날짜의 feedback.daily 작업 중 가장 최근에 등록된 것. 없으면 None.

    payload 키 이름(userId/date)은 `worker/jobs/feedback_daily.py` 가 이미
    정해둔 것과 맞춘다 — 여기서 새로 정하면 워커가 못 읽는다.
    """
    raise NotImplementedError("get_latest_refresh_task 미구현")


def list_day_evidence(
    db: Session,
    *,
    user_id: uuid.UUID,
    range_start: datetime,
    range_end: datetime,
) -> list[Row]:
    """그날([range_start, range_end), KST 하루 경계)의 하루 피드백 근거를 eaten_at 순으로.

    각 행은 (meal_feedback_id, meal_type, body, quantity_score, quality_score, satiety_score).

    근거가 되는 끼니 피드백만 남긴다:
      - 삭제되지 않은 식사 (`meals` 는 soft delete)
      - `safety_status = SAFE` — BLOCKED · REVIEW_REQUIRED 는 근거에도 점수에도 넣지 않는다
      - body 가 있다 — AI 에 넘길 끼니 요약이 없으면 근거가 아니다
      - Q/Q/S 평가 행이 있고 세 점수가 모두 채워져 있다 — 새로 채점하지 않는다
    """
    stmt = (
        select(
            MealFeedback.id.label("meal_feedback_id"),
            Meal.meal_type,
            MealFeedback.body,
            QQSEvaluation.quantity_score,
            QQSEvaluation.quality_score,
            QQSEvaluation.satiety_score,
        )
        .join(Meal, Meal.id == MealFeedback.meal_id)
        .join(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= range_start,
            Meal.eaten_at < range_end,
            MealFeedback.safety_status == SafetyStatus.SAFE,
            MealFeedback.body.is_not(None),
            QQSEvaluation.quantity_score.is_not(None),
            QQSEvaluation.quality_score.is_not(None),
            QQSEvaluation.satiety_score.is_not(None),
        )
        .order_by(Meal.eaten_at, Meal.id)
    )
    return list(db.execute(stmt).all())


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID,
    feedback_date: date,
    summary: str | None,
    quantity_score: int,
    quality_score: int,
    satiety_score: int,
    model_version: str | None,
    safety_status: SafetyStatus,
) -> uuid.UUID:
    """있으면 덮고 없으면 만든다. 행 id 를 돌려준다. flush 까지만.

    재생성해도 id 는 그대로다. 대신 `created_at` 을 새로 찍는다 — 이 컬럼이
    "마지막으로 생성된 시각"(generatedAt)이다 (D2(b)).

    `ON CONFLICT` 한 문장이다 — 같은 날 작업이 둘 겹쳐도 무결성 위반으로 죽지 않는다.
    """
    values = {
        "summary": summary,
        "quantity_score": quantity_score,
        "quality_score": quality_score,
        "satiety_score": satiety_score,
        "model_version": model_version,
        "safety_status": safety_status,
    }
    stmt = pg_insert(DailyFeedback).values(
        user_id=user_id, feedback_date=feedback_date, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailyFeedback.user_id, DailyFeedback.feedback_date],
        set_={**values, "created_at": func.now()},
    ).returning(DailyFeedback.id)
    daily_feedback_id = db.execute(stmt).scalar_one()
    db.flush()
    return daily_feedback_id


def replace_sources(
    db: Session,
    *,
    daily_feedback_id: uuid.UUID,
    meal_feedback_ids: Iterable[uuid.UUID],
) -> None:
    """근거 링크를 지우고 이번 근거로 다시 만든다. flush 까지만.

    지우지 않으면 재실행 때 같은 근거가 쌓이거나, 더는 근거가 아닌 끼니가 남는다.
    """
    db.execute(
        delete(DailyFeedbackSource).where(
            DailyFeedbackSource.daily_feedback_id == daily_feedback_id
        )
    )
    rows = [
        {"daily_feedback_id": daily_feedback_id, "meal_feedback_id": meal_feedback_id}
        for meal_feedback_id in meal_feedback_ids
    ]
    if rows:
        db.execute(insert(DailyFeedbackSource), rows)
    db.flush()


def delete_for_day(db: Session, *, user_id: uuid.UUID, feedback_date: date) -> None:
    """그 사용자의 그날 행을 지운다 (근거 링크는 FK CASCADE). 없으면 아무 일도 없다.

    근거가 0 이 된 날의 낡은 요약을 남기지 않기 위해서다. user_id 조건이 빠지면
    같은 날 다른 사용자의 행까지 지운다.
    """
    db.execute(
        delete(DailyFeedback).where(
            DailyFeedback.user_id == user_id,
            DailyFeedback.feedback_date == feedback_date,
        )
    )
    db.flush()
