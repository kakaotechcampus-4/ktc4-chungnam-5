"""medication_records · medication_snapshots 테이블 접근.

지금은 onboardingStatus 판정과 식사에 박제된 단계 조회뿐이다. POST /medications
작업에서 나머지가 붙는다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MedicationStage
from app.models.medication import MedicationRecord, MedicationSnapshot


def get_current(db: Session, user_id: uuid.UUID) -> MedicationRecord | None:
    """진행 중인 투약 기록. effective_to 가 NULL 인 행이 현재 상태다.

    부분 UNIQUE 인덱스 uq_medication_records_current 가 이 행이 둘이 되는 걸 막는다.
    """
    stmt = select(MedicationRecord).where(
        MedicationRecord.user_id == user_id,
        MedicationRecord.effective_to.is_(None),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_snapshot_stage(db: Session, snapshot_id: uuid.UUID) -> MedicationStage:
    """식사에 박제된 투약 단계.

    `meals.medication_snapshot_id` 는 NOT NULL + FK RESTRICT 라 행이 반드시 있다 —
    없으면 데이터가 깨진 것이므로 조용히 넘기지 않고 죽는다.
    """
    stmt = select(MedicationSnapshot.stage).where(MedicationSnapshot.id == snapshot_id)
    return db.execute(stmt).scalar_one()
