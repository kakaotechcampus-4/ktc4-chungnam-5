"""v1 API 라우터 묶음."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    dashboard,
    evaluations,
    feedbacks,
    meal_items,
    meals,
    medications,
    nutrition,
    user_states,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(meals.router, tags=["meals"])
api_router.include_router(meal_items.router, tags=["meals"])
api_router.include_router(nutrition.router, tags=["nutrition"])
api_router.include_router(users.router, tags=["users"])
api_router.include_router(dashboard.router, tags=["dashboard"])
api_router.include_router(user_states.router, tags=["user-states"])
api_router.include_router(medications.router, tags=["medications"])
api_router.include_router(evaluations.router, tags=["evaluations"])
api_router.include_router(feedbacks.router, tags=["feedbacks"])
