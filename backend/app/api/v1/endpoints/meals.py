"""식사(meal) 관련 공개 API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.meal import (
    MealCalendarResponse,
    MealDeleteResponse,
    MealDetailResponse,
    MealListResponse,
)
from app.services import meal as meal_service
from app.services import nutrition as nutrition_service

router = APIRouter()


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

    항목별 영양정보는 여기서 계산해서 넘긴다 — services 끼리 서로 부르지 않는
    규칙(README 절대 규칙 5) 때문에, meal(services/meal.py)과 nutrition
    (services/nutrition.py)을 엮는 건 이 api 레이어의 일이다.
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

    return ok(meal_service.build_meal_detail(db, meal=meal, nutrition_by_item=nutrition_by_item))


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
