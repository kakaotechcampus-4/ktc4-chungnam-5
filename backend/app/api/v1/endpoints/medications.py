"""투약 공개 API.

경로에 /api/v1 을 쓰지 않는다 — api/v1/__init__.py 의 api_router 가 prefix 로 갖고 있다.

이 층이 하는 일은 넷뿐이다 — 인증 의존성으로 유저 확인, 요청 검증, 서비스 호출,
도메인 예외를 HTTP 로 번역. 단계 판정도 기간 전이도 여기 없다.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.errors import ApiError, ErrorCode
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.medication import (
    MedicationCorrectRequest,
    MedicationCorrectResponse,
    CurrentMedicationResponse,
    DoseEventsResponse,
    MedicationRegisterRequest,
    MedicationRegisterResponse,
)
from app.services import medication as medication_service

router = APIRouter()


@router.post(
    "/medications",
    response_model=ApiResponse[MedicationRegisterResponse],
    summary="투약 정보 등록",
    # 422 를 빠뜨리면 FastAPI 가 자동 생성한 응답이
    # `#/components/schemas/HTTPValidationError` 를 가리키는데, `main.py` 의
    # custom_openapi 가 그 컴포넌트를 지운다 — 모든 라우트가 422 를 ErrorResponse 로
    # 명시한다는 전제여서다. 명시하지 않으면 openapi.json 에 깨진 $ref 가 남아
    # Swagger UI 와 코드 생성기가 죽는다.
    responses=error_responses(401, 404, 409, 422),
)
def register_medication(
    payload: MedicationRegisterRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MedicationRegisterResponse]:
    # 지원하지 않는 약물은 여기 오지 않는다 — DrugName ENUM 이 422 로 막는다.
    #
    # 시작일 오류도 422 다. 명세의 에러 코드 목록에 날짜 전용 코드가 없고,
    # 이건 값이 잘못된 경우라 VALIDATION_ERROR 로 흡수하는 게 맞다.
    # (핸들러가 422 → VALIDATION_ERROR 로 매핑한다)
    #
    # **등록 전용이다.** 잘못 넣은 값을 고치는 건 `PATCH /medications/{id}` 다.
    # 같은 날 다시 등록하거나 전체 시작일을 옮기려 하면 409 로 그쪽을 가리킨다 —
    # 요청만 봐서는 "정말 용량을 바꿨다" 와 "잘못 쳐서 고친다" 가 구분되지 않는다.
    try:
        result = medication_service.register(db, user_id, payload)
    except medication_service.InvalidStartDateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None

    # 응답 스키마가 GET /medications/current 와 다르다 — 이쪽은 현재 상태에 더해
    # 이번 요청으로 무엇이 바뀌었는지까지 내린다 (명세 POST /medications).
    return ok(medication_service.build_register_view(db, user_id, result))


@router.patch(
    "/medications/{record_id}",
    response_model=ApiResponse[MedicationCorrectResponse],
    summary="투약 정보 정정",
    # 422 를 빠뜨리면 openapi.json 에 깨진 $ref 가 남는다 — 위 POST 주석 참고.
    responses=error_responses(401, 404, 409, 422),
)
def correct_medication(
    record_id: uuid.UUID,
    payload: MedicationCorrectRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MedicationCorrectResponse]:
    """**정정 전용.** 잘못 넣은 값을 고친다. 이력을 만들지 않는다.

    `POST` 가 행을 더한다면(INSERT) 여기는 있는 행을 고친다(UPDATE). 한 번도 맞은 적
    없는 용량이 `dose-events` 에 남으면 안 되기 때문이다.

    과거 행도 고칠 수 있다. 이미 나온 끼니 평가·피드백은 안 바뀐다 — 식사는 만들 때
    복사해 둔 스냅샷을 보고, 장기 피드백은 일일 피드백에서 나온다. 바뀌는 건
    대시보드와 `dose-events` 처럼 **기록을 그대로 보여주는 화면**뿐이다.

    **응답이 `POST` 와 다르다.** 그쪽은 "지금 투약이 어떤 상태인가" 를 말하지만
    여기는 "그 기록이 어떻게 고쳐졌나" 라서, 고친 행 자체를 돌려준다.
    """
    try:
        return ok(medication_service.correct(db, user_id, record_id, payload))
    except medication_service.MedicationNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None


@router.get(
    "/medications/current",
    response_model=ApiResponse[CurrentMedicationResponse],
    summary="현재 투약 · 단계 · 회차 · D-day",
    # 422 를 뺄 수 없다. body · query · path 가 없어서 "낼 일이 없는 코드" 로 보이지만,
    # FastAPI 는 `X-User-Id` 헤더를 파라미터로 세어 422 응답을 **자동 생성**한다.
    # 그 응답이 `HTTPValidationError` 를 가리키는데 `main.py` 의 custom_openapi 가
    # 그 컴포넌트를 지우므로, 명시하지 않으면 openapi.json 에 깨진 $ref 가 남는다
    # (`test_no_route_references_the_removed_validation_schema` 가 잡는다).
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


@router.get(
    "/medications/dose-events",
    response_model=ApiResponse[DoseEventsResponse],
    summary="용량 변경 이력",
    # 422 는 GET /medications/current 와 같은 이유로 남긴다 — FastAPI 가
    # `X-User-Id` 헤더를 파라미터로 세어 자동 생성한다.
    responses=error_responses(401, 404, 422),
)
def list_dose_events(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[DoseEventsResponse]:
    """용량을 바꿔 온 이력 전체. 오래된 순이다.

    **기록이 없어도 200 이다** — `events: []`. "아직 투약 전"은 에러가 아니고,
    빈 목록이 그 사실을 그대로 말한다. 없는 사용자만 404 다.

    페이지네이션이 없다 — 명세에 커서가 없고, 한 사용자의 용량 변경은 주 단위라
    수십 줄을 넘지 않는다. 필요해지면 `DoseEventsResponse` 에 커서를 더한다.
    """
    return ok(medication_service.list_dose_events(db, user_id))
