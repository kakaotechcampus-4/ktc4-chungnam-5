"""`qqs_evaluations` 접근.

**식사당 1행이다.** 재평가하면 새로 만들지 않고 덮어쓴다 — `meal_id` 가 UNIQUE 라
`create` 를 두 번 부르면 무결성 위반으로 커밋이 통째로 깨진다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MedicationStage
from app.models.evaluation import QQSEvaluation
from app.models.meal import Meal


def get_by_meal(db: Session, meal_id: uuid.UUID | str) -> QQSEvaluation | None:
    return db.scalar(select(QQSEvaluation).where(QQSEvaluation.meal_id == meal_id))


def upsert(
    db: Session,
    meal_id: uuid.UUID | str,
    *,
    stage_at_evaluation: MedicationStage,
    quantity_score: Decimal | None,
    quality_score: Decimal | None,
    satiety_score: Decimal | None,
) -> QQSEvaluation:
    """점수를 쓴다. 이미 있으면 덮어쓴다.

    점수가 None 일 수 있다 — 성분이 비어 있는 음식이 섞이면 Quality 를 낼 수 없다.
    공공 DB 의 외식 15,225건이 지방·탄수화물 대부분 결측이라 흔한 경우다.
    """
    row = get_by_meal(db, meal_id)
    if row is None:
        row = QQSEvaluation(meal_id=meal_id)
        db.add(row)

    row.stage_at_evaluation = stage_at_evaluation
    row.quantity_score = quantity_score
    row.quality_score = quality_score
    row.satiety_score = satiety_score
    return row


def list_series(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    since: dt.datetime,
    until: dt.datetime,
) -> list[tuple[dt.datetime, QQSEvaluation]]:
    """기간 Q/Q/S 시계열. 장기 피드백과 대시보드가 쓴다.

    `eaten_at` 기준으로 묶는다 — 평가 시각(`computed_at`)이 아니라 **먹은 시각**이
    사용자가 보는 축이다. 어제 식사를 오늘 재평가해도 어제 자리에 남아야 한다.
    """
    rows = db.execute(
        select(Meal.eaten_at, QQSEvaluation)
        .join(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(Meal.user_id == user_id, Meal.eaten_at >= since, Meal.eaten_at <= until)
        .order_by(Meal.eaten_at)
    ).all()
    return [(eaten_at, evaluation) for eaten_at, evaluation in rows]
