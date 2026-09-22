"""식사와 그 구성 요소."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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
    is_recalculation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """사용자가 음식을 고쳐서 다시 분석 중인지. FE 는 최초 분석과 재분석의 문구를 다르게 띄운다."""

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
    """사용자 확인 전에는 NULL. **g 으로 환산된 값만 담는다** — "2개" 처럼 환산 근거가
    없는 단위면 사용자가 확인했어도 NULL 이다. 그 경우 양의 진실은 아래 두 컬럼이다."""

    confirmed_amount: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    """사용자가 입력한 양의 숫자. 확인 전에는 NULL."""
    confirmed_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """그 숫자의 단위("g" · "개" · "ml"). `confirmed_amount_g` 는 이걸 환산한 결과다.

    **확인 여부의 센티넬은 `confirmed_amount` 다** — `confirmed_amount_g` 는 환산된
    값만 담아 "2개" 로 확인한 항목도 NULL 이라, 확인 전과 구분되지 않는다.

    사용자 입력을 `raw_ai_result` 에 섞어 두면 읽는 쪽이 출처(MODEL/USER)와 수정
    이력에 따라 다른 자리를 뒤져야 해서 컬럼으로 뺐다. `GET /meals/{mealId}` 의
    `amount` · `unit` 은 **확인된 항목이면** 여기서 나온다. 확인 전 `source=MODEL`
    항목은 아직 `estimated_amount_g`(g 환산) 또는 `raw_ai_result`(환산 불가)를
    봐야 한다 — 그 갈래는 워커 구현 시 정리 대상이다(`raw_ai_result` 참고)."""
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    source: Mapped[MealItemSource] = mapped_column(
        pg_enum(MealItemSource, "meal_item_source"),
        nullable=False,
        server_default=MealItemSource.MODEL.value,
    )
    raw_ai_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    """AI 응답 원본 **전용**. 사용자 입력은 절대 섞지 않는다.

    `source=USER` 행은 AI 가 인식한 적이 없으므로 NULL 이다. 사용자가 입력한 양은
    출처와 무관하게 `confirmed_amount` · `confirmed_unit` 에 들어간다 — 읽는 쪽이
    출처와 수정 이력에 따라 다른 자리를 뒤지지 않게 하려는 것이다.

    아직 확인되지 않은 `source=MODEL` 항목의 AI 추정 양은 예외다: g 으로 환산되면
    `estimated_amount_g` 에, 환산이 안 되면("2개") 여기 말고 갈 곳이 없어 워커가
    이 JSONB 에 남긴다(`worker/jobs/analyze_meal.py` 7단계). 그 규약은 워커를
    구현할 때 확정한다 — `estimated_amount` · `estimated_unit` 컬럼으로 빼면
    읽기가 한 갈래로 줄지만, 워커가 붙은 뒤에 바꾸면 백필이 필요하다.
    """

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
