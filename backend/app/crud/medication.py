"""투약 기록 DB 접근.

`medication_records` 는 기간 모델이다 — `effective_from` ~ `effective_to`.
`UNIQUE (user_id) WHERE effective_to IS NULL` 부분 인덱스가 걸려 있어
"현재 상태"인 행은 사용자당 하나뿐이다. 그래서 새 행을 열기 전에 **반드시 기존
행을 닫고 flush** 해야 한다. 순서가 바뀌면 유니크 위반으로 죽는다.

규칙 5: 세션을 다루는 건 여기까지다. `services/` 는 이 함수들을 호출만 한다.
"""

import uuid
from datetime import date
from decimal import Decimal

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


def get_dosing_start_date(db: Session, user_id: uuid.UUID) -> date | None:
    """전체 투약 시작일.

    `medication_records` 에 `started_at` 컬럼이 없어 **가장 오래된 행의
    `effective_from`** 으로 유도한다 (`docs/be-medication-domain.md` 불일치 2번, A안).

    시작일 계산이 이 함수 하나에 격리돼 있다. 나중에 B안(`dosing_started_at`
    컬럼 추가)으로 바꾸면 여기만 고치면 된다.
    """
    stmt = (
        select(MedicationRecord.effective_from)
        .where(MedicationRecord.user_id == user_id)
        .order_by(MedicationRecord.effective_from.asc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_first(db: Session, user_id: uuid.UUID) -> MedicationRecord | None:
    """가장 오래된 행. 그 `effective_from` 이 곧 전체 투약 시작일이다.

    사용자가 회차 스테퍼로 시작일을 바꾸면 이 행의 날짜를 옮긴다.
    """
    stmt = (
        select(MedicationRecord)
        .where(MedicationRecord.user_id == user_id)
        .order_by(MedicationRecord.effective_from.asc(), MedicationRecord.created_at.asc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def list_doses_desc(
    db: Session,
    user_id: uuid.UUID,
    *,
    exclude_id: uuid.UUID | None = None,
) -> list[tuple[str, Decimal]]:
    """(약물, 용량) 목록. **최신 회차가 먼저다.**

    단계 판정이 최근부터 거꾸로 훑기 때문에 내림차순으로 준다
    (`services.medication.dose_context`).

    **약물로 필터링하지 않고 약물명을 같이 준다.** 약을 바꾼 지점에서 이력을 끊는 건
    호출자 몫이다 — 여기서 `drug_name = ?` 로 걸러버리면 약을 바꿨다 **되돌아온** 경우
    옛 구간과 지금 구간이 이어붙어, 몇 달 전 용량과 비교하게 된다.

    `exclude_id` 는 같은 날 재등록(정정) 때 **고치고 있는 행 자신**을 빼기 위한 것이다.
    자기 자신과 비교하면 "직전의 다른 용량"이 어긋난다.
    """
    stmt = select(MedicationRecord.drug_name, MedicationRecord.dose_mg).where(
        MedicationRecord.user_id == user_id
    )
    if exclude_id is not None:
        stmt = stmt.where(MedicationRecord.id != exclude_id)
    stmt = stmt.order_by(
        MedicationRecord.effective_from.desc(), MedicationRecord.created_at.desc()
    )
    return [(row.drug_name, row.dose_mg) for row in db.execute(stmt)]


def list_history(db: Session, user_id: uuid.UUID) -> list[MedicationRecord]:
    """투약 이력 전체. 오래된 순.

    별도 이벤트 테이블이 없다 — 행 하나가 곧 투약 1회다.
    """
    stmt = (
        select(MedicationRecord)
        .where(MedicationRecord.user_id == user_id)
        .order_by(MedicationRecord.effective_from.asc(), MedicationRecord.created_at.asc())
    )
    return list(db.execute(stmt).scalars())


def close_current(db: Session, record: MedicationRecord, effective_to: date) -> MedicationRecord:
    """현재 행을 닫는다. 부분 유니크 인덱스를 즉시 풀어주려고 flush 까지 한다."""
    record.effective_to = effective_to
    db.flush()
    return record


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    drug_name: str,
    dose_mg: Decimal,
    injection_count: int,
    stage: MedicationStage,
    effective_from: date,
) -> MedicationRecord:
    """새 투약 상태 행을 연다. `effective_to` 는 NULL — 이게 곧 '현재'다."""
    record = MedicationRecord(
        user_id=user_id,
        drug_name=drug_name,
        dose_mg=dose_mg,
        injection_count=injection_count,
        stage=stage,
        effective_from=effective_from,
        effective_to=None,
    )
    db.add(record)
    db.flush()
    return record


def add_snapshot(
    db: Session,
    *,
    user_id: uuid.UUID,
    source: MedicationRecord | None,
) -> MedicationSnapshot:
    """식사에 붙일 투약 스냅샷을 만든다.

    **생성 후 변경하지 않는다** — 그래서 이 테이블엔 `updated_at` 이 없다.
    사용자가 나중에 투약 기록을 고쳐도 이미 평가된 식사의 단계 맥락은 변하면 안 된다.
    그러려면 참조가 아니라 **값을 복사**해야 한다.

    `source` 가 None 이면 stage=PRE_DOSE 에 약제 컬럼이 전부 NULL 인 빈 스냅샷이다.
    투약 전 사용자도 식사를 기록하고(D8), `meals.medication_snapshot_id` 가 NOT NULL
    이라 그들에게도 스냅샷이 하나 붙어야 한다.

    commit 하지 않는다. 식사 INSERT 와 한 트랜잭션이어야 한다.
    """
    snapshot = MedicationSnapshot(
        user_id=user_id,
        source_record_id=source.id if source is not None else None,
        drug_name=source.drug_name if source is not None else None,
        dose_mg=source.dose_mg if source is not None else None,
        injection_count=source.injection_count if source is not None else None,
        stage=source.stage if source is not None else MedicationStage.PRE_DOSE,
    )
    db.add(snapshot)
    db.flush()
    return snapshot
