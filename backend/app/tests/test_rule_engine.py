"""rule_engine 순수 함수. DB 를 쓰지 않는다 (절대 규칙 2).

수치 근거는 docs/be-qqs-scoring-rule.md. 여기 밴드는 spec 「6. 단계별 기준」 표의 MAINTENANCE · REDUCED 다.
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

MAINTENANCE_BAND = QuantityBand(lo=D("60"), hi=D("85"), k_under=D("2"), k_over=D("2"))
REDUCED_BAND = QuantityBand(lo=D("65"), hi=D("90"), k_under=D("2"), k_over=D("4"))
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


def test_outside_band_falls_by_ratio_to_band_edge() -> None:
    """구간 끝과의 비율을 k 제곱한다 — 차이(%p)가 아니라 배수로 본다."""
    assert score_quantity(D("100"), MAINTENANCE_BAND) == 72   # (85/100)^2 = 0.7225
    assert score_quantity(D("45"), MAINTENANCE_BAND) == 56    # (45/60)^2 = 0.5625


def test_strict_side_falls_faster() -> None:
    """같은 '평소의 100%' 라도 초과 쪽이 엄격한 감량기가 더 크게 깎인다."""
    assert score_quantity(D("100"), REDUCED_BAND) == 66       # (90/100)^4 = 0.6561
    assert score_quantity(D("100"), MAINTENANCE_BAND) == 72


def test_far_outside_band_keeps_falling_without_hitting_zero() -> None:
    """2배와 3배가 같은 0점이 되지 않는다 — 많이 벗어날수록 계속 낮아진다."""
    double = score_quantity(D("170"), MAINTENANCE_BAND)       # (85/170)^2 = 0.25
    triple = score_quantity(D("255"), MAINTENANCE_BAND)       # (85/255)^2 = 0.111
    assert (double, triple) == (25, 11)


def test_nothing_eaten_is_zero() -> None:
    """0kcal 만 0점이다 — (0/60)^k."""
    assert score_quantity(D("0"), MAINTENANCE_BAND) == 0


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


def test_quality_is_weighted_toward_protein() -> None:
    """단백질 14/28=0.5, 식이섬유 4/8.33=0.48, 나트륨 500 ≤ 한도 → 1.
    0.5×0.5 + 0.2×0.48 + 0.3×1 = 0.646."""
    assert score_quality(protein_g=D("14"), fiber_g=D("4"), sodium_mg=D("500"), targets=TARGETS_70KG) == 65


def test_protein_weighs_most() -> None:
    """단백질만 빠진 끼니가 식이섬유만 빠진 끼니보다 낮다 (0.5 vs 0.8)."""
    no_protein = score_quality(protein_g=D("0"), fiber_g=D("100"), sodium_mg=D("0"), targets=TARGETS_70KG)
    no_fiber = score_quality(protein_g=D("100"), fiber_g=D("0"), sodium_mg=D("0"), targets=TARGETS_70KG)
    assert (no_protein, no_fiber) == (50, 80)


def test_recommended_nutrients_cap_at_target() -> None:
    """목표를 넘겨도 1 이상 가점이 없다 (NRF 방식)."""
    assert score_quality(protein_g=D("100"), fiber_g=D("100"), sodium_mg=D("0"), targets=TARGETS_70KG) == 100


def test_sodium_over_limit_falls_by_squared_ratio() -> None:
    """나트륨 부분 점수 = (한도 ÷ 먹은 양)². 2배 0.25, 3배 0.11 — 0 이 되지 않는다.
    식이섬유가 없으면 가중치를 단백질 0.5 · 나트륨 0.3 으로 다시 나눈다."""
    limit = TARGETS_70KG.sodium_mg
    def q(times: str) -> int | None:
        return score_quality(protein_g=D("28"), fiber_g=None, sodium_mg=limit * D(times), targets=TARGETS_70KG)
    assert q("1.5") == 79    # (0.5 + 0.3 × 0.444) / 0.8
    assert q("2") == 72      # (0.5 + 0.3 × 0.25) / 0.8
    assert q("3") == 67      # (0.5 + 0.3 × 0.111) / 0.8 — 2배와 3배가 구분된다


def test_missing_fiber_or_sodium_is_dropped_not_zeroed() -> None:
    """빈 영양소는 빼고 남은 가중치로 다시 나눈다 — 0 으로 채우지 않는다."""
    assert score_quality(protein_g=D("14"), fiber_g=None, sodium_mg=D("500"), targets=TARGETS_70KG) == 69  # 0.55 / 0.8
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
    assert scores._asdict() == {"quantity": 100, "quality": 65, "satiety": 68}
