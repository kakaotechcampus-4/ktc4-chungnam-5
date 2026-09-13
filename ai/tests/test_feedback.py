from __future__ import annotations

import pytest

from agents.schemas import LongFeedbackResponse, ShortFeedbackResponse

SHORT = "/short-feedback"
LONG = "/long-feedback"


# ─────────────────────────── short · scope=MEAL ───────────────────────────


def test_meal_scope_returns_structured_suggestions(client, short_meal_payload):
    res = client.post(SHORT, json=short_meal_payload)

    assert res.status_code == 200
    body = ShortFeedbackResponse.model_validate(res.json())
    assert body.suggestions
    assert all(s.food_name and s.advice for s in body.suggestions)


def test_suggestions_carry_no_nutrient_numbers(client, short_meal_payload):
    """nutrients 는 BE 가 food_refs 에서 채운다 — AI 응답에 없어야 한다."""
    suggestion = client.post(SHORT, json=short_meal_payload).json()["suggestions"][0]

    assert "nutrients" not in suggestion
    assert set(suggestion) == {"foodName", "advice", "candidateFoodRefId"}


def test_reasoning_follows_weakest_axis(client, short_meal_payload):
    short_meal_payload["qqs"] = {"quantity": 30, "quality": 90, "satiety": 90}

    res = client.post(SHORT, json=short_meal_payload)

    assert "양이 많았던" in res.json()["reasoning"]


def test_meal_scope_requires_meal_id(client, short_meal_payload):
    short_meal_payload.pop("mealId")

    assert client.post(SHORT, json=short_meal_payload).status_code == 422


# ─────────────────────────── short · scope=DAILY ───────────────────────────


def test_daily_scope_omits_suggestions_and_reasoning(client, short_daily_payload):
    """daily_feedbacks 에는 대응 컬럼이 없다."""
    res = client.post(SHORT, json=short_daily_payload)

    assert res.status_code == 200
    assert res.json()["suggestions"] is None
    assert res.json()["reasoning"] is None
    assert res.json()["body"]


def test_daily_scope_requires_date(client, short_daily_payload):
    short_daily_payload.pop("date")

    assert client.post(SHORT, json=short_daily_payload).status_code == 422


# ─────────────────────────── long ───────────────────────────


def test_long_feedback_success(client, long_payload):
    res = client.post(LONG, json=long_payload)

    assert res.status_code == 200
    body = LongFeedbackResponse.model_validate(res.json())
    assert body.trend_summary and body.recommendation


def test_long_feedback_has_no_chart_data(client, long_payload):
    """chartData 는 BE 가 qqs_evaluations 를 집계해 채운다 (S6)."""
    assert "chartData" not in client.post(LONG, json=long_payload).json()


def test_long_feedback_accepts_all_period(client, long_payload):
    long_payload["periodType"] = "ALL"

    res = client.post(LONG, json=long_payload)

    assert res.status_code == 200
    assert "기록 전체" in res.json()["trendSummary"]


def test_trend_follows_series_direction(client, long_payload):
    long_payload["series"] = [
        {"date": "2026-08-15", "quantity": 70, "quality": 80, "satiety": 75},
        {"date": "2026-08-21", "quantity": 72, "quality": 60, "satiety": 74},
    ]

    assert "내려가는" in client.post(LONG, json=long_payload).json()["trendSummary"]


# ─────────────────────────── 시나리오 · 가드레일 ───────────────────────────


@pytest.mark.parametrize("url", [SHORT, LONG])
def test_error_500_scenario(client, url, short_meal_payload, long_payload):
    payload = short_meal_payload if url == SHORT else long_payload

    res = client.post(url, json=payload, headers={"X-Stub-Scenario": "ERROR_500"})

    assert res.status_code == 500


@pytest.mark.parametrize("url", [SHORT, LONG])
def test_blocked_scenario(client, url, short_meal_payload, long_payload):
    payload = short_meal_payload if url == SHORT else long_payload

    res = client.post(url, json=payload, headers={"X-Stub-Scenario": "BLOCKED"})

    assert res.status_code == 200
    assert res.json()["safetyStatus"] == "BLOCKED"
