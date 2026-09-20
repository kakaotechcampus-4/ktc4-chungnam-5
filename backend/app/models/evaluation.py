"""Q/Q/S 평가 결과.

total_score 도 *_weight 도 없다. 단계별 차이는 곱셈 가중치가 아니라 단계별
기준선의 엄격함으로 구현하고, 그 기준선은 코드(stage_profile)에 있다 (D9).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import MedicationStage, pg_enum
from app.models.mixins import created_at, uuid_pk


class QQSEvaluation(Base):
    """식사별 Quantity·Quality·Satiety 점수. 재평가하면 덮어쓴다 (식사당 1행)."""

    __tablename__ = "qqs_evaluations"

    id: Mapped[uuid.UUID] = uuid_pk()
    meal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    stage_at_evaluation: Mapped[MedicationStage] = mapped_column(
        pg_enum(MedicationStage, "medication_stage"), nullable=False
    )

    quantity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    satiety_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    computed_at: Mapped[datetime] = created_at()
