"""`medication_snapshots` 접근.

식사는 기록(record)이 아니라 스냅샷을 본다. 나중에 사용자가 과거 투약 기록을 고쳐도
이미 평가된 식사의 근거가 흔들리지 않게 하기 위해서다. **만든 뒤에는 바꾸지 않는다.**
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.enums import MedicationStage
from app.models.medication import MedicationRecord, MedicationSnapshot


def get(db: Session, snapshot_id: uuid.UUID | str) -> MedicationSnapshot | None:
    return db.get(MedicationSnapshot, snapshot_id)


def add(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    stage: MedicationStage,
    source_record: MedicationRecord | None = None,
) -> MedicationSnapshot:
    """식사에 붙일 스냅샷.

    PRE_DOSE 사용자는 투약 기록이 없어 `source_record` 가 None 이다 — 그래도 스냅샷은
    하나 붙는다. 약제 컬럼이 전부 NULL 허용인 게 이 때문이다.
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
