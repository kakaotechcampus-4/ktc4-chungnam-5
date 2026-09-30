"""rule_engine 순수 함수. DB 를 쓰지 않는다 (절대 규칙 2).

수치 근거는 docs/be-qqs-scoring-rule.md. 여기 밴드는 spec 1.4 표의 MAINTENANCE · REDUCED 다.
"""

from decimal import Decimal as D

import pytest

from app.services.evaluation.rule_engine import (
    DailyTargets,
    MealTargets,
    QuantityBand,
    StageProfile,
    evaluate,
    intake_ratio_pct,
    meal_targets,
    score_quality,
    score_quantity,
)

MAINTENANCE_BAND = QuantityBand(lo=D("60"), hi=D("85"), tol_under=D("30"), tol_over=D("30"))
REDUCED_BAND = QuantityBand(lo=D("65"), hi=D("90"), tol_under=D("30"), tol_over=D("15"))
ON_DRUG_DAILY = DailyTargets(protein_g_per_kg=D("1.2"), fiber_g=D("25"), sodium_mg=D("2300"))


# ── Quantity ─────────────────────────────────────────────


def test_ratio_is_percent_of_usual_meal() -> None:
    assert intake_ratio_pct(D("525"), D("700")) == D("75")


@pytest.mark.parametrize("meal, baseline", [(None, D("700")), (D("500"), None), (D("500"), D("0"))])
def test_ratio_is_none_without_usable_inputs(meal, baseline) -> None:
    """kcal 이 불완전하거나 기준이 0 이면 비율이 없다 — 0 으로 나누지 않는다."""
    assert intake_ratio_pct(meal, baseline) is None


@pytest.mark.parametrize("ratio", [D("60"), D("71.4"), D("85")])
def test_inside_band_is_full_score(ratio) -> None:
    """경계 포함이다."""
    assert score_quantity(ratio, MAINTENANCE_BAND) == 100


def test_outside_band_falls_linearly_by_tolerance() -> None:
    assert score_quantity(D("100"), MAINTENANCE_BAND) == 50   # 15%p 초과 / T 30
    assert score_quantity(D("45"), MAINTENANCE_BAND) == 50    # 15%p 부족 / T 30


def test_strict_side_falls_faster() -> None:
    """같은 '평소의 100%' 라도 초과 쪽이 엄격한 감량기가 더 크게 깎인다 (spec 1.2 예시)."""
    assert score_quantity(D("100"), REDUCED_BAND) == 33       # 10%p 초과 / T 15
    assert score_quantity(D("100"), MAINTENANCE_BAND) == 50


@pytest.mark.parametrize("ratio", [D("0"), D("300")])
def test_far_outside_band_floors_at_zero(ratio) -> None:
    assert score_quantity(ratio, MAINTENANCE_BAND) == 0


def test_quantity_is_none_without_ratio() -> None:
    assert score_quantity(None, MAINTENANCE_BAND) is None


# ── Quality ──────────────────────────────────────────────


def test_meal_targets_divide_daily_by_three() -> None:
    t = meal_targets(ON_DRUG_DAILY, D("70"))
    assert t.protein_g == D("28")                 # 70 × 1.2 ÷ 3
    assert t.fiber_g == D("25") / 3
    assert t.sodium_mg == D("2300") / 3


@pytest.mark.parametrize("weight", [None, D("0")])
def test_no_protein_target_without_weight(weight) -> None:
    assert meal_targets(ON_DRUG_DAILY, weight).protein_g is None


TARGETS_70KG = meal_targets(ON_DRUG_DAILY, D("70"))   # 단백질 28 · 식이섬유 8.33 · 나트륨 766.7


def test_quality_averages_capped_subscores() -> None:
    """단백질 14/28=0.5, 식이섬유 4/8.33=0.48, 나트륨 500 ≤ 한도 → 1. 평균 0.66."""
    assert score_quality(protein_g=D("14"), fiber_g=D("4"), sodium_mg=D("500"), targets=TARGETS_70KG) == 66


def test_recommended_nutrients_cap_at_target() -> None:
    """목표를 넘겨도 1 이상 가점이 없다 (NRF 방식)."""
    assert score_quality(protein_g=D("100"), fiber_g=D("100"), sodium_mg=D("0"), targets=TARGETS_70KG) == 100


def test_sodium_over_limit_is_penalised_to_zero_at_double() -> None:
    limit = TARGETS_70KG.sodium_mg
    half = score_quality(protein_g=D("28"), fiber_g=None, sodium_mg=limit * D("1.5"), targets=TARGETS_70KG)
    zero = score_quality(protein_g=D("28"), fiber_g=None, sodium_mg=limit * 3, targets=TARGETS_70KG)
    assert half == 75    # (1 + 0.5) / 2
    assert zero == 50    # (1 + 0) / 2 — 음수로 내려가지 않는다


def test_missing_fiber_or_sodium_is_dropped_not_zeroed() -> None:
    assert score_quality(protein_g=D("14"), fiber_g=None, sodium_mg=D("500"), targets=TARGETS_70KG) == 75
    assert score_quality(protein_g=D("14"), fiber_g=None, sodium_mg=None, targets=TARGETS_70KG) == 50


def test_quality_is_none_without_protein() -> None:
    assert score_quality(protein_g=None, fiber_g=D("4"), sodium_mg=D("500"), targets=TARGETS_70KG) is None
    no_weight = MealTargets(protein_g=None, fiber_g=D("8"), sodium_mg=D("766"))
    assert score_quality(protein_g=D("14"), fiber_g=D("4"), sodium_mg=D("500"), targets=no_weight) is None


# ── evaluate ─────────────────────────────────────────────


def test_evaluate_scores_three_axes_separately() -> None:
    profile = StageProfile(quantity=MAINTENANCE_BAND, daily=ON_DRUG_DAILY)
    scores = evaluate(
        profile=profile,
        satiety_after_pct=68,
        meal_kcal=D("500"), baseline_meal_kcal=D("700"),
        protein_g=D("14"), fiber_g=D("4"), sodium_mg=D("500"),
        weight_kg=D("70"),
    )
    assert scores._asdict() == {"quantity": 100, "quality": 66, "satiety": 68}
