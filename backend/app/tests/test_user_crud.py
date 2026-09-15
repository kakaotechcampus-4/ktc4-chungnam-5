"""crud/user · user_state · medication 의 DB 왕복 테스트."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.crud import user_state as user_state_crud
from app.models.enums import MedicationStage
from app.models.medication import MedicationRecord


def _make_user(db):
    return user_crud.create(
        db,
        nickname="종호",
        height_cm=Decimal("174.0"),
        baseline_meal_kcal=Decimal("700.00"),
    )


def test_create_assigns_id_without_commit(db):
    user = _make_user(db)
    assert user.id is not None
    assert user.nickname == "종호"
    assert user.height_cm == Decimal("174.0")


def test_get_returns_created_user(db):
    user = _make_user(db)
    assert user_crud.get(db, user.id) is user


def test_get_returns_none_for_unknown_id(db):
    assert user_crud.get(db, uuid.uuid4()) is None


def test_update_changes_only_given_fields(db):
    user = _make_user(db)
    updated = user_crud.update(db, user, nickname="종호2")
    assert updated.nickname == "종호2"
    assert updated.height_cm == Decimal("174.0")
    assert updated.baseline_meal_kcal == Decimal("700.00")


def test_latest_weight_is_none_without_records(db):
    user = _make_user(db)
    assert user_state_crud.get_latest_weight(db, user.id) is None


def test_latest_weight_returns_most_recent_by_recorded_at(db):
    user = _make_user(db)
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=Decimal("79.0"),
        recorded_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=Decimal("78.4"),
        recorded_at=datetime(2026, 8, 21, 21, 30, tzinfo=timezone.utc),
    )
    assert user_state_crud.get_latest_weight(db, user.id) == Decimal("78.40")


def test_latest_weight_skips_records_without_weight(db):
    """체중 없이 증상만 기록한 행은 '최신 체중' 이 아니다."""
    user = _make_user(db)
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=Decimal("79.0"),
        recorded_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=None,
        recorded_at=datetime(2026, 8, 21, 21, 30, tzinfo=timezone.utc),
        appetite_level=3,
    )
    assert user_state_crud.get_latest_weight(db, user.id) == Decimal("79.00")


def test_current_medication_is_none_without_records(db):
    user = _make_user(db)
    assert medication_crud.get_current(db, user.id) is None


def test_current_medication_ignores_ended_records(db):
    user = _make_user(db)
    db.add(
        MedicationRecord(
            user_id=user.id,
            drug_name="위고비",
            dose_mg=Decimal("0.25"),
            injection_count=1,
            stage=MedicationStage.INITIAL,
            effective_from=date(2026, 6, 14),
            effective_to=date(2026, 7, 12),
        )
    )
    db.flush()
    assert medication_crud.get_current(db, user.id) is None


def test_current_medication_returns_open_record(db):
    user = _make_user(db)
    db.add(
        MedicationRecord(
            user_id=user.id,
            drug_name="위고비",
            dose_mg=Decimal("1.0"),
            injection_count=10,
            stage=MedicationStage.MAINTENANCE,
            effective_from=date(2026, 8, 9),
            effective_to=None,
        )
    )
    db.flush()
    current = medication_crud.get_current(db, user.id)
    assert current is not None
    assert current.stage == MedicationStage.MAINTENANCE
