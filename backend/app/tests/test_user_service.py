"""services/user.py — 조립과 onboardingStatus 판정."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ApiError, ErrorCode
from app.crud import user as user_crud
from app.models.enums import MedicationStage
from app.models.medication import MedicationRecord
from app.schemas.user import (
    OnboardingStatus,
    ProfileCreateRequest,
    ProfileUpdateRequest,
)
from app.services import user as user_service


def _create_request(**overrides) -> ProfileCreateRequest:
    payload = {
        "nickname": "종호",
        "heightCm": 174.0,
        "weightKg": 79.0,
        "baselineIntake": 700,
    }
    payload.update(overrides)
    return ProfileCreateRequest.model_validate(payload)


def _add_current_medication(db, user_id) -> None:
    db.add(
        MedicationRecord(
            user_id=user_id,
            drug_name="위고비",
            dose_mg=Decimal("1.0"),
            injection_count=10,
            stage=MedicationStage.MAINTENANCE,
            effective_from=date(2026, 8, 9),
            effective_to=None,
        )
    )
    db.flush()


def test_create_profile_returns_medication_required(db):
    response = user_service.create_profile(db, request=_create_request())
    assert response.nickname == "종호"
    assert response.height_cm == 174.0
    assert response.weight_kg == 79.0
    assert response.baseline_intake == 700.0
    assert response.onboarding_status is OnboardingStatus.MEDICATION_REQUIRED
    assert response.created_at is not None


def test_create_profile_records_first_weight(db):
    response = user_service.create_profile(db, request=_create_request())
    me = user_service.get_me(db, user_id=response.user_id)
    assert me.weight_kg == 79.0


def test_get_me_is_ready_when_medication_exists(db):
    created = user_service.create_profile(db, request=_create_request())
    _add_current_medication(db, created.user_id)
    assert user_service.get_me(db, user_id=created.user_id).onboarding_status is (
        OnboardingStatus.READY
    )


def test_get_me_raises_for_unknown_user(db):
    with pytest.raises(ApiError) as exc_info:
        user_service.get_me(db, user_id=uuid.uuid4())
    assert exc_info.value.code is ErrorCode.USER_NOT_FOUND
    assert exc_info.value.http_status == 404


def test_get_me_is_profile_required_when_height_missing(db):
    """인증이 붙기 전에는 나올 수 없는 경로지만, 판정 자체는 맞아야 한다."""
    user = user_crud.create(
        db, nickname="미입력", height_cm=None, baseline_meal_kcal=Decimal("700.00")
    )
    assert user_service.get_me(db, user_id=user.id).onboarding_status is (
        OnboardingStatus.PROFILE_REQUIRED
    )


def test_update_me_changes_only_given_fields(db):
    created = user_service.create_profile(db, request=_create_request())
    updated = user_service.update_me(
        db,
        user_id=created.user_id,
        request=ProfileUpdateRequest.model_validate({"nickname": "종호2"}),
    )
    assert updated.nickname == "종호2"
    assert updated.height_cm == 174.0
    assert updated.weight_kg == 79.0


def test_update_me_weight_creates_new_state_record(db):
    """체중 수정은 users 를 고치지 않고 user_states 에 기록을 남긴다."""
    created = user_service.create_profile(db, request=_create_request())
    updated = user_service.update_me(
        db,
        user_id=created.user_id,
        request=ProfileUpdateRequest.model_validate({"weightKg": 78.4}),
    )
    assert updated.weight_kg == 78.4
    assert user_service.get_me(db, user_id=created.user_id).weight_kg == 78.4


def test_update_me_raises_for_unknown_user(db):
    with pytest.raises(ApiError) as exc_info:
        user_service.update_me(
            db,
            user_id=uuid.uuid4(),
            request=ProfileUpdateRequest.model_validate({"nickname": "없음"}),
        )
    assert exc_info.value.code is ErrorCode.USER_NOT_FOUND
