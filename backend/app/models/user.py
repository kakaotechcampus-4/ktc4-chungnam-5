"""사용자·목표·상태 기록."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import GoalStatus, pg_enum
from app.models.mixins import created_at, updated_at, uuid_pk


class User(Base):
    """로그인한 사용자. 대부분의 데이터가 user_id 로 여기에 연결된다."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()

    auth_provider: Mapped[str] = mapped_column(String(32), nullable=True)
    # 카카오는 이메일이 선택 동의라 없을 수 있다. 계정 식별은 provider_user_id 로 한다.
    provider_user_id: Mapped[str] = mapped_column(String(128), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)

    restrictions: Mapped[dict | list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    """알레르기·못 먹는 음식."""

    fcm_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fcm_token_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notification_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    baseline_meal_kcal: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    """평소 한 끼 열량(kcal). Quantity 감소폭의 분모 (D7)."""

    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    goals: Mapped[list["UserGoal"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    states: Mapped[list["UserState"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("auth_provider", "provider_user_id"),
        # 같은 기기에 두 계정이 붙으면 남의 식사 알림이 뜬다.
        Index(
            "uq_users_fcm_token_active",
            "fcm_token",
            unique=True,
            postgresql_where=text("fcm_token IS NOT NULL"),
        ),
    )


class UserGoal(Base):
    """목표 체중. 현재 체중과 비교해 진행 상황을 보여준다."""

    __tablename__ = "user_goals"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    target_weight_kg: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    status: Mapped[GoalStatus] = mapped_column(
        pg_enum(GoalStatus, "goal_status"),
        nullable=False,
        server_default=GoalStatus.ACTIVE.value,
    )

    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    user: Mapped["User"] = relationship(back_populates="goals")

    __table_args__ = (
        # 진행 중인 목표는 사용자당 하나.
        Index(
            "uq_user_goals_active",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class UserState(Base):
    """사용자가 입력한 체중·식욕·GI 증상. 기간별 변화 분석의 원본."""

    __tablename__ = "user_states"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    appetite_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    gi_symptoms: Mapped[dict | list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    """위장관 증상 목록 및 정도."""
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()

    user: Mapped["User"] = relationship(back_populates="states")

    __table_args__ = (
        # "최신 상태" 조회와 기간 조회가 같은 인덱스를 탄다.
        Index("ix_user_states_user_id_recorded_at", "user_id", text("recorded_at DESC")),
    )
