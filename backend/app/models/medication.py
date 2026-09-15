"""투약 기록과 식사 시점 스냅샷.

`medication_records` 는 현재 진행 중인 투약 상태를 기간(effective_from ~ to)으로 들고,
`medication_snapshots` 는 그 시점의 값을 얼려 식사에 붙인다. 사용자가 나중에 과거
투약 기록을 수정해도 이미 평가된 식사의 단계 맥락은 변하지 않는다.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import MedicationStage, pg_enum
from app.models.mixins import created_at, updated_at, uuid_pk


class MedicationRecord(Base):
    """약물명·용량·투약 회차·단계. 식사 평가의 핵심 Context."""

    __tablename__ = "medication_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    drug_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dose_mg: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    injection_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[MedicationStage] = mapped_column(
        pg_enum(MedicationStage, "medication_stage"), nullable=False
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    """종료일. 현재 상태면 NULL."""

    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        # "현재 단계"인 행이 둘이면 단계 판정이 모호해진다.
        Index(
            "uq_medication_records_current",
            "user_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
        Index("ix_medication_records_user_id_effective_from", "user_id", "effective_from"),
    )


class MedicationSnapshot(Base):
    """한 끼 식사에 매칭될 투약 정보 스냅샷. 생성 후 변경하지 않는다."""

    __tablename__ = "medication_snapshots"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("medication_records.id", ondelete="SET NULL"), nullable=True
    )
    """어느 기록에서 떠온 스냅샷인지. 원본이 지워져도 스냅샷은 남는다."""

    # PRE_DOSE 사용자는 투약 기록이 없다. 약제 관련 컬럼은 그래서 NULL 허용.
    drug_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dose_mg: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    injection_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage: Mapped[MedicationStage] = mapped_column(
        pg_enum(MedicationStage, "medication_stage"), nullable=False
    )

    created_at: Mapped[datetime] = created_at()

    source_record: Mapped["MedicationRecord | None"] = relationship()
