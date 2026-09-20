"""피드백 3계층과 생성 근거 추적.

meal → daily → long_term 순으로 쌓이고, 각 계층은 자기가 어떤 하위 피드백을
근거로 만들어졌는지 source 테이블에 남긴다.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import FeedbackPeriodType, SafetyStatus, pg_enum
from app.models.mixins import created_at, uuid_pk


class MealFeedback(Base):
    """한 끼의 QQS 결과를 설명하고 다음 끼니 행동을 제안한다. 식사당 1행."""

    __tablename__ = "meal_feedbacks"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestions: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    safety_status: Mapped[SafetyStatus] = mapped_column(
        pg_enum(SafetyStatus, "safety_status"),
        nullable=False,
        server_default=SafetyStatus.REVIEW_REQUIRED.value,
    )
    """그대로 노출해도 되는지. 기본값이 REVIEW_REQUIRED 인 건 의도된 것이다 —
    가드레일을 통과해야만 SAFE 가 된다 (규칙 1)."""

    created_at: Mapped[datetime] = created_at()


class DailyFeedback(Base):
    """하루치 종합 평가. 홈의 오늘 요약·일별 기록에 쓴다."""

    __tablename__ = "daily_feedbacks"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    feedback_date: Mapped[date] = mapped_column(Date, nullable=False)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    satiety_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safety_status: Mapped[SafetyStatus] = mapped_column(
        pg_enum(SafetyStatus, "safety_status"),
        nullable=False,
        server_default=SafetyStatus.REVIEW_REQUIRED.value,
    )
    created_at: Mapped[datetime] = created_at()

    sources: Mapped[list["DailyFeedbackSource"]] = relationship(
        back_populates="daily_feedback", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("user_id", "feedback_date"),)


class LongTermFeedback(Base):
    """주간·월간 Q/Q/S 추이와 장기 행동 제안."""

    __tablename__ = "long_term_feedbacks"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    period_type: Mapped[FeedbackPeriodType] = mapped_column(
        pg_enum(FeedbackPeriodType, "feedback_period_type"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    trend_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    chart_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    """대시보드용 집계 데이터."""

    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safety_status: Mapped[SafetyStatus] = mapped_column(
        pg_enum(SafetyStatus, "safety_status"),
        nullable=False,
        server_default=SafetyStatus.REVIEW_REQUIRED.value,
    )
    created_at: Mapped[datetime] = created_at()

    sources: Mapped[list["LongTermFeedbackSource"]] = relationship(
        back_populates="long_term_feedback", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "period_type", "period_start"),
        Index("ix_long_term_feedbacks_user_id_period_start", "user_id", "period_start"),
    )


class DailyFeedbackSource(Base):
    """하나의 daily_feedback 이 어떤 meal_feedback 들에서 나왔는지."""

    __tablename__ = "daily_feedback_sources"

    daily_feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("daily_feedbacks.id", ondelete="CASCADE"), primary_key=True
    )
    meal_feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meal_feedbacks.id", ondelete="CASCADE"), primary_key=True
    )

    daily_feedback: Mapped["DailyFeedback"] = relationship(back_populates="sources")


class LongTermFeedbackSource(Base):
    """하나의 long_term_feedback 이 어떤 daily_feedback 들에서 나왔는지."""

    __tablename__ = "long_term_feedback_sources"

    long_term_feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("long_term_feedbacks.id", ondelete="CASCADE"), primary_key=True
    )
    daily_feedback_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("daily_feedbacks.id", ondelete="CASCADE"), primary_key=True
    )

    long_term_feedback: Mapped["LongTermFeedback"] = relationship(back_populates="sources")
