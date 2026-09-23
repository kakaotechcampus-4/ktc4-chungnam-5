"""식사(meal) 관련 공개 API."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.infra.storage import FileStorage, get_file_storage
from app.models.enums import MealType
from app.schemas.meal import (
    MealCalendarResponse,
    MealCreateResponse,
    MealDeleteResponse,
    MealListResponse,
)
from app.services import meal as meal_service

router = APIRouter()

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
"""업로드 이미지 최대 크기. `await image.read()` 가 파일 전체를 메모리에 올리므로
크기 제한이 없으면 큰 파일 하나로 서버 메모리를 고갈시킬 수 있다."""


@dataclass
class _ParsedMealInput:
    """멀티파트(사진)·JSON(텍스트) 두 형식을 같은 모양으로 정리한 결과."""

    meal_type: MealType
    eaten_at: datetime
    raw_text: str | None
    image_bytes: bytes | None
    image_filename: str | None
    satiety_before_pct: int | None


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
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"eatenAt 형식이 올바르지 않습니다: {value!r}"
        ) from exc


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

    image_bytes = await image.read(_MAX_IMAGE_BYTES + 1)
    if not image_bytes:
        raise HTTPException(status_code=422, detail="빈 이미지 파일입니다.")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"이미지 파일은 {_MAX_IMAGE_BYTES // (1024 * 1024)}MB 를 넘을 수 없습니다.",
        )

    return _ParsedMealInput(
        meal_type=_parse_meal_type(form.get("mealType")),
        eaten_at=_parse_eaten_at(form.get("eatenAt")),
        raw_text=None,
        image_bytes=image_bytes,
        image_filename=image.filename,
        satiety_before_pct=_parse_satiety_before_pct(form.get("satietyBeforePct")),
    )


async def _parse_json_input(request: Request) -> _ParsedMealInput:
    """텍스트 입력. AI 가 인식할 게 없으니 `rawText` 가 필수다."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="잘못된 JSON 입니다.") from exc

    raw_text = body.get("rawText")
    if not raw_text or not str(raw_text).strip():
        raise HTTPException(status_code=422, detail="rawText 는 필수입니다.")

    return _ParsedMealInput(
        meal_type=_parse_meal_type(body.get("mealType")),
        eaten_at=_parse_eaten_at(body.get("eatenAt")),
        raw_text=str(raw_text).strip(),
        image_bytes=None,
        image_filename=None,
        satiety_before_pct=_parse_satiety_before_pct(body.get("satietyBeforePct")),
    )


async def parse_meal_input(request: Request) -> _ParsedMealInput:
    """`Depends()` 전용. body 를 `await` 해야 해서 async 다 — DB·디스크는 건드리지 않는다.

    `Content-Type` 으로 두 형식을 나눈다 — multipart 면 사진, JSON 이면 텍스트다.
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
    responses=error_responses(401, 422),
)
def create_meal(
    parsed: _ParsedMealInput = Depends(parse_meal_input),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> ApiResponse[MealCreateResponse]:
    """사진 또는 텍스트로 식사를 등록한다. 실제 분석은 워커가 비동기로 한다 (202).

    **일부러 `def` 다.** body 파싱(진짜 비동기 I/O)은 `parse_meal_input` 의존성이
    이미 끝내고 넘겨주고, 여기 남은 건 DB 쓰기·디스크 저장(둘 다 동기)뿐이다.
    `async def` 였다면 그 동기 작업이 끝날 때까지 이벤트 루프 전체가 막혀 다른
    요청도 같이 기다렸을 것이다 — `def` 면 FastAPI 가 스레드풀에서 돌려준다.

    사진 인식·`meal_items` 채우기(`worker/jobs/analyze_meal.py`)는 이번 범위 밖이다
    — 큐에 작업만 넣고, 실제 AI 연동 로직은 아직 스켈레톤(TODO) 상태로 남겨둔다.
    """
    image_key: str | None = None
    if parsed.image_bytes is not None:
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
            raw_text=parsed.raw_text,
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
