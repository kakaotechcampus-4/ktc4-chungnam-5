"""식사(meal) 관련 공개 API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.meal import MealListResponse
from app.services import meal as meal_service

router = APIRouter()


@router.get("/meals", response_model=MealListResponse)
def list_meals(
    user_id: uuid.UUID,  # TODO: JWT 인증 붙으면 Depends(get_current_user_id) 로 교체
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> MealListResponse:
    try:
        return meal_service.list_meals(db, user_id=user_id, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
