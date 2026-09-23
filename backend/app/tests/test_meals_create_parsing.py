"""`api/v1/endpoints/meals.py` 의 POST /meals 파싱 로직 테스트. DB·네트워크 의존 0."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.meals import (
    _parse_eaten_at,
    _parse_meal_type,
    _parse_satiety_before_pct,
)
from app.models.enums import MealType


def test_parse_meal_type_accepts_valid_value():
    assert _parse_meal_type("LUNCH") == MealType.LUNCH


@pytest.mark.parametrize("value", [None, "", "BRUNCH", "lunch"])
def test_parse_meal_type_rejects_invalid_value(value):
    """대소문자까지 정확히 맞아야 한다 — enum 값은 대문자다."""
    with pytest.raises(HTTPException) as exc_info:
        _parse_meal_type(value)
    assert exc_info.value.status_code == 422


def test_parse_eaten_at_accepts_iso8601():
    result = _parse_eaten_at("2026-09-23T12:00:00+09:00")
    assert result == datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))


@pytest.mark.parametrize("value", [None, "", "not-a-date", "2026/09/23"])
def test_parse_eaten_at_rejects_invalid_value(value):
    with pytest.raises(HTTPException) as exc_info:
        _parse_eaten_at(value)
    assert exc_info.value.status_code == 422


def test_parse_satiety_before_pct_none_when_absent():
    """선택 입력이라 안 왔으면(None) 또는 빈 문자열(폼 필드 미입력)이면 None 이어야 한다."""
    assert _parse_satiety_before_pct(None) is None
    assert _parse_satiety_before_pct("") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", 0), ("100", 100), ("42", 42), (50, 50)],
)
def test_parse_satiety_before_pct_accepts_valid_range(value, expected):
    assert _parse_satiety_before_pct(value) == expected


@pytest.mark.parametrize("value", ["-1", "101", "abc", "3.5"])
def test_parse_satiety_before_pct_rejects_invalid_value(value):
    with pytest.raises(HTTPException) as exc_info:
        _parse_satiety_before_pct(value)
    assert exc_info.value.status_code == 422
