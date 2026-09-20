"""식사와 그 구성 요소."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import MealItemSource, MealStatus, MealType, pg_enum
from app.models.mixins import created_at, uuid_pk


class Meal(Base):
    """한 끼 자체. 사진/텍스트 입력, 식사 시각, 종류, 분석 진행 상태."""

    __tablename__ = "meals"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    medication_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("medication_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    """식사 당시 투약 단계. PRE_DOSE 사용자도 빈 스냅샷이 하나 붙는다."""

    meal_type: Mapped[MealType] = mapped_column(pg_enum(MealType, "meal_type"), nullable=False)
    image_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    """S3 오브젝트 키. presigned URL 은 런타임에 만든다 — 키를 로그에 남기지 않는다."""
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    eaten_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[MealStatus] = mapped_column(
        pg_enum(MealStatus, "meal_status"),
        nullable=False,
        server_default=MealStatus.ANALYZING.value,
    )
    created_at: Mapped[datetime] = created_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """NULL 이면 살아있는 식사. soft delete — 값이 채워지면 삭제된 것으로 취급한다."""

    items: Mapped[list["MealItem"]] = relationship(
        back_populates="meal", cascade="all, delete-orphan"
    )
    satiety_log: Mapped["SatietyLog | None"] = relationship(
        back_populates="meal", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        CheckConstraint(
            "image_key IS NOT NULL OR raw_text IS NOT NULL",
            name="input_present",
        ),
        # 홈 화면(오늘 끼니)과 기간별 조회가 같은 인덱스를 탄다.
        # 삭제된 식사는 거의 모든 조회에서 제외되므로 partial index 로 좁힌다.
        Index(
            "ix_meals_user_id_eaten_at",
            "user_id",
            text("eaten_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class MealItem(Base):
    """사진에서 인식된 음식 1건. 한 끼에서 밥·고기·계란이 나오면 3행이 생긴다."""

    __tablename__ = "meal_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    meal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    food_ref_id: Mapped[str | None] = mapped_column(
        ForeignKey("food_refs.id", ondelete="RESTRICT"), nullable=True
    )
    """공공 DB 음식 ID. AI 가 매칭하지 못한 음식은 NULL."""

    original_food_name: Mapped[str] = mapped_column(String(255), nullable=False)
    """최초 AI 추정 음식명."""
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    """최종 확정 음식명."""

    estimated_amount_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    confirmed_amount_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    """사용자 확인 전에는 NULL."""
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    source: Mapped[MealItemSource] = mapped_column(
        pg_enum(MealItemSource, "meal_item_source"),
        nullable=False,
        server_default=MealItemSource.MODEL.value,
    )
    raw_ai_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    meal: Mapped["Meal"] = relationship(back_populates="items")
    corrections: Mapped[list["UserCorrection"]] = relationship(
        back_populates="meal_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"),
    )


class UserCorrection(Base):
    """AI 인식값을 사용자가 고친 변경 전/후. AI 분석 성능 평가에 쓴다."""

    __tablename__ = "user_corrections"

    id: Mapped[uuid.UUID] = uuid_pk()
    meal_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meal_items.id", ondelete="CASCADE"), nullable=False, index=True
    )

    original_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    corrected_value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    corrected_at: Mapped[datetime] = created_at()

    meal_item: Mapped["MealItem"] = relationship(back_populates="corrections")


class SatietyLog(Base):
    """식전·식후 포만감과 다시 허기를 느낀 시간. Satiety 평가의 입력."""

    __tablename__ = "satiety_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    meal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    satiety_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    satiety_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hunger_return_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    meal: Mapped["Meal"] = relationship(back_populates="satiety_log")

    __table_args__ = (
        CheckConstraint(
            "satiety_before IS NULL OR satiety_before BETWEEN 0 AND 100",
            name="satiety_before_range",
        ),
        CheckConstraint(
            "satiety_after IS NULL OR satiety_after BETWEEN 0 AND 100",
            name="satiety_after_range",
        ),
    )
