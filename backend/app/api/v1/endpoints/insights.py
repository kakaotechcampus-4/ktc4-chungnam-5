"""insights(장기 피드백) 공개 API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.core.time import today_kst
from app.db.session import get_db
from app.schemas.insights import InsightRefreshRequest, InsightRefreshResponse, LongTermInsightResponse
from app.services import insight as insight_service

router = APIRouter()


@router.get(
    "/insights/long-term",
    response_model=ApiResponse[LongTermInsightResponse],
    responses=error_responses(400, 401, 422),
)
def get_long_term_insight(
    period: str = Query(default="7d", pattern=r"^(7d|28d)$"),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[LongTermInsightResponse]:
    try:
        return ok(
            insight_service.get_long_term_insight(
                db, user_id=user_id, period=period, today=today_kst()
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/insights/long-term/refresh",
    status_code=202,
    response_model=ApiResponse[InsightRefreshResponse],
    responses=error_responses(401, 422),
)
def refresh_long_term_insight(
    body: InsightRefreshRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[InsightRefreshResponse]:
    return ok(
        insight_service.refresh_long_term_insight(
            db, user_id=user_id, period=body.period, today=today_kst()
        )
    )
