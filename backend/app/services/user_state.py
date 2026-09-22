"""컨디션 기록(user_states) 도메인 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 커밋은 응답을 만들어 return 하기 직전에
여기서 한다. 다른 services/ 모듈을 import 하지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core.errors import ApiError, ErrorCode
from app.core.time import KST
from app.crud import user as user_crud
from app.crud import user_state as user_state_crud
from app.models.user import UserState
from app.schemas.user_state import GiSymptom, UserStateCreateRequest, UserStateResponse


def _ensure_user_exists(db: Session, user_id: uuid.UUID) -> None:
    # services/user.py 의 _get_user_or_raise 와 같은 모양 — services 끼리 import 하지 않는다.
    if user_crud.get(db, user_id) is None:
        raise ApiError(ErrorCode.USER_NOT_FOUND, "사용자를 찾을 수 없습니다.", 404)


def _last_week_cutoff(recorded_at: datetime) -> datetime:
    """"지난주" 비교 대상의 상한(이 시각 미만). (recorded_at 의 KST 날짜 − 6일) 00:00 KST.

    "KST 날짜가 (기준일 − 7일) 이하" 와 같은 뜻이다 (D1·D2). 날짜 단위로 자르므로 경계 날에도
    그날 마지막 기록이 대표값이 된다 — GET /dashboard 의 하루 대표값 규칙과 같다.
    기준은 서버 now 가 아니라 recorded_at 이다.
    """
    kst_date = recorded_at.astimezone(KST).date()
    return datetime.combine(kst_date - timedelta(days=6), time.min, tzinfo=KST)


def _weight_change_kg(db: Session, state: UserState) -> Decimal | None:
    """이번 체중 − 지난주 비교 대상 체중. 소수 첫째 자리, ROUND_HALF_UP (D3).

    float 로 빼면 0.1 단위 반올림이 흔들리므로 Decimal 로 계산한다.
    """
    if state.weight_kg is None:
        return None
    baseline = user_state_crud.get_latest_weight_before(
        db, user_id=state.user_id, before=_last_week_cutoff(state.recorded_at)
    )
    if baseline is None:
        return None
    change = state.weight_kg - baseline
    return change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def build_user_state_response(db: Session, state: UserState) -> UserStateResponse:
    """저장된 UserState 한 건을 응답으로 만든다. GET /user-states/latest 가 재사용한다."""
    return UserStateResponse(
        user_state_id=state.id,
        weight_kg=state.weight_kg,
        weight_change_kg=_weight_change_kg(db, state),
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
    # flush 만 된 객체는 요청값(예: 78.456)을 그대로 들고 있다. Numeric(5,2) 로 반올림된
    # DB 저장값을 다시 읽어야 응답·변화량이 GET /user-states/latest 와 어긋나지 않는다.
    db.refresh(state)
    response = build_user_state_response(db, state)
    db.commit()
    return response


def get_latest_user_state(
    db: Session, *, user_id: uuid.UUID
) -> UserStateResponse | None:
    """가장 최근 컨디션 기록 1건. 기록이 없으면 None (읽기 전용, 커밋하지 않는다)."""
    raise NotImplementedError("get_latest_user_state 미구현")
