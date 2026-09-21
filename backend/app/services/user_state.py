"""컨디션 기록(user_states) 도메인 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 커밋은 응답을 만들어 return 하기 직전에
여기서 한다. 다른 services/ 모듈을 import 하지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core.errors import ApiError, ErrorCode
from app.crud import user as user_crud
from app.crud import user_state as user_state_crud
from app.models.user import UserState
from app.schemas.user_state import (
    WEIGHT_CHANGE_BASELINE_LAST_WEEK,
    GiSymptom,
    UserStateCreateRequest,
    UserStateResponse,
)


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _ensure_user_exists(db: Session, user_id: uuid.UUID) -> None:
    # services/user.py 의 _get_user_or_raise 와 같은 모양 — services 끼리 import 하지 않는다.
    if user_crud.get(db, user_id) is None:
        raise ApiError(ErrorCode.USER_NOT_FOUND, "사용자를 찾을 수 없습니다.", 404)


def _weight_change_kg(db: Session, state: UserState) -> float | None:
    """이번 체중 − 지난주 비교 대상 체중. 소수 첫째 자리, ROUND_HALF_UP (D3).

    float 로 빼면 0.1 단위 반올림이 흔들리므로 Decimal 로 계산한 뒤 마지막에 바꾼다.
    """
    if state.weight_kg is None:
        return None
    baseline = user_state_crud.get_last_week_weight(
        db, user_id=state.user_id, recorded_at=state.recorded_at
    )
    if baseline is None:
        return None
    change = state.weight_kg - baseline
    return float(change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def build_user_state_response(db: Session, state: UserState) -> UserStateResponse:
    """저장된 UserState 한 건을 응답으로 만든다. GET /user-states/latest 가 재사용한다."""
    return UserStateResponse(
        user_state_id=state.id,
        weight_kg=_to_float(state.weight_kg),
        weight_change_kg=_weight_change_kg(db, state),
        weight_change_baseline=WEIGHT_CHANGE_BASELINE_LAST_WEEK,
        appetite_level=state.appetite_level,
        gi_symptoms=[GiSymptom.model_validate(item) for item in state.gi_symptoms],
        note=state.note,
        recorded_at=state.recorded_at,
    )


def create_user_state(
    db: Session, *, user_id: uuid.UUID, request: UserStateCreateRequest
) -> UserStateResponse:
    """컨디션 기록 1건을 저장하고 지난주 대비 체중 변화량을 붙여 돌려준다."""
    _ensure_user_exists(db, user_id)

    state = user_state_crud.create(
        db,
        user_id=user_id,
        weight_kg=request.weight_kg,
        recorded_at=request.recorded_at or datetime.now(timezone.utc),
        appetite_level=request.appetite_level,
        gi_symptoms=[symptom.model_dump(mode="json") for symptom in request.gi_symptoms],
        note=request.note,
    )
    response = build_user_state_response(db, state)
    db.commit()
    return response
