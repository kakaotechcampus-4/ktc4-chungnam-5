"""`medical_handoff_logs` 접근.

가드레일이 의료 질문을 감지하면 여기 남긴다(규칙 1). 기록은 지우지 않는다 —
"차단했다" 는 사실 자체가 감사 대상이다.

`original_input` 에 사용자의 원본 질문이 들어간다. **로그로 흘리지 않는다** —
약제·용량이 섞여 있을 수 있다(규칙 6).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import HandoffStatus, HandoffTriggerType
from app.models.handoff import MedicalHandoffLog


def get(db: Session, log_id: uuid.UUID | str) -> MedicalHandoffLog | None:
    return db.get(MedicalHandoffLog, log_id)


def add(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    trigger_type: HandoffTriggerType,
    detected_at: dt.datetime,
    meal_id: uuid.UUID | str | None = None,
    user_state_id: uuid.UUID | str | None = None,
    trigger_reason: str | None = None,
    original_input: str | None = None,
) -> MedicalHandoffLog:
    log = MedicalHandoffLog(
        user_id=user_id,
        meal_id=meal_id,
        user_state_id=user_state_id,
        trigger_type=trigger_type,
        trigger_reason=trigger_reason,
        original_input=original_input,
        detected_at=detected_at,
    )
    db.add(log)
    return log


def list_pending(db: Session, *, limit: int = 100) -> list[MedicalHandoffLog]:
    """미처리 건. 이 테이블의 주 용도이고 인덱스도 여기 맞춰져 있다."""
    return list(
        db.scalars(
            select(MedicalHandoffLog)
            .where(MedicalHandoffLog.status == HandoffStatus.PENDING)
            .order_by(MedicalHandoffLog.detected_at)
            .limit(limit)
        ).all()
    )


def list_for_user(db: Session, user_id: uuid.UUID | str) -> list[MedicalHandoffLog]:
    return list(
        db.scalars(
            select(MedicalHandoffLog)
            .where(MedicalHandoffLog.user_id == user_id)
            .order_by(MedicalHandoffLog.detected_at.desc())
        ).all()
    )


def review(
    db: Session,
    log: MedicalHandoffLog,
    *,
    status: HandoffStatus,
    reviewer_note: str | None = None,
    reviewed_at: dt.datetime | None = None,
) -> None:
    log.status = status
    log.reviewer_note = reviewer_note
    log.reviewed_at = reviewed_at or dt.datetime.now(dt.timezone.utc)
    db.add(log)
