"""`medication_records` 접근.

**현재 유효한 기록은 사용자당 하나다** — `effective_to IS NULL` 인 행에 부분 유니크
인덱스(`uq_medication_records_current`)가 걸려 있다. 둘이면 현재 단계 판정이 모호해진다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MedicationStage
from app.models.medication import MedicationRecord


def get_current(db: Session, user_id: uuid.UUID | str) -> MedicationRecord | None:
    return db.scalar(
        select(MedicationRecord).where(
            MedicationRecord.user_id == user_id,
            MedicationRecord.effective_to.is_(None),
        )
    )


def close(db: Session, record: MedicationRecord, effective_to: dt.date) -> None:
    """기록을 닫는다. **새 기록을 만들기 전에 먼저 불러야 한다.**"""
    record.effective_to = effective_to
    db.add(record)


def add(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    drug_name: str,
    dose_mg: Decimal,
    injection_count: int,
    stage: MedicationStage,
    effective_from: dt.date,
) -> MedicationRecord:
    record = MedicationRecord(
        user_id=user_id,
        drug_name=drug_name,
        dose_mg=dose_mg,
        injection_count=injection_count,
        stage=stage,
        effective_from=effective_from,
    )
    db.add(record)
    return record
