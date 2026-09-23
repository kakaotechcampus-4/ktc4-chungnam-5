"""대시보드(dashboard) 관련 공개 API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.dashboard import DashboardResponse
from app.services import dashboard as dashboard_service

router = APIRouter()


@router.get(
    "/dashboard",
    response_model=ApiResponse[DashboardResponse],
    responses=error_responses(400, 401, 422),
)
def get_dashboard(
    period: str = Query(default="7d", pattern=r"^(7d|28d|all)$"),
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[DashboardResponse]:
    try:
        return ok(dashboard_service.get_dashboard(db, user_id=user_id, period=period))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
