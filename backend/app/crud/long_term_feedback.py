"""long_term_feedbacks 쓰기 + 장기 피드백의 근거(기간 안 SAFE 하루 피드백) 조회.

`long_term_feedbacks` 는 `(user_id, period_type, period_start)` UNIQUE 라 create 가 아니라
**upsert** 다. 링크 테이블 `long_term_feedback_sources` 는 따로 파일을 두지 않고 여기서
부모와 함께 다룬다. 읽기 API 용 조회는 `crud/insight.py` 에 있다.

전부 flush 까지만 한다 — 커밋은 부르는 쪽(워커는 큐)이 한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import Row, delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.enums import FeedbackPeriodType, SafetyStatus
from app.models.feedback import DailyFeedback, LongTermFeedback, LongTermFeedbackSource


def list_period_sources(
    db: Session,
    *,
    user_id: uuid.UUID,
    date_from: date | None,
    date_to: date,
) -> list[Row]:
    """[date_from, date_to](양끝 포함) 안의 근거 하루 피드백을 날짜순으로. date_from=None 이면 하한 없음.

    각 행은 (daily_feedback_id, summary).

    `feedback_date` 는 이미 KST 달력 날짜라 날짜끼리 비교한다. 근거가 되는 것만 남긴다:
      - `safety_status = SAFE` — BLOCKED · REVIEW_REQUIRED 문장을 AI 에 다시 먹이지 않는다
      - summary 가 있다
    """
    stmt = (
        select(DailyFeedback.id.label("daily_feedback_id"), DailyFeedback.summary)
        .where(
            DailyFeedback.user_id == user_id,
            DailyFeedback.feedback_date <= date_to,
            DailyFeedback.safety_status == SafetyStatus.SAFE,
            DailyFeedback.summary.is_not(None),
        )
        .order_by(DailyFeedback.feedback_date)
    )
    if date_from is not None:
        stmt = stmt.where(DailyFeedback.feedback_date >= date_from)
    return list(db.execute(stmt).all())


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID,
    period_type: FeedbackPeriodType,
    period_start: date,
    period_end: date,
    trend_summary: str | None,
    recommendation: str | None,
    chart_data: dict[str, Any] | None,
    model_version: str | None,
    safety_status: SafetyStatus,
) -> uuid.UUID:
    """있으면 덮고 없으면 만든다. 행 id 를 돌려준다. flush 까지만.

    재생성해도 id 와 `created_at` 은 그대로고 `updated_at` 을 새로 찍는다 — 이 컬럼이
    `GET /insights/long-term` 의 generatedAt 이자 stale 판정 기준이다.
    `ON CONFLICT DO UPDATE` 는 SQLAlchemy 의 `onupdate` 를 타지 않으므로 SET 에 직접 넣는다.
    """
    values = {
        "period_end": period_end,
        "trend_summary": trend_summary,
        "recommendation": recommendation,
        "chart_data": chart_data,
        "model_version": model_version,
        "safety_status": safety_status,
    }
    stmt = pg_insert(LongTermFeedback).values(
        user_id=user_id, period_type=period_type, period_start=period_start, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            LongTermFeedback.user_id,
            LongTermFeedback.period_type,
            LongTermFeedback.period_start,
        ],
        set_={**values, "updated_at": func.now()},
    ).returning(LongTermFeedback.id)
    long_term_feedback_id = db.execute(stmt).scalar_one()
    db.flush()
    return long_term_feedback_id


def replace_sources(
    db: Session,
    *,
    long_term_feedback_id: uuid.UUID,
    daily_feedback_ids: Iterable[uuid.UUID],
) -> None:
    """근거 링크를 지우고 이번 근거로 다시 만든다. flush 까지만.

    지우지 않으면 재실행 때 같은 근거가 쌓이거나, 더는 근거가 아닌 하루 피드백이 남는다.
    """
    db.execute(
        delete(LongTermFeedbackSource).where(
            LongTermFeedbackSource.long_term_feedback_id == long_term_feedback_id
        )
    )
    rows = [
        {"long_term_feedback_id": long_term_feedback_id, "daily_feedback_id": daily_feedback_id}
        for daily_feedback_id in daily_feedback_ids
    ]
    if rows:
        db.execute(insert(LongTermFeedbackSource), rows)
    db.flush()


def delete_for_period(
    db: Session,
    *,
    user_id: uuid.UUID,
    period_type: FeedbackPeriodType,
    period_start: date,
) -> None:
    """그 사용자의 같은 (기간 유형 · 시작일) 행을 지운다 (근거 링크는 FK CASCADE). 없으면 아무 일도 없다.

    데이터가 부족해진 기간의 낡은 추세 문장을 남기지 않기 위해서다. 다른 시작일의 과거 행은
    건드리지 않는다. user_id 조건이 빠지면 다른 사용자의 행까지 지운다.
    """
    db.execute(
        delete(LongTermFeedback).where(
            LongTermFeedback.user_id == user_id,
            LongTermFeedback.period_type == period_type,
            LongTermFeedback.period_start == period_start,
        )
    )
    db.flush()
