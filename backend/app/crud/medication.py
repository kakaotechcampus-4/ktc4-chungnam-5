"""medication_records 테이블 접근.

지금은 onboardingStatus 판정에 필요한 함수 하나뿐이다. POST /medications 작업에서
나머지가 붙는다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.medication import MedicationRecord


def get_current(db: Session, user_id: uuid.UUID) -> MedicationRecord | None:
    """진행 중인 투약 기록. effective_to 가 NULL 인 행이 현재 상태다.

    부분 UNIQUE 인덱스 uq_medication_records_current 가 이 행이 둘이 되는 걸 막는다.
    """
    stmt = select(MedicationRecord).where(
        MedicationRecord.user_id == user_id,
        MedicationRecord.effective_to.is_(None),
    )
    return db.execute(stmt).scalar_one_or_none()

