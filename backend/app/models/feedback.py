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
from app.models.mixins import created_at, updated_at, uuid_pk


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
    suggestions: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    """다음 끼니 제안. **AI 계약(`ai-stub/schemas.py::Suggestion`) 모양 그대로** 담는다.

        [{"foodName": "두부 반 모", "advice": "단백질을 10g 더 채워요",
          "candidateFoodRefId": "KFD_01023"}]

    `nutrients` 는 **여기 없다.** AI 계약이 "BE 가 food_refs 에서 채운다" 고 적어
    두었고, 저장해 두면 `food_refs` 가 갱신될 때 낡는다. 읽을 때
    `candidateFoodRefId` 로 조회해 채운다.

    TEXT 가 아니라 JSONB 인 이유: 명세 응답이 객체 배열이고 AI 도 배열로 준다.
    문자열로 담으면 컬럼 타입이 내용을 안 말해 주고, 깨진 JSON 이 들어가도 DB 가
    안 막는다. `raw_ai_result` 가 이미 JSONB 라 선례도 있다."""
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
    updated_at: Mapped[datetime] = updated_at()
    """마지막으로 생성/재생성된 시각. `created_at` 은 최초 INSERT 시각에 고정된다.

    **주의**: `ON CONFLICT DO UPDATE` 로 upsert 하면 SQLAlchemy 의 `onupdate` 가
    자동으로 안 걸린다 — `crud/daily_feedback.py::upsert` 의 `set_` 에
    `updated_at=func.now()` 를 직접 넣어야 한다."""

    sources: Mapped[list["DailyFeedbackSource"]] = relationship(
        back_populates="daily_feedback", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("user_id", "feedback_date"),)


ALL_PERIOD_START = date(1970, 1, 1)
"""period_type=ALL 행의 period_start 고정값.

실제 분석 시작일이 아니라, UNIQUE(user_id, period_type, period_start) 를
사용자당 한 행으로 만드는 키다. `date.min` 은 쓰지 않는다 — KST 로 만든
aware datetime 을 UTC 로 바꾸면 범위를 벗어나 OverflowError 가 난다.
"""


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
    updated_at: Mapped[datetime] = updated_at()
    """마지막으로 생성/재생성된 시각. `GET /insights/long-term`의 `generatedAt`이
    이 값을 쓴다 — `created_at`은 최초 INSERT 시각에 고정돼 재확정 때 안 바뀐다.

    **주의**: `ON CONFLICT DO UPDATE`로 upsert 하면 SQLAlchemy 의 `onupdate`가
    자동으로 안 걸린다 — SET 목록에 `updated_at=func.now()`를 직접 넣어야 한다
    (`worker/jobs/feedback_long.py` 구현 시 챙길 것)."""

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
