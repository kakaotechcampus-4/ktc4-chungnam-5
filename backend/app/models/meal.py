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

    # 양은 여섯 컬럼이다 — 쌍 두 개 + 각 쌍의 g 환산값. 규칙 하나로 읽는다:
    #
    #     확인됐으면(`confirmed_amount is not None`) confirmed_*, 아니면 estimated_*
    #
    # 각 쌍은 **사용자·AI 가 말한 그대로(숫자 + 단위)** 를 담고, `*_amount_g` 는 그걸
    # g 으로 환산한 결과다. "2개" 처럼 환산 근거가 없으면 `*_amount_g` 만 NULL 이 되고
    # 숫자·단위는 남는다 — 그래서 `*_amount_g` 는 "양이 있는가" 의 기준이 될 수 없다.
    estimated_amount: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    """AI 가 추정한 양의 숫자."""
    estimated_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """그 숫자의 단위("g" · "개" · "ml"). `estimated_amount_g` 는 이걸 환산한 결과다.

    `source=USER` 행은 AI 가 추정한 적이 없으므로 NULL 이다."""
    estimated_amount_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    """AI 추정 양의 g 환산값. **사용자 수정에 덮이지 않는다** — 인식 성능 평가의 기준이다."""

    confirmed_amount: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    """사용자가 입력한 양의 숫자. 확인 전에는 NULL.

    **확인 여부의 센티넬이 이 컬럼이다** — `confirmed_amount_g` 는 환산된 값만 담아
    "2개" 로 확인한 항목도 NULL 이라, 확인 전과 구분되지 않는다."""
    confirmed_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """그 숫자의 단위. `confirmed_amount_g` 는 이걸 환산한 결과다.

    `GET /meals/{mealId}` 의 `amount` · `unit` 은 위 규칙 한 줄로 나온다 — 출처
    (MODEL/USER)도 수정 이력도 볼 필요가 없다."""
    confirmed_amount_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    """사용자 확인 양의 g 환산값. 확인 전이거나 환산 불가면 NULL."""
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    source: Mapped[MealItemSource] = mapped_column(
        pg_enum(MealItemSource, "meal_item_source"),
        nullable=False,
        server_default=MealItemSource.MODEL.value,
    )
    raw_ai_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    """AI 응답 원본 **전용**. 파싱해서 컬럼에 나눠 담고 난 뒤 버리지 않고 두는 자리다.

    `source=USER` 행은 AI 가 인식한 적이 없으므로 NULL 이다.

    **여기서 양을 읽지 않는다.** 음식명·신뢰도·양은 전부 컬럼에 있고(위 양 규칙 참고),
    이 JSONB 는 "AI 가 원래 뭐라고 했나" 를 되짚거나 파싱 규칙이 바뀌었을 때 다시
    읽기 위한 것이다. 키 모양은 AI 응답 스키마를 따라 바뀔 수 있으므로 읽는 쪽이
    의존해서는 안 된다.
    """

    # 사용자가 직접 적어 넣은 영양성분(`PUT /meals/{mealId}/items/{itemId}/nutrition`
    # 의 `manual`). 여섯 컬럼이 `food_refs` 와 같은 타입·정밀도다 — 두 출처를 Q/Q/S
    # 채점기가 한 규칙으로 합산해야 하기 때문이다.
    #
    # ⚠️ **기준량이 아니라 섭취량 기준 총량이다.** `food_refs` 의 성분값은
    # `serving_size` 기준이라 먹은 양만큼 환산해야 하지만, 이쪽은 사용자가 "내가
    # 먹은 만큼" 을 적은 값이라 그대로 쓴다. 그래서 **양이 바뀌면 거짓이 된다** —
    # `services.meal.update_items` 가 이름·양이 실제로 바뀌면 여기를 NULL 로 되돌린다.
    #
    # 영양정보 출처(`nutritionSource`)는 저장하지 않고 이 컬럼들로 유도한다:
    # 하나라도 차 있으면 USER_INPUT, 아니면 `food_ref_id` 환산이 되면 PUBLIC_DB.
    #
    # ⚠️ **그 유도를 직접 하지 말 것.** 출처 컬럼을 두지 않는 이 설계는 유도하는
    # 코드가 한 곳일 때만 성립한다 — `services.meal.item_nutrition` 이 그 한 곳이고,
    # 이 필드를 내보내는 응답은 전부 거기를 거쳐야 한다. 두 곳이 각자 계산하면 같은
    # 항목이 화면마다 다른 출처로 보인다.
    manual_kcal: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    manual_protein_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    manual_fat_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    manual_carb_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    manual_fiber_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    manual_sodium_mg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)

    meal: Mapped["Meal"] = relationship(back_populates="items")
    corrections: Mapped[list["UserCorrection"]] = relationship(
        back_populates="meal_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint(
            " AND ".join(
                f"({name} IS NULL OR {name} >= 0)"
                for name in (
                    "manual_kcal",
                    "manual_protein_g",
                    "manual_fat_g",
                    "manual_carb_g",
                    "manual_fiber_g",
                    "manual_sodium_mg",
                )
            ),
            name="manual_nutrition_non_negative",
        ),
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
