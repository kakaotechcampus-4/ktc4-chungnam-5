"""`services/meal.py` 단위 테스트. DB·네트워크 의존 0."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.meal import to_grams


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
