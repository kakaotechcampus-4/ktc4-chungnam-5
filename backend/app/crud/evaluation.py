"""`qqs_evaluations` 접근. **식사당 1행이다**(`meal_id` UNIQUE).

시계열을 뽑을 때는 `computed_at` 이 아니라 식사의 `eaten_at` 으로 묶어야 한다.
사용자가 보는 축은 먹은 시각이다 — 어제 식사를 오늘 재평가해도 어제 자리에 남아야 한다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MedicationStage
from app.models.evaluation import QQSEvaluation


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
    """재평가하면 덮어쓴다. `create` 를 두 번 부르면 UNIQUE 위반이다.

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
