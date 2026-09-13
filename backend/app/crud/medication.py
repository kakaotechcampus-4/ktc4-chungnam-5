"""`medication_records` · `medication_snapshots` 접근.

기록(record)은 바뀌지만 스냅샷(snapshot)은 바뀌지 않는다. 식사는 스냅샷을 보므로,
나중에 사용자가 과거 투약 기록을 고쳐도 이미 평가된 식사의 근거는 흔들리지 않는다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MedicationStage
from app.models.medication import MedicationRecord, MedicationSnapshot


# ─────────────────────────── medication_records ───────────────────────────


def get_current_record(db: Session, user_id: uuid.UUID | str) -> MedicationRecord | None:
    """현재 유효한 기록. `effective_to IS NULL` 인 행은 사용자당 하나다(부분 유니크)."""
    return db.scalar(
        select(MedicationRecord).where(
            MedicationRecord.user_id == user_id,
            MedicationRecord.effective_to.is_(None),
        )
    )


def list_records(db: Session, user_id: uuid.UUID | str) -> list[MedicationRecord]:
    return list(
        db.scalars(
            select(MedicationRecord)
            .where(MedicationRecord.user_id == user_id)
            .order_by(MedicationRecord.effective_from.desc())
        ).all()
    )


def close_record(db: Session, record: MedicationRecord, effective_to: dt.date) -> None:
    """기록을 닫는다. 새 기록을 만들기 전에 먼저 불러야 한다.

    `effective_to IS NULL` 인 행이 둘이면 '현재 단계' 판정이 모호해진다.
    """
    record.effective_to = effective_to
    db.add(record)


def add_record(
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


# ─────────────────────────── medication_snapshots ───────────────────────────


def get_snapshot(db: Session, snapshot_id: uuid.UUID | str) -> MedicationSnapshot | None:
    return db.get(MedicationSnapshot, snapshot_id)


def add_snapshot(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    stage: MedicationStage,
    source_record: MedicationRecord | None = None,
) -> MedicationSnapshot:
    """식사에 붙일 스냅샷. 만든 뒤에는 바꾸지 않는다.

    PRE_DOSE 사용자는 투약 기록이 없어 `source_record` 가 None 이다 —
    그래도 스냅샷은 하나 붙는다. 약제 컬럼이 전부 NULL 허용인 게 이 때문이다.
    """
    snapshot = MedicationSnapshot(
        user_id=user_id,
        stage=stage,
        source_record_id=source_record.id if source_record else None,
        drug_name=source_record.drug_name if source_record else None,
        dose_mg=source_record.dose_mg if source_record else None,
        injection_count=source_record.injection_count if source_record else None,
    )
    db.add(snapshot)
    return snapshot
