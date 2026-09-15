"""v1 API 라우터 묶음."""

from fastapi import APIRouter

from app.api.v1.endpoints import meals

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(meals.router, tags=["meals"])
