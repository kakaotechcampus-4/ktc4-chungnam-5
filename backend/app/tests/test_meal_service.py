"""`services/meal.py` 단위 테스트. DB·네트워크 의존 0."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.crud import meal as meal_crud
from app.models.enums import MealStatus, SafetyStatus
from app.schemas.meal import MealScores
from app.schemas.nutrition import NutritionInfo
from app.services.meal import (
    MealNotFoundError,
    _build_feedback,
    _build_item_detail,
    _build_satiety,
    _build_scores,
    _build_thumbnail_url,
    _parse_month_range,
    _resolve_steps,
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


def test_parse_month_range_returns_kst_boundaries():
    start, end = _parse_month_range("2026-08")

    assert start == datetime(2026, 8, 1, tzinfo=ZoneInfo("Asia/Seoul"))
    assert end == datetime(2026, 9, 1, tzinfo=ZoneInfo("Asia/Seoul"))


def test_parse_month_range_rolls_over_year_at_december():
    """12월이면 다음 달 시작이 해가 넘어가야 한다."""
    _, end = _parse_month_range("2026-12")

    assert end == datetime(2027, 1, 1, tzinfo=ZoneInfo("Asia/Seoul"))


@pytest.mark.parametrize(
    "garbage_month",
    ["2026-13", "2026-00", "not-a-month", "2026", "2026-08-01"],
)
def test_parse_month_range_rejects_invalid_format(garbage_month):
    with pytest.raises(ValueError):
        _parse_month_range(garbage_month)


def _fake_item(
    *,
    confirmed_amount=None,
    confirmed_amount_g=None,
    confirmed_unit=None,
    estimated_amount=None,
    estimated_amount_g=None,
    estimated_unit=None,
    confidence=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        display_name="테스트 음식",
        confidence=confidence,
        confirmed_amount=confirmed_amount,
        confirmed_amount_g=confirmed_amount_g,
        confirmed_unit=confirmed_unit,
        estimated_amount=estimated_amount,
        estimated_amount_g=estimated_amount_g,
        estimated_unit=estimated_unit,
    )


def test_build_item_detail_unconfirmed_uses_estimated_amount():
    """확인 전(confirmed_amount 없음)이면 estimated_* 로 폴백해야 한다."""
    item = _fake_item(
        estimated_amount=Decimal("150"), estimated_unit="g", confidence=Decimal("0.62")
    )

    detail = _build_item_detail(item, nutrition=None)

    assert detail.amount == 150.0
    assert detail.unit == "g"
    assert detail.confidence == 0.62
    assert detail.matched is False
    assert detail.nutrition_source is None
    assert detail.user_confirmed is False


def test_build_item_detail_confirmed_with_nutrition():
    """확인됐고(confirmed_amount 있음) 영양정보도 있으면 matched 가 true 여야 한다."""
    item = _fake_item(confirmed_amount=Decimal("200"), confirmed_unit="g")
    nutrition = NutritionInfo(
        kcal=Decimal("274"), protein_g=None, fat_g=None, carb_g=None, fiber_g=None, sodium_mg=None
    )

    detail = _build_item_detail(item, nutrition=nutrition)

    assert detail.amount == 200.0
    assert detail.matched is True
    assert detail.nutrition_source == "PUBLIC_DB"
    assert detail.user_confirmed is True
    assert detail.nutrition is nutrition


@pytest.mark.parametrize(
    ("status", "is_recalculation", "expected"),
    [
        (
            MealStatus.ANALYZING,
            False,
            [("FOOD_RECOGNITION", "RUNNING"), ("DB_MATCHING", "PENDING"), ("STAGE_RULE_APPLY", "PENDING")],
        ),
        (
            MealStatus.ANALYZING,
            True,
            [("FOOD_RECOGNITION", "DONE"), ("DB_MATCHING", "RUNNING"), ("STAGE_RULE_APPLY", "PENDING")],
        ),
        (
            MealStatus.REVIEW_REQUIRED,
            False,
            [("FOOD_RECOGNITION", "DONE"), ("DB_MATCHING", "RUNNING"), ("STAGE_RULE_APPLY", "PENDING")],
        ),
        (
            MealStatus.EVALUATED,
            False,
            [("FOOD_RECOGNITION", "DONE"), ("DB_MATCHING", "DONE"), ("STAGE_RULE_APPLY", "DONE")],
        ),
        (MealStatus.FAILED, False, []),
    ],
)
def test_resolve_steps(status, is_recalculation, expected):
    steps = _resolve_steps(status, is_recalculation)
    assert [(step.key, step.state) for step in steps] == expected


def test_build_satiety_none_when_no_log():
    assert _build_satiety(None) is None


def test_build_satiety_maps_fields_and_checkins_always_empty():
    log = SimpleNamespace(satiety_before=30, satiety_after=75, hunger_return_minutes=90)

    detail = _build_satiety(log)

    assert detail.before_pct == 30
    assert detail.after_pct == 75
    assert detail.hunger_return_minutes == 90
    assert detail.checkins == []


def test_build_feedback_none_when_missing():
    assert _build_feedback(None) is None


def test_build_feedback_maps_fields():
    feedback_row = SimpleNamespace(
        body="요약", suggestions="제안", safety_status=SafetyStatus.SAFE
    )

    summary = _build_feedback(feedback_row)

    assert summary.summary == "요약"
    assert summary.suggestions == "제안"


def test_build_feedback_none_when_blocked():
    feedback_row = SimpleNamespace(
        body="요약", suggestions="제안", safety_status=SafetyStatus.BLOCKED
    )

    assert _build_feedback(feedback_row) is None
