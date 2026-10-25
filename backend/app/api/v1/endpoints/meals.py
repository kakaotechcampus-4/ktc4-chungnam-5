"""식사(meal) 관련 공개 API."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.infra.storage import FileStorage, get_file_storage
from app.models.enums import MealStatus, MealType
from app.schemas.meal import (
    MealCalendarResponse,
    MealCreateFoodItem,
    MealCreateFoodsRequest,
    MealCreateResponse,
    MealDeleteResponse,
    MealDetailResponse,
    MealListResponse,
)
from app.services import feedback as feedback_service
from app.services import meal as meal_service
from app.services import nutrition as nutrition_service

router = APIRouter()

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
"""업로드 이미지 최대 크기. `await image.read()` 가 파일 전체를 메모리에 올리므로
크기 제한이 없으면 큰 파일 하나로 서버 메모리를 고갈시킬 수 있다."""

_ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _has_known_image_signature(data: bytes) -> bool:
    """매직 바이트로 실제 이미지 파일인지 확인한다.

    `Content-Type` 헤더는 클라이언트가 마음대로 적어 보내는 값이라 그것만 믿으면
    안 된다 — 실행 파일에 `Content-Type: image/jpeg` 를 붙여 보내도 헤더 검사는
    통과한다. 파일 앞부분의 실제 바이트 시그니처를 봐야 진짜 그 형식인지 알 수
    있다. `/media` 로 그대로 서빙되는 파일이라 여기서 막지 않으면 아무 파일이나
    올려서 내려받는 통로가 된다.
    """
    if data.startswith(b"\xff\xd8\xff"):  # JPEG
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # WEBP
        return True
    return False


@dataclass
class _ParsedMealInput:
    """멀티파트(사진)·JSON(고른 음식) 두 형식을 같은 모양으로 정리한 결과.

    `image_bytes` · `items` 중 정확히 하나만 차 있다.
    """

    meal_type: MealType
    eaten_at: datetime
    image_bytes: bytes | None
    image_filename: str | None
    satiety_before_pct: int | None
    items: list[MealCreateFoodItem] | None


def _parse_meal_type(value: object) -> MealType:
    if value is None:
        raise HTTPException(status_code=422, detail="mealType 은 필수입니다.")
    try:
        return MealType(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"알 수 없는 mealType 입니다: {value!r}"
        ) from exc


def _parse_eaten_at(value: object) -> datetime:
    if value is None:
        raise HTTPException(status_code=422, detail="eatenAt 은 필수입니다.")
    try:
        eaten_at = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"eatenAt 형식이 올바르지 않습니다: {value!r}"
        ) from exc
    # 시간대 없는 값은 KST 인지 UTC 인지 추측하지 않고 거부한다 (schemas/base.py 와 같은 규칙).
    if eaten_at.tzinfo is None:
        raise HTTPException(
            status_code=422,
            detail="eatenAt 에 시간대가 필요합니다. 예: 2026-09-23T12:30:00+09:00",
        )
    return eaten_at


def _parse_satiety_before_pct(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        pct = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="satietyBeforePct 는 정수여야 합니다."
        ) from exc
    if not (0 <= pct <= 100):
        raise HTTPException(
            status_code=422, detail="satietyBeforePct 는 0~100 사이여야 합니다."
        )
    return pct


async def _parse_multipart_input(request: Request) -> _ParsedMealInput:
    """사진 입력. `image` 파일 하나 + 폼 필드들."""
    form = await request.form()
    image = form.get("image")
    if image is None or isinstance(image, str):
        raise HTTPException(status_code=422, detail="image 파일이 필요합니다.")
    if image.content_type not in _ALLOWED_IMAGE_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"지원하지 않는 이미지 형식입니다: {image.content_type!r}",
        )

    image_bytes = await image.read(_MAX_IMAGE_BYTES + 1)
    if not image_bytes:
        raise HTTPException(status_code=422, detail="빈 이미지 파일입니다.")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"이미지 파일은 {_MAX_IMAGE_BYTES // (1024 * 1024)}MB 를 넘을 수 없습니다.",
        )
    if not _has_known_image_signature(image_bytes):
        raise HTTPException(status_code=422, detail="이미지 파일 형식이 올바르지 않습니다.")

    return _ParsedMealInput(
        meal_type=_parse_meal_type(form.get("mealType")),
        eaten_at=_parse_eaten_at(form.get("eatenAt")),
        image_bytes=image_bytes,
        image_filename=image.filename,
        satiety_before_pct=_parse_satiety_before_pct(form.get("satietyBeforePct")),
        items=None,
    )


def _parse_items(value: object) -> list[MealCreateFoodItem]:
    """검색 탭에서 고른 음식 목록. 모양 검증은 `MealCreateFoodsRequest` 에 맡긴다."""
    try:
        return MealCreateFoodsRequest.model_validate({"items": value}).items
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        raise HTTPException(
            status_code=422, detail=f"{location}: {first['msg']}"
        ) from exc


async def _parse_json_input(request: Request) -> _ParsedMealInput:
    """JSON 입력 — 검색 탭에서 사용자가 고른 공공 DB 음식(`items[]`).

    예전에는 고른 목록을 `rawText`(`"계란말이 120g, 바나나 100g"`)로 이어 붙여 받아
    AI 에게 다시 나누게 했다. 이미 고른 `foodRefId` 를 버리고 이름으로 다시 추측하는
    셈이라 `items[]` 로 바꿨다. `rawText` 가 오면 조용히 무시하지 않고 거부한다 —
    무시하면 옛 FE 의 요청이 "items 가 없다" 는 엉뚱한 이유로 실패한다.
    """
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="잘못된 JSON 입니다.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="JSON 객체여야 합니다.")

    if "rawText" in body:
        raise HTTPException(
            status_code=422,
            detail="rawText 는 더 이상 받지 않습니다. 고른 음식을 items 로 보내 주세요.",
        )
    if body.get("items") is None:
        raise HTTPException(status_code=422, detail="items 는 필수입니다.")

    return _ParsedMealInput(
        meal_type=_parse_meal_type(body.get("mealType")),
        eaten_at=_parse_eaten_at(body.get("eatenAt")),
        image_bytes=None,
        image_filename=None,
        satiety_before_pct=_parse_satiety_before_pct(body.get("satietyBeforePct")),
        items=_parse_items(body["items"]),
    )


async def parse_meal_input(request: Request) -> _ParsedMealInput:
    """`Depends()` 전용. body 를 `await` 해야 해서 async 다 — DB·디스크는 건드리지 않는다.

    `Content-Type` 으로 두 형식을 나눈다 — multipart 면 사진, JSON 이면 고른 음식이다.
    FastAPI 는 `Form` 과 `Body`(JSON) 를 같은 라우트에 동시에 선언할 수 없어서,
    `Request` 를 직접 받아 수동으로 분기한다.
    """
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        return await _parse_multipart_input(request)
    if content_type.startswith("application/json"):
        return await _parse_json_input(request)
    raise HTTPException(
        status_code=422,
        detail=f"지원하지 않는 Content-Type 입니다: {content_type!r}",
    )


@router.post(
    "/meals",
    status_code=202,
    response_model=ApiResponse[MealCreateResponse],
    responses={
        201: {
            "model": ApiResponse[MealCreateResponse],
            "description": "고른 음식(items[])으로 등록 — 분석 없이 REVIEW_REQUIRED",
        },
        **error_responses(401, 404, 422),
    },
)
def create_meal(
    response: Response,
    parsed: _ParsedMealInput = Depends(parse_meal_input),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> ApiResponse[MealCreateResponse]:
    """사진 또는 고른 음식으로 식사를 등록한다.

    사진은 실제 분석을 워커가 비동기로 한다 (202).

    ## 고른 음식(`items[]`)은 분석하지 않는다 (201)

    검색 탭에서 공공 DB 음식을 직접 고른 경우다. 이미 정확한 `foodRefId` 와 g 이
    있으니 AI 에게 다시 추측시키지 않고 그대로 항목을 만들어 `REVIEW_REQUIRED` 로
    돌려준다 — 기다릴 작업이 없으므로 202 가 아니라 201 이다. 없는 `foodRefId` 가
    섞이면 404 다(`PUT .../nutrition` 과 같다).

    **일부러 `def` 다.** body 파싱(진짜 비동기 I/O)은 `parse_meal_input` 의존성이
    이미 끝내고 넘겨주고, 여기 남은 건 DB 쓰기·디스크 저장(둘 다 동기)뿐이다.
    `async def` 였다면 그 동기 작업이 끝날 때까지 이벤트 루프 전체가 막혀 다른
    요청도 같이 기다렸을 것이다 — `def` 면 FastAPI 가 스레드풀에서 돌려준다.

    사진 인식·`meal_items` 채우기(`worker/jobs/analyze_meal.py`)는 이번 범위 밖이다
    — 큐에 작업만 넣고, 실제 AI 연동 로직은 아직 스켈레톤(TODO) 상태로 남겨둔다.
    """
    if parsed.items is not None:
        try:
            names = nutrition_service.get_food_names(
                db, food_ref_ids=[item.food_ref_id for item in parsed.items]
            )
        except nutrition_service.FoodRefNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        response.status_code = 201
        return ok(
            meal_service.create_meal_from_foods(
                db,
                user_id=user_id,
                meal_type=parsed.meal_type,
                eaten_at=parsed.eaten_at,
                satiety_before_pct=parsed.satiety_before_pct,
                foods=[
                    meal_service.SelectedFood(
                        food_ref_id=item.food_ref_id,
                        display_name=names[item.food_ref_id],
                        amount=item.amount,
                        unit=item.unit,
                    )
                    for item in parsed.items
                ],
            )
        )

    # 여기 왔으면 사진이다 — `image_bytes` · `items` 중 하나만 차 있다(`_ParsedMealInput`).
    ext = Path(parsed.image_filename or "").suffix or ".jpg"
    image_key = f"meals/{uuid.uuid4()}{ext}"
    storage.save(image_key, parsed.image_bytes)

    return ok(
        meal_service.create_meal(
            db,
            user_id=user_id,
            meal_type=parsed.meal_type,
            eaten_at=parsed.eaten_at,
            image_key=image_key,
            satiety_before_pct=parsed.satiety_before_pct,
        )
    )


@router.get(
    "/meals",
    response_model=ApiResponse[MealListResponse],
    responses=error_responses(400, 401, 422),
)
def list_meals(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealListResponse]:
    try:
        return ok(
            meal_service.list_meals(db, user_id=user_id, cursor=cursor, limit=limit)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/meals/calendar",
    response_model=ApiResponse[MealCalendarResponse],
    responses=error_responses(400, 401, 422),
)
def get_meals_calendar(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealCalendarResponse]:
    try:
        return ok(meal_service.get_calendar(db, user_id=user_id, month=month))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/meals/{meal_id}",
    response_model=ApiResponse[MealDetailResponse],
    responses=error_responses(401, 404, 422),
)
def get_meal(
    meal_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealDetailResponse]:
    """식사 상세 — status 에 따라 폴링·확인·상세 조회에 모두 쓰인다.

    없는 식사·남의 식사·삭제된 식사는 전부 404 로 동일하게 응답한다.

    항목별 영양정보와 피드백 제안은 여기서 계산해서 넘긴다 — services 끼리 서로
    부르지 않는 규칙(README 절대 규칙 5) 때문에, meal(services/meal.py)과 nutrition
    (services/nutrition.py) · feedback(services/feedback.py)을 엮는 건 이 api 레이어의 일이다.
    """
    try:
        meal = meal_service.get_meal_for_detail(db, user_id=user_id, meal_id=meal_id)
    except meal_service.MealNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    nutrition_by_item = {}
    for item in meal.items:
        if item.food_ref_id is None:
            nutrition_by_item[item.id] = None
            continue
        amount_g, _, _ = meal_service.resolved_amount(item)
        nutrition_by_item[item.id] = nutrition_service.resolve_by_food_ref_id(
            db, food_ref_id=item.food_ref_id, amount_g=amount_g
        )

    # 확정 전에는 상세가 피드백을 싣지 않는다 — 제안 조회도 하지 않는다.
    suggestions = (
        feedback_service.build_meal_suggestions(db, meal.id)
        if meal.status is MealStatus.EVALUATED
        else []
    )

    return ok(
        meal_service.build_meal_detail(
            db, meal=meal, nutrition_by_item=nutrition_by_item, suggestions=suggestions
        )
    )


@router.delete(
    "/meals/{meal_id}",
    response_model=ApiResponse[MealDeleteResponse],
    responses=error_responses(401, 404, 422),
)
def delete_meal(
    meal_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealDeleteResponse]:
    """식사를 soft delete 한다.

    없는 식사 · 남의 식사 · 이미 삭제된 식사는 전부 404 로 동일하게 응답한다 —
    "남의 mealId 는 존재는 한다" 는 정보조차 흘리지 않기 위해서다(crud 의 단일 WHERE).
    """
    try:
        return ok(meal_service.delete_meal(db, user_id=user_id, meal_id=meal_id))
    except meal_service.MealNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
