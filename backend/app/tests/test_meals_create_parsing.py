"""`api/v1/endpoints/meals.py` 의 POST /meals 파싱 로직 테스트. DB·네트워크 의존 0."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.meals import (
    _has_known_image_signature,
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


def test_parse_eaten_at_rejects_timezone_naive_value():
    """시간대 없는 값은 KST/UTC 를 추측하지 않고 거부한다 (schemas/base.py 와 같은 규칙, PR #42 리뷰)."""
    with pytest.raises(HTTPException) as exc_info:
        _parse_eaten_at("2026-09-23T12:00:00")
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


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xd8\xff\xe0\x00\x10JFIF",  # JPEG
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",  # PNG
        b"RIFF\x00\x00\x00\x00WEBPVP8 ",  # WEBP
    ],
)
def test_has_known_image_signature_accepts_real_images(data):
    assert _has_known_image_signature(data) is True


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image",
        b"MZ\x90\x00\x03\x00\x00\x00",  # Windows 실행 파일(PE) 시그니처
        b"RIFF\x00\x00\x00\x00AVI ",  # RIFF 이긴 하지만 WEBP 가 아님
    ],
)
def test_has_known_image_signature_rejects_non_images(data):
    """Content-Type 헤더는 위조 가능하므로, 실제 바이트 시그니처가 없는 파일은 거부한다 (PR #42 리뷰)."""
    assert _has_known_image_signature(data) is False
