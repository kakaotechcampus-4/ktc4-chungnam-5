"""식사(meal) 관련 공개 API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, ok
from app.db.session import get_db
from app.schemas.meal import MealDeleteResponse, MealListResponse
from app.services import meal as meal_service

router = APIRouter()


@router.get("/meals", response_model=ApiResponse[MealListResponse])
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


@router.delete("/meals/{meal_id}", response_model=ApiResponse[MealDeleteResponse])
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
