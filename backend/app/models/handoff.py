"""의료 판단 핸드오프 로그.

용량 증량·감량·단약·처방 판단은 하지 않는다. 감지되면 차단하고 여기에 기록한다 (규칙 1).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import HandoffStatus, HandoffTriggerType, pg_enum
from app.models.mixins import uuid_pk


class MedicalHandoffLog(Base):
    __tablename__ = "medical_handoff_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meals.id", ondelete="SET NULL"), nullable=True
    )
    user_state_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_states.id", ondelete="SET NULL"), nullable=True
    )

    trigger_type: Mapped[HandoffTriggerType] = mapped_column(
        pg_enum(HandoffTriggerType, "handoff_trigger_type"), nullable=False
    )
    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    """사용자의 원본 질문. 민감 정보라 로그로는 흘리지 않고 이 컬럼에만 둔다."""

    status: Mapped[HandoffStatus] = mapped_column(
        pg_enum(HandoffStatus, "handoff_status"),
        nullable=False,
        server_default=HandoffStatus.PENDING.value,
    )
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 미처리 건 조회가 주 용도다.
        Index("ix_medical_handoff_logs_status_detected_at", "status", "detected_at"),
    )
