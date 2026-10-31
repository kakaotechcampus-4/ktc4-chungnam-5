"""daily_feedbacks 쓰기. 쓰는 쪽은 `feedback.meal` 워커 하나다.

끼니 피드백은 AI 가 그날 앞 끼니를 누적해 쓴 문장이라, 가장 최근 끼니 피드백이 곧 그날의
하루 피드백이다(`worker/jobs/feedback_meal.py`). 공개 API 는 없다 — 읽는 쪽은 장기 피드백
(`worker/jobs/feedback_long.py`)이다.

`daily_feedbacks` 는 `(user_id, feedback_date)` UNIQUE 라 create 가 아니라 **upsert** 다
(`crud/__init__.py` 의 목록 참고). 링크 테이블 `daily_feedback_sources` 는 따로 파일을
두지 않고 여기서 부모와 함께 다룬다.

전부 flush 까지만 한다 — 커밋은 부르는 쪽(워커는 큐)이 한다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Row, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.enums import SafetyStatus
from app.models.evaluation import QQSEvaluation
from app.models.feedback import DailyFeedback, DailyFeedbackSource
from app.models.meal import Meal


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID,
    feedback_date: date,
    summary: str | None,
    quantity_score: int | None,
    quality_score: int | None,
    satiety_score: int | None,
    model_version: str | None,
    safety_status: SafetyStatus,
) -> uuid.UUID:
    """있으면 덮고 없으면 만든다. 행 id 를 돌려준다. flush 까지만.

    재생성해도 id 와 `created_at`(최초 INSERT 시각)은 그대로다. 대신 `updated_at` 을
    새로 찍는다 — 이 컬럼이 "마지막으로 생성된 시각"(generatedAt)이다.
    `ON CONFLICT DO UPDATE` 는 SQLAlchemy 의 `onupdate` 를 타지 않으므로 SET 에 직접 넣는다.

    `ON CONFLICT` 한 문장이다 — 같은 날 끼니 작업이 둘 겹쳐도 무결성 위반으로 죽지 않는다.

    점수가 None 이면 그날 그 축을 채점한 끼니가 없다는 뜻이다 (`day_score_averages`).
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
        set_={**values, "updated_at": func.now()},
    ).returning(DailyFeedback.id)
    daily_feedback_id = db.execute(stmt).scalar_one()
    db.flush()
    return daily_feedback_id


def upsert_scores(
    db: Session,
    *,
    user_id: uuid.UUID,
    feedback_date: date,
    quantity_score: int | None,
    quality_score: int | None,
    satiety_score: int | None,
    safety_status: SafetyStatus,
) -> uuid.UUID:
    """점수만 덮는다. 행이 없으면 요약 없이 만든다. 행 id 를 돌려준다. flush 까지만.

    끼니 피드백이 SAFE 가 아닐 때 쓴다. 그 문장은 하루 요약으로 올리지 않지만 점수는
    Rule Engine 결과라 그 끼니까지 반영해야 한다 — 안 그러면 저녁을 먹었는데 점심까지의
    점수가 남는다. 이미 있는 행의 summary · model_version · safety_status 는 앞 끼니까지의
    요약에 붙은 값이라 건드리지 않는다.

    행이 없을 때만 `safety_status` 를 쓴다 — 요약이 비어 있는 이유(AI 판정)를 남긴다.

    `updated_at` 도 새로 찍는다 — 행이 마지막으로 바뀐 시각이다.
    """
    scores = {
        "quantity_score": quantity_score,
        "quality_score": quality_score,
        "satiety_score": satiety_score,
    }
    stmt = pg_insert(DailyFeedback).values(
        user_id=user_id,
        feedback_date=feedback_date,
        summary=None,
        safety_status=safety_status,
        **scores,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailyFeedback.user_id, DailyFeedback.feedback_date],
        set_={**scores, "updated_at": func.now()},
    ).returning(DailyFeedback.id)
    daily_feedback_id = db.execute(stmt).scalar_one()
    db.flush()
    return daily_feedback_id


def day_score_averages(
    db: Session,
    *,
    user_id: uuid.UUID,
    range_start: datetime,
    range_end: datetime,
) -> Row:
    """그날([range_start, range_end), KST 하루 경계) 채점된 끼니의 축별 Q/Q/S 평균.

    행은 (quantity, quality, satiety) — 각각 Decimal 이거나, 그 축을 채점한 끼니가 없으면 None.
    `AVG` 는 NULL 을 건너뛴다 — 채점 못 한 축을 0 으로 세지 않는다. 지워진 식사는 뺀다.
    끼니 피드백의 safety_status 는 보지 않는다 — 점수는 AI 가 쓴 문장이 아니라 Rule Engine 결과다.
    """
    stmt = (
        select(
            func.avg(QQSEvaluation.quantity_score).label("quantity"),
            func.avg(QQSEvaluation.quality_score).label("quality"),
            func.avg(QQSEvaluation.satiety_score).label("satiety"),
        )
        .join(Meal, Meal.id == QQSEvaluation.meal_id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= range_start,
            Meal.eaten_at < range_end,
        )
    )
    return db.execute(stmt).one()


def add_source(
    db: Session, *, daily_feedback_id: uuid.UUID, meal_feedback_id: uuid.UUID
) -> None:
    """근거 링크 하나를 더한다. 이미 있으면 아무 일도 없다. flush 까지만.

    끼니 피드백이 그날 앞 끼니들을 누적해 쓴 문장이라 링크도 지우지 않고 쌓는다.
    같은 끼니가 다시 돌아도(재확정·재시도) PK 에 걸려 깨지지 않게 `ON CONFLICT DO NOTHING`.
    """
    db.execute(
        pg_insert(DailyFeedbackSource)
        .values(daily_feedback_id=daily_feedback_id, meal_feedback_id=meal_feedback_id)
        .on_conflict_do_nothing()
    )
    db.flush()
