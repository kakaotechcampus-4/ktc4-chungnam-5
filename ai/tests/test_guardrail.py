from __future__ import annotations

import pytest

from guardrail.medical import find_violation


@pytest.mark.parametrize(
    "text",
    [
        "용량을 늘려도 될까요",
        "다음 주에 증량 예정이에요",
        "단약해도 괜찮을까요",
        "처방받은 대로 먹었어요",
        "0.5mg 맞고 있어요",
        "약을 끊고 싶어요",
    ],
)
def test_medical_text_is_caught(text):
    assert find_violation(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "김치찌개랑 밥 한 공기 먹었어요",
        "저녁까지 안 배고팠어요",
        "체중이 조금 줄었어요",
        None,
        "",
    ],
)
def test_ordinary_meal_text_passes(text):
    assert find_violation(text) is None


def test_scans_every_argument():
    assert find_violation("평범한 아침", None, "처방 얘기") == "처방"


# ─────────────────────────── 라우터 통합 ───────────────────────────


def test_analyze_meal_blocks_medical_raw_text(client, analyze_payload):
    analyze_payload["rawText"] = "용량 늘리는 게 나을까요"

    res = client.post("/analyze-meal", json=analyze_payload)

    assert res.json()["safetyStatus"] == "BLOCKED"
    assert res.json()["items"] == []


def test_short_feedback_blocks_medical_user_comment(client, short_meal_payload):
    short_meal_payload["satiety"]["userComment"] = "단약하면 식욕이 돌아올까요"

    res = client.post("/short-feedback", json=short_meal_payload)

    assert res.json()["safetyStatus"] == "BLOCKED"
    assert res.json()["suggestions"] is None


def test_short_feedback_blocks_medical_meal_summary(client, short_daily_payload):
    short_daily_payload["meals"][0]["summary"] = "0.25mg 맞은 날"

    res = client.post("/short-feedback", json=short_daily_payload)

    assert res.json()["safetyStatus"] == "BLOCKED"


def test_long_feedback_blocks_medical_daily_summary(client, long_payload):
    long_payload["dailySummaries"] = ["무난한 하루", "증량 이야기가 나온 날"]

    res = client.post("/long-feedback", json=long_payload)

    assert res.json()["safetyStatus"] == "BLOCKED"


def test_clean_request_is_safe(client, analyze_payload):
    assert client.post("/analyze-meal", json=analyze_payload).json()["safetyStatus"] == "SAFE"
