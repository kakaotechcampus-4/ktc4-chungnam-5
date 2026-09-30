"""단계 표가 spec(docs/be-qqs-scoring-rule.md 1.4)과 같은지. 표를 바꾸면 spec 도 같이 바꾼다."""

from decimal import Decimal as D

import pytest

from app.models.enums import MedicationStage as S
from app.services.evaluation.stage_profile import STAGE_PROFILES, profile_for


def test_every_stage_has_a_profile() -> None:
    assert set(STAGE_PROFILES) == set(S)


@pytest.mark.parametrize(
    "stage, band",
    [
        (S.PRE_DOSE, ("85", "105", "30", "30")),
        (S.INITIAL, ("70", "100", "15", "30")),
        (S.TITRATION, ("60", "90", "15", "30")),
        (S.MAINTENANCE, ("60", "85", "30", "30")),
        (S.REDUCED, ("65", "90", "30", "15")),
    ],
)
def test_quantity_bands_match_spec(stage, band) -> None:
    assert tuple(profile_for(stage).quantity) == tuple(D(v) for v in band)


def test_protein_is_kdri_before_dose_and_advisory_floor_on_drug() -> None:
    assert profile_for(S.PRE_DOSE).daily.protein_g_per_kg == D("0.91")
    for stage in (S.INITIAL, S.TITRATION, S.MAINTENANCE, S.REDUCED):
        assert profile_for(stage).daily.protein_g_per_kg == D("1.2")


def test_fiber_is_lower_only_in_initial() -> None:
    assert profile_for(S.INITIAL).daily.fiber_g == D("25") * 2 / 3
    for stage in (S.PRE_DOSE, S.TITRATION, S.MAINTENANCE, S.REDUCED):
        assert profile_for(stage).daily.fiber_g == D("25")


def test_sodium_limit_is_the_same_everywhere() -> None:
    assert {p.daily.sodium_mg for p in STAGE_PROFILES.values()} == {D("2300")}


def test_maintenance_and_reduced_differ() -> None:
    """이 작업을 시작한 이유 — 유지기와 감량기가 같은 기준이면 안 된다."""
    assert profile_for(S.MAINTENANCE) != profile_for(S.REDUCED)
