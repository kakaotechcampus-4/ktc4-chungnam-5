from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def analyze_payload() -> dict:
    return {
        "mealId": "meal_456",
        "mealType": "LUNCH",
        "eatenAt": dt.datetime(2026, 8, 21, 12, 40).isoformat(),
        "stage": "MAINTENANCE",
        "rawText": "참치김밥, 삶은 계란",
    }


@pytest.fixture
def short_meal_payload() -> dict:
    return {
        "scope": "MEAL",
        "userId": "user_1",
        "mealId": "meal_456",
        "stage": "MAINTENANCE",
        "qqs": {"quantity": 75, "quality": 80, "satiety": 68},
        "items": [
            {
                "displayName": "참치김밥",
                "amount": 250,
                "unit": "g",
                "nutrition": {"kcal": 400, "proteinG": 12, "fiberG": 4},
            }
        ],
        "satiety": {
            "beforePct": 20,
            "afterPct": 68,
            "checkins": [{"checkinOffsetHours": 3, "satietyPct": 40}],
            "hungerReturnMinutes": 60,
            "userComment": "저녁까지 안 배고팠어요",
        },
    }


@pytest.fixture
def short_daily_payload() -> dict:
    return {
        "scope": "DAILY",
        "userId": "user_1",
        "date": "2026-08-21",
        "stage": "MAINTENANCE",
        "qqs": {"quantity": 68, "quality": 71, "satiety": 74},
        "meals": [
            {
                "mealType": "BREAKFAST",
                "summary": "가볍게 먹은 아침",
                "qqs": {"quantity": 70, "quality": 65, "satiety": 72},
            }
        ],
    }


@pytest.fixture
def long_payload() -> dict:
    return {
        "userId": "user_1",
        "periodType": "WEEKLY",
        "periodStart": "2026-08-15",
        "periodEnd": "2026-08-21",
        "stage": "MAINTENANCE",
        "series": [
            {"date": "2026-08-15", "quantity": 70, "quality": 61, "satiety": 75},
            {"date": "2026-08-21", "quantity": 72, "quality": 68, "satiety": 74},
        ],
        "dailySummaries": ["무난한 하루", "저녁이 늦었던 하루"],
    }
