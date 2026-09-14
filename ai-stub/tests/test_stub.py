from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from schemas import AnalyzeMealResponse, LongFeedbackResponse, ShortFeedbackResponse

client = TestClient(app)

ANALYZE = {
    "mealId": "meal_456",
    "mealType": "LUNCH",
    "eatenAt": "2026-08-21T12:40:00+09:00",
    "stage": "MAINTENANCE",
    "rawText": "김밥 한 줄",
}
SHORT_MEAL = {
    "scope": "MEAL",
    "userId": "user_1",
    "mealId": "meal_456",
    "stage": "MAINTENANCE",
    "qqs": {"quantity": 75, "quality": 80, "satiety": 68},
}
SHORT_DAILY = {
    "scope": "DAILY",
    "userId": "user_1",
    "date": "2026-08-21",
    "stage": "MAINTENANCE",
    "qqs": {"quantity": 68, "quality": 71, "satiety": 74},
}
LONG = {
    "userId": "user_1",
    "periodType": "WEEKLY",
    "periodStart": "2026-08-15",
    "periodEnd": "2026-08-21",
    "stage": "MAINTENANCE",
}

CASES = [
    ("/analyze-meal", ANALYZE, AnalyzeMealResponse),
    ("/short-feedback", SHORT_MEAL, ShortFeedbackResponse),
    ("/short-feedback", SHORT_DAILY, ShortFeedbackResponse),
    ("/long-feedback", LONG, LongFeedbackResponse),
]


@pytest.mark.parametrize(("url", "payload", "model"), CASES)
def test_response_matches_contract(url, payload, model):
    res = client.post(url, json=payload)

    assert res.status_code == 200
    model.model_validate(res.json())  # 계약 위반이면 여기서 터진다


def test_meal_id_is_echoed():
    assert client.post("/analyze-meal", json=ANALYZE).json()["mealId"] == "meal_456"


def test_analyze_response_has_no_nutrition_fields():
    """AI 는 성분값을 만들지 않는다 — BE 가 food_refs 에서 채운다."""
    item = client.post("/analyze-meal", json=ANALYZE).json()["items"][0]

    assert set(item) == {
        "originalFoodName",
        "estimatedAmount",
        "unit",
        "confidence",
        "candidateFoodRefId",
        "clarifyQuestion",
    }


def test_daily_scope_omits_suggestions_and_reasoning():
    """daily_feedbacks 에는 대응 컬럼이 없다."""
    body = client.post("/short-feedback", json=SHORT_DAILY).json()

    assert body["suggestions"] is None
    assert body["reasoning"] is None
    assert body["body"]


def test_long_feedback_has_no_chart_data():
    """chartData 는 BE 가 qqs_evaluations 를 집계해 채운다."""
    assert "chartData" not in client.post("/long-feedback", json=LONG).json()


@pytest.mark.parametrize(("url", "payload", "_model"), CASES)
def test_blocked_scenario(url, payload, _model):
    res = client.post(url, json=payload, headers={"X-Stub-Scenario": "BLOCKED"})

    assert res.status_code == 200
    assert res.json()["safetyStatus"] == "BLOCKED"


@pytest.mark.parametrize(("url", "payload", "_model"), CASES)
def test_error_500_scenario(url, payload, _model):
    res = client.post(url, json=payload, headers={"X-Stub-Scenario": "ERROR_500"})

    assert res.status_code == 500


def test_unknown_scenario_is_rejected():
    res = client.post("/analyze-meal", json=ANALYZE, headers={"X-Stub-Scenario": "NOPE"})

    assert res.status_code == 400


def test_bad_request_is_rejected():
    """진짜 AI 가 붙는 날 한꺼번에 터지지 않게, 지금부터 계약을 강제한다."""
    assert client.post("/analyze-meal", json=dict(ANALYZE, mealType="NOPE")).status_code == 422

    no_input = {k: v for k, v in ANALYZE.items() if k != "rawText"}
    assert client.post("/analyze-meal", json=no_input).status_code == 422

    assert client.post("/short-feedback", json={**SHORT_MEAL, "mealId": None}).status_code == 422
