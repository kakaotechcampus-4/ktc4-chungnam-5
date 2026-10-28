"""`app/main.py` 라우팅 테스트. `analyze_food` 자체는 `test_food_analyzer.py`가
검증하니, 여긴 "라우터가 결과를 AnalyzeMealResponse로 올바르게 감싸는가"만 본다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import RecognizedItem

client = TestClient(app)

_REQUEST_BODY = {
    "mealId": "meal-1",
    "mealType": "LUNCH",
    "eatenAt": "2026-10-17T12:00:00+09:00",
    "stage": "MAINTENANCE",
    "imageUrl": "https://example.com/meal.jpg",
}


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_analyze_meal_wraps_items_into_response(monkeypatch):
    async def _fake_analyze_food(meal_type: str, image_url: str) -> list[RecognizedItem]:
        assert meal_type == "LUNCH"
        assert image_url == "https://example.com/meal.jpg"
        return [
            RecognizedItem(
                original_food_name="닭가슴살",
                estimated_amount=100,
                unit="g",
                confidence=0.9,
            )
        ]

    monkeypatch.setattr("app.main.analyze_food", _fake_analyze_food)

    response = client.post("/analyze-meal", json=_REQUEST_BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["mealId"] == "meal-1"
    assert body["safetyStatus"] == "SAFE"
    assert len(body["items"]) == 1
    assert body["items"][0]["originalFoodName"] == "닭가슴살"
    assert body["items"][0]["candidateFoodRefId"] is None


def test_analyze_meal_requires_image_url():
    response = client.post("/analyze-meal", json={**_REQUEST_BODY, "imageUrl": None})
    assert response.status_code == 422
