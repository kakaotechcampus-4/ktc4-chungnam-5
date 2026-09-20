"""투약 공개 API.

경로에 /api/v1 을 쓰지 않는다 — api/v1/__init__.py 의 api_router 가 prefix 로 갖고 있다.

이 층이 하는 일은 넷뿐이다 — 인증 의존성으로 유저 확인, 요청 검증, 서비스 호출,
도메인 예외를 HTTP 로 번역. 단계 판정도 기간 전이도 여기 없다.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, ok
from app.db.session import get_db
from app.schemas.medication import (
    MedicationUpsertRequest,
    MedicationUpsertResponse,
)
from app.services import medication as medication_service

router = APIRouter()


@router.post(
    "/medications",
    response_model=ApiResponse[MedicationUpsertResponse],
    summary="투약 정보 등록·수정",
)
def upsert_medication(
    payload: MedicationUpsertRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MedicationUpsertResponse]:
    # 지원하지 않는 약물은 여기 오지 않는다 — DrugName ENUM 이 422 로 막는다.
    #
    # 미래 시작일도 422 다. 명세의 에러 코드 목록에 날짜 전용 코드가 없고,
    # 이건 값이 잘못된 경우라 VALIDATION_ERROR 로 흡수하는 게 맞다.
    # (핸들러가 422 → VALIDATION_ERROR 로 매핑한다)
    try:
        result = medication_service.upsert(db, user_id, payload)
    except medication_service.FutureStartDateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None

    # 응답 스키마가 GET /medications/current 와 다르다 — 이쪽은 현재 상태에 더해
    # 이번 요청으로 무엇이 바뀌었는지까지 내린다 (명세 POST /medications).
    return ok(medication_service.build_upsert_view(db, user_id, result))
