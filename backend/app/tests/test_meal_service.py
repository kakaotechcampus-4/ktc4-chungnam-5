"""`services/meal.py` 단위 테스트. DB·네트워크 의존 0."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.crud import meal as meal_crud
from app.schemas.meal import MealScores
from app.services.meal import (
    MealNotFoundError,
    _build_scores,
    _build_thumbnail_url,
    decode_cursor,
    delete_meal,
    encode_cursor,
    to_grams,
)


@pytest.mark.parametrize(
    ("amount", "unit", "expected"),
    [
        (250, "g", Decimal("250.00")),
        (250.5, "g", Decimal("250.50")),
        (300, "ml", Decimal("300.00")),  # 물 기준 1ml ≈ 1g
        (0, "g", Decimal("0.00")),
    ],
)
def test_gram_equivalent_units_pass_through(amount, unit, expected):
    assert to_grams(amount, unit) == expected


@pytest.mark.parametrize("unit", ["개", "공기", "조각", "접시", "줄", "컵"])
def test_countable_units_cannot_be_converted(unit):
    """환산표가 없다. 지어내지 않고 None 을 돌려준다.

    `food_refs.serving_size` 는 성분값의 기준량(보통 100g)이지 "1개 = 50g" 이 아니다.
    지어낸 g 으로 채점하면 Q/Q/S 가 조용히 틀린다.
    """
    assert to_grams(2, unit) is None


@pytest.mark.parametrize(("amount", "unit"), [(None, "g"), (100, None), (None, None)])
def test_missing_input_is_none(amount, unit):
    assert to_grams(amount, unit) is None


def test_negative_amount_is_rejected():
    assert to_grams(-1, "g") is None


def test_whitespace_in_unit_is_tolerated():
    assert to_grams(100, " g ") == Decimal("100.00")


def test_cursor_round_trip():
    """encode 한 걸 decode 하면 원래 값이 그대로 나와야 한다."""
    eaten_at = datetime(2026, 8, 21, 12, 40, 0, tzinfo=UTC)
    meal_id = uuid.uuid4()

    cursor = encode_cursor(eaten_at, meal_id)
    decoded_eaten_at, decoded_meal_id = decode_cursor(cursor)

    assert decoded_eaten_at == eaten_at
    assert decoded_meal_id == meal_id


@pytest.mark.parametrize("garbage", ["not-a-valid-cursor", "", "abc", "12345"])
def test_decode_cursor_rejects_garbage(garbage):
    """조작되거나 형식이 다른 cursor 는 ValueError 여야 한다 (400 으로 이어짐)."""
    with pytest.raises(ValueError):
        decode_cursor(garbage)


def test_build_scores_all_none_returns_none():
    """세 점수가 전부 없으면(미평가) 객체 자체가 None 이어야 한다."""
    assert _build_scores(None, None, None) is None


def test_build_scores_rounds_decimal_to_int():
    scores = _build_scores(Decimal("74.6"), Decimal("81.2"), None)
    assert scores == MealScores(quantity=75, quality=81, satiety=None)


def test_build_thumbnail_url_none_when_no_image():
    assert _build_thumbnail_url(None) is None


def test_build_thumbnail_url_wraps_image_key():
    assert _build_thumbnail_url("abc123.jpg") == "/media/abc123.jpg"


def test_delete_meal_raises_when_not_found(monkeypatch):
    """crud 가 None 을 돌려주면(없음 / 남의 것 / 이미 삭제됨) MealNotFoundError 로 바뀌어야 한다."""
    monkeypatch.setattr(meal_crud, "soft_delete_meal", lambda db, *, user_id, meal_id: None)

    with pytest.raises(MealNotFoundError):
        delete_meal(db=None, user_id=uuid.uuid4(), meal_id=uuid.uuid4())


def test_delete_meal_builds_response_when_found(monkeypatch):
    """crud 가 Meal 을 돌려주면 그 값 그대로 MealDeleteResponse 로 조립돼야 한다."""
    meal_id = uuid.uuid4()
    deleted_at = datetime(2026, 8, 22, 10, 4, 0, tzinfo=UTC)
    fake_meal = SimpleNamespace(id=meal_id, deleted_at=deleted_at)

    monkeypatch.setattr(meal_crud, "soft_delete_meal", lambda db, *, user_id, meal_id: fake_meal)
    fake_db = MagicMock()

    response = delete_meal(db=fake_db, user_id=uuid.uuid4(), meal_id=meal_id)

    fake_db.commit.assert_called_once()

    assert response.meal_id == meal_id
    assert response.deleted_at == deleted_at
    assert response.affected_insights == []
