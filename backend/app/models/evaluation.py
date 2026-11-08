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

    # 평소 한 끼 대비 섭취 비율(%). Quantity 점수의 근거다 — 점수만으로는 과식인지 소식인지
    # 모른다. 끼니 피드백이 단계 구간과 함께 AI 에 넘긴다. 열량이나 평소 섭취량을 모르면 NULL.
    # 자릿수를 묶지 않는다 — 평소 섭취량 하한이 0 초과뿐이라 비율의 상한이 없고, 넘치면 확정이
    # 500 으로 죽는다. 소수 둘째 자리 반올림은 services 가 한다.
    intake_ratio_pct: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)

    computed_at: Mapped[datetime] = created_at()
