from __future__ import annotations

from agents.schemas import AnalyzeMealResponse

URL = "/analyze-meal"


def test_success_echoes_raw_text(client, analyze_payload):
    res = client.post(URL, json=analyze_payload)

    assert res.status_code == 200
    body = AnalyzeMealResponse.model_validate(res.json())
    assert [item.original_food_name for item in body.items] == ["참치김밥", "삶은 계란"]
    assert body.safety_status.value == "SAFE"


def test_success_falls_back_to_fixtures_without_raw_text(client, analyze_payload):
    analyze_payload.pop("rawText")
    analyze_payload["imageUrl"] = "https://example.com/presigned"

    res = client.post(URL, json=analyze_payload)

    assert res.status_code == 200
    assert len(res.json()["items"]) == 3


def test_units_are_not_forced_to_grams(client, analyze_payload):
    """공개 API 의 amount + unit. g 환산은 BE 몫이라 AI 는 자연 단위로 준다."""
    res = client.post(URL, json=analyze_payload)

    units = {item["originalFoodName"]: item["unit"] for item in res.json()["items"]}
    assert units["삶은 계란"] == "개"
    assert units["참치김밥"] == "g"


def test_no_nutrition_fields_in_response(client, analyze_payload):
    """규칙 2 — AI 는 성분값을 생성하지 않는다."""
    item = client.post(URL, json=analyze_payload).json()["items"][0]

    assert set(item) == {
        "originalFoodName",
        "estimatedAmount",
        "unit",
        "confidence",
        "candidateFoodRefId",
        "clarifyQuestion",
    }


def test_deterministic(client, analyze_payload):
    first = client.post(URL, json=analyze_payload).json()
    second = client.post(URL, json=analyze_payload).json()

    assert first == second


def test_low_confidence_stays_under_fe_highlight_threshold(client, analyze_payload):
    res = client.post(URL, json=analyze_payload, headers={"X-Stub-Scenario": "LOW_CONFIDENCE"})

    items = res.json()["items"]
    assert all(item["confidence"] < 0.8 for item in items)
    assert all(item["clarifyQuestion"] for item in items)


def test_no_match_drops_food_ref_ids(client, analyze_payload):
    res = client.post(URL, json=analyze_payload, headers={"X-Stub-Scenario": "NO_MATCH"})

    assert all(item["candidateFoodRefId"] is None for item in res.json()["items"])


def test_blocked_scenario_returns_no_items(client, analyze_payload):
    res = client.post(URL, json=analyze_payload, headers={"X-Stub-Scenario": "BLOCKED"})

    assert res.status_code == 200
    assert res.json()["safetyStatus"] == "BLOCKED"
    assert res.json()["items"] == []


def test_error_500_scenario(client, analyze_payload):
    res = client.post(URL, json=analyze_payload, headers={"X-Stub-Scenario": "ERROR_500"})

    assert res.status_code == 500


def test_slow_scenario_still_succeeds(client, analyze_payload):
    res = client.post(URL, json=analyze_payload, headers={"X-Stub-Scenario": "SLOW"})

    assert res.status_code == 200


def test_unknown_scenario_is_rejected(client, analyze_payload):
    res = client.post(URL, json=analyze_payload, headers={"X-Stub-Scenario": "NOPE"})

    assert res.status_code == 400


def test_image_and_text_both_missing_is_rejected(client, analyze_payload):
    analyze_payload.pop("rawText")

    res = client.post(URL, json=analyze_payload)

    assert res.status_code == 422
