"""투약 공개 API.

경로에 /api/v1 을 쓰지 않는다 — api/v1/__init__.py 의 api_router 가 prefix 로 갖고 있다.

이 층이 하는 일은 넷뿐이다 — 인증 의존성으로 유저 확인, 요청 검증, 서비스 호출,
도메인 예외를 HTTP 로 번역. 단계 판정도 기간 전이도 여기 없다.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.medication import (
    CurrentMedicationResponse,
    MedicationUpsertRequest,
    MedicationUpsertResponse,
)
from app.services import medication as medication_service

router = APIRouter()


@router.post(
    "/medications",
    response_model=ApiResponse[MedicationUpsertResponse],
    summary="투약 정보 등록·수정",
    # 422 를 빠뜨리면 FastAPI 가 자동 생성한 응답이
    # `#/components/schemas/HTTPValidationError` 를 가리키는데, `main.py` 의
    # custom_openapi 가 그 컴포넌트를 지운다 — 모든 라우트가 422 를 ErrorResponse 로
    # 명시한다는 전제여서다. 명시하지 않으면 openapi.json 에 깨진 $ref 가 남아
    # Swagger UI 와 코드 생성기가 죽는다.
    responses=error_responses(401, 404, 409, 422),
)
def upsert_medication(
    payload: MedicationUpsertRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MedicationUpsertResponse]:
    # 지원하지 않는 약물은 여기 오지 않는다 — DrugName ENUM 이 422 로 막는다.
    #
    # 시작일 오류도 422 다. 명세의 에러 코드 목록에 날짜 전용 코드가 없고,
    # 이건 값이 잘못된 경우라 VALIDATION_ERROR 로 흡수하는 게 맞다.
    # (핸들러가 422 → VALIDATION_ERROR 로 매핑한다)
    #
    # 미래 시작일과 "첫 용량 변경 이후로 미는 시작일" 둘 다 InvalidStartDateError 다.
    try:
        result = medication_service.upsert(db, user_id, payload)
    except medication_service.InvalidStartDateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None

    # 응답 스키마가 GET /medications/current 와 다르다 — 이쪽은 현재 상태에 더해
    # 이번 요청으로 무엇이 바뀌었는지까지 내린다 (명세 POST /medications).
    return ok(medication_service.build_upsert_view(db, user_id, result))


@router.get(
    "/medications/current",
    response_model=ApiResponse[CurrentMedicationResponse],
    summary="현재 투약 · 단계 · 회차 · D-day",
    responses=error_responses(401, 404, 409, 422),
)
def get_current_medication(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[CurrentMedicationResponse]:
    """지금 무슨 약을 얼마로 맞고 있는지, 몇 회차이고 다음이 언제인지.

    응답은 `POST /medications` 와 같은 14필드다 (명세: "동일 구조"). 그중
    `doseChanged` · `doseEvent` · `stageChanged` 는 조회에서 늘 고정값이다 —
    쓰기 결과를 담는 자리라 대응물이 없다.

    **단계는 매번 다시 판정한다.** 용량을 안 바꿔도 날짜가 지나면 단계가 옮겨가는데,
    그 순간에는 아무 요청도 없어서 저장값만 읽으면 옛 단계가 나간다.

    투약 미등록은 409 `STAGE_NOT_SET` 이다 (명세). 없는 사용자는 404 다.
    """
    return ok(medication_service.get_current_view(db, user_id))


