"""투약 등록 유스케이스 통합 테스트 — 실제 Postgres 로 돌린다.

여기서 검증하는 건 **기간 전이**다. `medication_records` 에는
`UNIQUE (user_id) WHERE effective_to IS NULL` 부분 인덱스가 걸려 있어서
"이전 행 닫기 → flush → 새 행 열기" 순서가 틀리면 그 자리에서 죽는다.
순수 함수 테스트로는 절대 잡히지 않는 부분이라 컨테이너를 띄운다.

**행 하나 = 용량 변경 1건**이다 (명세: "용량 변경 자동 기록" / "용량 변경 이력").
같은 용량으로 다시 보내도 행이 늘지 않는다는 게 이 파일의 중심 주장이다.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.models.enums import DrugName, MedicationStage
from app.schemas.medication import MedicationUpsertRequest
from app.services import medication as service

TODAY = date(2026, 9, 20)


@pytest.fixture
def user_id(db: Session) -> uuid.UUID:
    user = user_crud.create(
        db,
        nickname="투약테스트",
        height_cm=Decimal("170"),
        baseline_meal_kcal=Decimal("700"),
    )
    db.flush()
    return user.id


def _req(
    dose: str, started_at: date, drug: DrugName = DrugName.WEGOVY
) -> MedicationUpsertRequest:
    return MedicationUpsertRequest(
        drug_name=drug, dose_mg=Decimal(dose), started_at=started_at
    )


def _rows(db: Session, user_id: uuid.UUID) -> int:
    return len(medication_crud.list_history(db, user_id))


# ── 행의 의미: 용량 변경 1건 ────────────────────────────────────


def test_first_registration_opens_a_record(db: Session, user_id: uuid.UUID) -> None:
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)

    current = medication_crud.get_current(db, user_id)
    assert current is not None
    assert current.dose_mg == Decimal("0.250")
    assert current.effective_from == date(2026, 9, 1)
    assert current.effective_to is None


def test_same_dose_does_not_create_a_row(db: Session, user_id: uuid.UUID) -> None:
    """같은 약·같은 용량이면 이력에 남길 변경이 없다.

    옛 모델(1회 = 1행)과 갈리는 지점이다. 명세의 `POST /medications` 는
    "용량 변경 자동 기록"이라 변경이 없으면 기록하지 않는다.
    """
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)

    assert _rows(db, user_id) == 1


def test_dose_change_closes_previous_row_the_day_before(
    db: Session, user_id: uuid.UUID
) -> None:
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)
    service.upsert(db, user_id, _req("0.5", date(2026, 9, 1)), today=TODAY)

    history = medication_crud.list_history(db, user_id)
    assert len(history) == 2
    assert history[0].effective_to == TODAY - __import__("datetime").timedelta(days=1)
    assert history[1].effective_from == TODAY
    assert history[1].effective_to is None


def test_only_one_open_record_survives(db: Session, user_id: uuid.UUID) -> None:
    """부분 UNIQUE 인덱스 위반이면 여기서 죽는다."""
    for dose in ("0.25", "0.5", "1.0", "1.7"):
        service.upsert(db, user_id, _req(dose, date(2026, 9, 1)), today=TODAY)

    open_rows = [r for r in medication_crud.list_history(db, user_id) if r.effective_to is None]
    assert len(open_rows) == 1


def test_drug_change_also_creates_a_row(db: Session, user_id: uuid.UUID) -> None:
    service.upsert(db, user_id, _req("1.0", date(2026, 9, 1)), today=TODAY)
    service.upsert(
        db, user_id, _req("5.0", date(2026, 9, 1), DrugName.MOUNJARO), today=TODAY
    )

    assert _rows(db, user_id) == 2
    assert medication_crud.get_current(db, user_id).drug_name == DrugName.MOUNJARO


# ── startedAt: 전체 투약 시작일 ────────────────────────────────


def test_started_at_moves_the_first_row(db: Session, user_id: uuid.UUID) -> None:
    """FE 의 회차 스테퍼는 시작일을 역산해서 보낸다.

    회차를 직접 받는 자리가 명세 body 에 없어서, 시작일 변경이 곧 회차 변경이다.
    """
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 13)), today=TODAY)
    assert medication_crud.get_dosing_start_date(db, user_id) == date(2026, 9, 13)

    service.upsert(db, user_id, _req("0.25", date(2026, 8, 30)), today=TODAY)

    assert medication_crud.get_dosing_start_date(db, user_id) == date(2026, 8, 30)
    assert _rows(db, user_id) == 1  # 시작일만 옮겼지 변경 이력이 아니다


def test_dose_count_is_computed_from_started_at(db: Session, user_id: uuid.UUID) -> None:
    """명세 「서버 계산 항목」 — doseCount = floor((today - startedAt) / 7) + 1.

    행을 세지 않는다. 등록을 한 번만 해도 시간이 지나면 회차가 오른다.
    """
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)

    view = service.get_current_view(db, user_id, today=TODAY)
    assert view.started_at == date(2026, 9, 1)
    assert view.dose_count == 3  # 9/1 부터 19일 = 2주 + 5일 → 3회차
    assert _rows(db, user_id) == 1


def test_future_start_date_is_rejected(db: Session, user_id: uuid.UUID) -> None:
    """미래 시작일이면 회차 공식이 0 이나 음수를 낸다."""
    with pytest.raises(service.FutureStartDateError):
        service.upsert(db, user_id, _req("0.25", date(2026, 12, 25)), today=TODAY)

    assert _rows(db, user_id) == 0


# ── 단계 판정 연동 ─────────────────────────────────────────────


def test_stage_moves_with_dose(db: Session, user_id: uuid.UUID) -> None:
    """사다리를 오르면 단계가 따라 바뀐다 (위고비 0.25 → 1.0 → 2.4)."""
    steps = [
        ("0.25", MedicationStage.INITIAL),
        ("1.0", MedicationStage.TITRATION),
        ("2.4", MedicationStage.MAINTENANCE),
    ]
    for dose, expected in steps:
        service.upsert(db, user_id, _req(dose, date(2026, 9, 1)), today=TODAY)
        assert medication_crud.get_current(db, user_id).stage is expected


def test_dose_reduction_is_marked_reduced(db: Session, user_id: uuid.UUID) -> None:
    """2.4 → 1.7 로 내리면 감량기다. 사다리 위치만 보면 TITRATION 으로 잡혔을 자리다."""
    service.upsert(db, user_id, _req("2.4", date(2026, 9, 1)), today=TODAY)
    service.upsert(db, user_id, _req("1.7", date(2026, 9, 1)), today=TODAY)

    assert medication_crud.get_current(db, user_id).stage is MedicationStage.REDUCED


def test_no_record_is_pre_dose(db: Session, user_id: uuid.UUID) -> None:
    """투약 기록이 없으면 PRE_DOSE. 에러가 아니다 — 투약 전 식사 평가가 제품 기능이다 (D8)."""
    view = service.get_current_view(db, user_id, today=TODAY)
    assert view.stage is MedicationStage.PRE_DOSE
    assert view.drug_name is None
    assert view.dose_count is None


# ── 스냅샷 ─────────────────────────────────────────────────────


def test_snapshot_freezes_current_medication(db: Session, user_id: uuid.UUID) -> None:
    """스냅샷은 만든 시점의 값을 얼린다. 원본을 고쳐도 따라 변하지 않는다."""
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)
    snapshot = service.create_snapshot_for_meal(db, user_id)

    assert snapshot.stage is MedicationStage.INITIAL
    assert snapshot.dose_mg == Decimal("0.250")

    service.upsert(db, user_id, _req("2.4", date(2026, 9, 1)), today=TODAY)
    db.refresh(snapshot)

    assert medication_crud.get_current(db, user_id).stage is MedicationStage.MAINTENANCE
    assert snapshot.stage is MedicationStage.INITIAL  # ← 얼어 있다
    assert snapshot.dose_mg == Decimal("0.250")


def test_snapshot_for_pre_dose_user_is_empty(db: Session, user_id: uuid.UUID) -> None:
    """투약 전 사용자도 빈 스냅샷이 붙는다 — meals.medication_snapshot_id 가 NOT NULL."""
    snapshot = service.create_snapshot_for_meal(db, user_id)

    assert snapshot.stage is MedicationStage.PRE_DOSE
    assert snapshot.drug_name is None
    assert snapshot.dose_mg is None
    assert snapshot.source_record_id is None


# ── 시작일 정정의 상한 ─────────────────────────────────────────


def _open_two_records(db: Session, user_id: uuid.UUID) -> None:
    """[(09-01, 09-14, 0.25), (09-15, None, 0.5)] 을 만든다."""
    service.upsert(db, user_id, _req("0.25", date(2026, 9, 1)), today=date(2026, 9, 10))
    service.upsert(db, user_id, _req("0.5", date(2026, 9, 1)), today=date(2026, 9, 15))


def test_started_at_cannot_pass_the_first_dose_change(db: Session, user_id: uuid.UUID) -> None:
    """시작일을 첫 용량 변경일 뒤로 밀 수 없다.

    막지 않으면 첫 행이 `effective_from > effective_to` 가 되어 기간이 뒤집힌다 —
    어느 날짜에도 걸리지 않는 유령 행이 남는다.
    """
    _open_two_records(db, user_id)

    with pytest.raises(service.StartDateAfterFirstChangeError):
        service.upsert(db, user_id, _req("0.5", date(2026, 9, 15)), today=TODAY)


def test_rejected_start_date_leaves_history_intact(db: Session, user_id: uuid.UUID) -> None:
    """거부된 요청이 이력을 건드리고 가면 안 된다."""
    _open_two_records(db, user_id)
    before = [(r.effective_from, r.effective_to, r.dose_mg) for r in medication_crud.list_history(db, user_id)]

    with pytest.raises(service.StartDateAfterFirstChangeError):
        service.upsert(db, user_id, _req("0.5", date(2026, 9, 15)), today=TODAY)
    db.rollback()

    after = [(r.effective_from, r.effective_to, r.dose_mg) for r in medication_crud.list_history(db, user_id)]
    assert after == before


def test_every_period_stays_ordered(db: Session, user_id: uuid.UUID) -> None:
    """모든 행이 effective_from <= effective_to 를 지킨다."""
    _open_two_records(db, user_id)
    # 첫 변경일 전날까지는 옮길 수 있다.
    service.upsert(db, user_id, _req("0.5", date(2026, 9, 14)), today=TODAY)

    rows = medication_crud.list_history(db, user_id)
    assert [(r.effective_from, r.effective_to) for r in rows if r.effective_to is not None] == [
        (date(2026, 9, 14), date(2026, 9, 14))
    ]
    for row in rows:
        assert row.effective_to is None or row.effective_from <= row.effective_to


def test_started_at_can_still_move_backward(db: Session, user_id: uuid.UUID) -> None:
    """뒤로 미는 것만 막는다 — 앞으로 당기는 건 회차 스테퍼의 정상 동작이다."""
    _open_two_records(db, user_id)

    service.upsert(db, user_id, _req("0.5", date(2026, 8, 1)), today=TODAY)

    assert medication_crud.get_dosing_start_date(db, user_id) == date(2026, 8, 1)
