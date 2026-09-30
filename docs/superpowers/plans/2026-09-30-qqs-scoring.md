# Q/Q/S 한 끼 채점 (Quantity · Quality) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /meals/{mealId}/confirm` 이 Quantity · Quality 점수와 `nutrients[].target` · `state` 를 채워 내보낸다. **API 스키마는 바꾸지 않는다.**

**Architecture:** 채점은 순수 함수 `rule_engine.py` 가 한다. 단계별 기준선은 `stage_profile.py` 의 데이터 표(`StageProfile`)에 둔다. `services/evaluation/__init__.py` 는 입력을 모은다 — 성분 합계, 평소 한 끼 열량, 먹을 때의 체중. 모은 입력을 넘기고 결과를 저장·조립한다. 세 축은 합치지 않는다.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy · PostgreSQL · pytest (+ testcontainers — API 테스트는 Docker 필요)

**Spec:** `docs/be-qqs-scoring-rule.md`

## Global Constraints

- API 응답 스키마(`app/schemas/evaluation.py`)는 **수정하지 않는다.** 값만 채운다.
- 절대 규칙 2: `rule_engine.py` 는 `decimal` · `typing` 외 임포트 0 (`__future__` 제외). DB · enum · 모델을 모른다.
- 절대 규칙 3 · D9: `total_score` · 축별 가중치 없음. 세 축은 따로 매긴다.
- 절대 규칙 4 · D7: Quantity 분모는 `users.baseline_meal_kcal`.
- 절대 규칙 5: `services/evaluation` 은 다른 `services/*` 를 import 하지 않는다. DB 는 `crud/` 로만 읽는다. 커밋은 `confirm` 한 곳.
- 하루 세 끼 가정: 끼니 목표 = 하루 기준 ÷ 3.
- 근거가 없으면 `None`. 0 으로 채우지 않는다.
- 점수 반올림은 기존 `score_satiety` 와 같은 방식이다(`Decimal.to_integral_value()`).
- 단계별 수치 (spec 1.4 표 그대로):

| | PRE_DOSE | INITIAL | TITRATION | MAINTENANCE | REDUCED |
|---|---|---|---|---|---|
| Quantity lo–hi (%) | 85–105 | 70–100 | 60–90 | 60–85 | 65–90 |
| Quantity T 부족/초과 (%p) | 30/30 | 15/30 | 15/30 | 30/30 | 30/15 |
| 단백질 g/kg/일 | 0.91 | 1.2 | 1.2 | 1.2 | 1.2 |
| 식이섬유 g/일 | 25 | 25×2/3 | 25 | 25 | 25 |
| 나트륨 mg/일 (한도) | 2300 | 2300 | 2300 | 2300 | 2300 |

- **git 은 사용자가 관리한다.** 각 태스크 끝의 "보고" 단계에서 변경 파일 목록만 알린다. `git add` · `commit` 을 실행하지 않는다.

## Review Focus

1. **합산에서 빠진 음식이 있는 식사** (`excluded > 0`: 이름 매칭 실패, "2개" 같은 환산 불가 양) → 있는 것만으로 채점하고 `NUTRITION_NOT_MATCHED` 경고가 **같이** 나가야 한다. 경고 없이 점수만 나가면 안 된다. (Task 3 테스트)
2. **먹은 시각보다 뒤에만 체중 기록이 있는 사용자** (가입 직후 과거 시각으로 식사 입력) → 가장 최근 체중으로 대신해 Quality 를 매긴다. (Task 3 테스트)
3. **특정 성분 컬럼만 NULL 인 음식** (공공 DB 식이섬유 결측) → 그 항목만 빼고 평균한다. 0 으로 채우지 않는다. 그 줄의 `current` · `state` 는 null 이다. (Task 3 테스트)
4. **구간 경계와 극단값** (r 이 정확히 lo · hi, 0 kcal, 평소의 300%) → 경계는 100점, 극단은 0점 아래로 내려가지 않는다. (Task 1 테스트)
5. **confirm 응답과 GET evaluation 재조회가 같아야 한다** — 목표치(`target`)를 조회 때 다시 계산해도 값이 같아야 한다. (Task 3 테스트)

---

## File Structure

| 파일 | 변경 | 책임 |
| --- | --- | --- |
| `backend/app/services/evaluation/rule_engine.py` | 수정 | 순수 채점 함수 + 입력 타입(`QuantityBand` · `DailyTargets` · `StageProfile` · `MealTargets`) |
| `backend/app/services/evaluation/stage_profile.py` | 수정 | 단계 → `StageProfile` 수치 표 (`profile_for`). 강조축(`STAGE_EMPHASIS`)은 그대로 |
| `backend/app/services/evaluation/__init__.py` | 수정 | 입력 수집(기준 열량 · 체중 · 성분 합계), `evaluate` 호출, `nutrients[].target/state` 조립, 버전 v2 |
| `backend/app/tests/test_rule_engine.py` | 생성 | 순수 함수 단위 테스트 (DB 불필요) |
| `backend/app/tests/test_stage_profile.py` | 생성 | 단계 표가 spec 수치와 같은지 |
| `backend/app/tests/factories.py` | 수정 | `make_weight` 추가 |
| `backend/app/tests/test_evaluations_api.py` | 수정 | null 전제였던 테스트 갱신 + 새 API 테스트 |

**범위 밖 (이번에 안 함):** `GET /meals/{mealId}` 의 `nutrients[].target` (`services/meal.py::_build_nutrients`) 은 계속 null 이다. 절대 규칙 5 때문에 `services/meal` 이 `services/evaluation` 을 부를 수 없어서, 목표치 계산을 공용 위치로 옮기는 별도 작업이 필요하다. `ranges` · `quantityRatio` 도 넣지 않는다 (spec 7장).

---

### Task 1: rule_engine — Quantity · Quality 순수 채점 함수

**Files:**
- Modify: `backend/app/services/evaluation/rule_engine.py` (전체 교체)
- Create: `backend/app/tests/test_rule_engine.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `QuantityBand(lo: Decimal, hi: Decimal, tol_under: Decimal, tol_over: Decimal)`
  - `DailyTargets(protein_g_per_kg: Decimal, fiber_g: Decimal, sodium_mg: Decimal)`
  - `StageProfile(quantity: QuantityBand, daily: DailyTargets)`
  - `MealTargets(protein_g: Decimal | None, fiber_g: Decimal, sodium_mg: Decimal)`
  - `MEALS_PER_DAY: Final = 3`
  - `intake_ratio_pct(meal_kcal: Decimal | None, baseline_meal_kcal: Decimal | None) -> Decimal | None`
  - `score_quantity(ratio_pct: Decimal | None, band: QuantityBand) -> int | None`
  - `meal_targets(daily: DailyTargets, weight_kg: Decimal | None) -> MealTargets`
  - `score_quality(*, protein_g: Decimal | None, fiber_g: Decimal | None, sodium_mg: Decimal | None, targets: MealTargets) -> int | None`
  - `score_satiety(satiety_after_pct: int | None) -> int | None` (기존 그대로)
  - `evaluate(*, profile: StageProfile, satiety_after_pct: int | None, meal_kcal: Decimal | None, baseline_meal_kcal: Decimal | None, protein_g: Decimal | None, fiber_g: Decimal | None, sodium_mg: Decimal | None, weight_kg: Decimal | None) -> Scores`
  - `Scores(quantity: int | None, quality: int | None, satiety: int | None)` (기존 그대로)

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/app/tests/test_rule_engine.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest app/tests/test_rule_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'DailyTargets'`

- [ ] **Step 3: 구현**

`backend/app/services/evaluation/rule_engine.py` 전체를 다음으로 교체:

```python
"""Q/Q/S 채점기 — **순수 함수다** (절대 규칙 2).

DB 도 네트워크도 모른다. AI 가 죽어도 점수는 남는다는 게 이 층의 존재 이유다.
규칙과 수치의 근거는 `docs/be-qqs-scoring-rule.md` 에 있다.

**세 축을 합치지 않는다** (절대 규칙 3 · D9). 단계별 차이는 곱셈 가중치가 아니라
`StageProfile` 에 담긴 기준선의 엄격함으로 낸다. 수치 표는 `stage_profile.py` 가 들고,
여기는 그 표를 인자로 받기만 한다 — 그래야 enum 도 모르는 순수 함수로 남는다.

**근거가 없으면 None 이다.** 0 을 쓰면 "못 쟀다" 와 "바닥이다" 가 같은 값이 되고,
`qqs_evaluations.*_score` 가 NULL 허용인 것도 그래서다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, NamedTuple

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")

MEALS_PER_DAY: Final = 3
"""하루 기준량을 끼니 목표로 나누는 수. 하루 세 끼 가정이다 (spec 5.2)."""


class Scores(NamedTuple):
    """명세 응답의 `scores`. 0~100 정수이고, 근거가 없으면 None.

    **합치지 않는다** (절대 규칙 3 · D9). `total_score` 도 가중치도 없다.
    """

    quantity: int | None
    quality: int | None
    satiety: int | None


class QuantityBand(NamedTuple):
    """평소 대비 섭취 비율(%)의 적정 구간과 허용폭(%p).

    허용폭은 구간을 벗어난 뒤 몇 %p 를 더 벗어나야 0점이 되는지다. **작을수록 엄격하다.**
    부족 쪽과 초과 쪽을 나눈 건 단계마다 위험한 방향이 달라서다 (spec 2장).
    """

    lo: Decimal
    hi: Decimal
    tol_under: Decimal
    tol_over: Decimal


class DailyTargets(NamedTuple):
    """하루 기준. 끼니 목표는 `meal_targets` 가 ÷ `MEALS_PER_DAY` 로 만든다."""

    protein_g_per_kg: Decimal
    fiber_g: Decimal
    sodium_mg: Decimal
    """제한 영양소라 목표가 아니라 **한도**다."""


class StageProfile(NamedTuple):
    """한 투약 단계의 채점 기준선 전부."""

    quantity: QuantityBand
    daily: DailyTargets


class MealTargets(NamedTuple):
    """한 끼 목표. 응답 `nutrients[].target` 도 이 값이다."""

    protein_g: Decimal | None
    """체중을 모르면 None — 목표를 지어내지 않는다."""
    fiber_g: Decimal
    sodium_mg: Decimal


def _as_score(fraction: Decimal) -> int:
    """0~1 비율을 0~100 정수로. 범위 밖은 자른다. 반올림은 `score_satiety` 와 같다."""
    clamped = max(_ZERO, min(_ONE, fraction))
    return int((clamped * _HUNDRED).to_integral_value())


def score_satiety(satiety_after_pct: int | None) -> int | None:
    """사용자가 말한 포만감을 그대로 쓴다.

    명세 예시가 그렇다 — 요청 `satietyAfterPct: 68` 이 응답 `scores.satiety: 68`
    로 나간다. 주관 지표라 서버가 보정할 근거가 없다.
    """
    if satiety_after_pct is None:
        return None
    clamped = max(_ZERO, min(_HUNDRED, Decimal(satiety_after_pct)))
    return int(clamped.to_integral_value())


def intake_ratio_pct(
    meal_kcal: Decimal | None, baseline_meal_kcal: Decimal | None
) -> Decimal | None:
    """평소 한 끼 대비 섭취 비율(%). 절대 규칙 4(D7) — 분모는 개인 baseline 이다."""
    if meal_kcal is None or baseline_meal_kcal is None or baseline_meal_kcal <= 0:
        return None
    return meal_kcal / baseline_meal_kcal * _HUNDRED


def score_quantity(ratio_pct: Decimal | None, band: QuantityBand) -> int | None:
    """구간 안이면 100, 벗어나면 그 쪽 허용폭에 비례해 선형으로 깎는다."""
    if ratio_pct is None:
        return None
    if ratio_pct < band.lo:
        return _as_score(_ONE - (band.lo - ratio_pct) / band.tol_under)
    if ratio_pct > band.hi:
        return _as_score(_ONE - (ratio_pct - band.hi) / band.tol_over)
    return 100


def meal_targets(daily: DailyTargets, weight_kg: Decimal | None) -> MealTargets:
    """하루 기준 ÷ 3. 단백질만 체중에 따라 달라진다."""
    protein = (
        None
        if weight_kg is None or weight_kg <= 0
        else weight_kg * daily.protein_g_per_kg / MEALS_PER_DAY
    )
    return MealTargets(
        protein_g=protein,
        fiber_g=daily.fiber_g / MEALS_PER_DAY,
        sodium_mg=daily.sodium_mg / MEALS_PER_DAY,
    )


def score_quality(
    *,
    protein_g: Decimal | None,
    fiber_g: Decimal | None,
    sodium_mg: Decimal | None,
    targets: MealTargets,
) -> int | None:
    """NRF 방식: 권장 영양소는 목표 대비 충족도(상한 1), 제한 영양소는 초과만큼 감점.

    **단백질은 필수다.** 없으면 None — 투약 중 근손실 방지의 핵심이라 빼고 매기면
    점수가 의미를 잃는다. 식이섬유 · 나트륨은 값이 없으면 **빼고** 평균한다.
    0 으로 채우면 식이섬유는 부당하게 깎이고 나트륨은 짠 음식이 만점을 받는다.

    영양소별 가중치는 없다 — 단순 평균이다 (NRF9.3 도 가중치 없이 합산한다).
    """
    if protein_g is None or targets.protein_g is None:
        return None
    subs = [min(_ONE, protein_g / targets.protein_g)]
    if fiber_g is not None:
        subs.append(min(_ONE, fiber_g / targets.fiber_g))
    if sodium_mg is not None:
        over = max(_ZERO, sodium_mg - targets.sodium_mg)
        # 한도의 2배에서 0 이다 (spec 5.3, 제품 결정).
        subs.append(max(_ZERO, _ONE - over / targets.sodium_mg))
    return _as_score(sum(subs, _ZERO) / len(subs))


def evaluate(
    *,
    profile: StageProfile,
    satiety_after_pct: int | None,
    meal_kcal: Decimal | None,
    baseline_meal_kcal: Decimal | None,
    protein_g: Decimal | None,
    fiber_g: Decimal | None,
    sodium_mg: Decimal | None,
    weight_kg: Decimal | None,
) -> Scores:
    """세 축을 **따로** 매긴다. 불완전한 입력은 호출부가 None 으로 넘긴다."""
    return Scores(
        quantity=score_quantity(
            intake_ratio_pct(meal_kcal, baseline_meal_kcal), profile.quantity
        ),
        quality=score_quality(
            protein_g=protein_g,
            fiber_g=fiber_g,
            sodium_mg=sodium_mg,
            targets=meal_targets(profile.daily, weight_kg),
        ),
        satiety=score_satiety(satiety_after_pct),
    )
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest app/tests/test_rule_engine.py -v`
Expected: 전부 PASS

주의: 이 시점에 `services/evaluation/__init__.py` 의 `evaluate(satiety_after_pct=...)` 호출은 **깨진다** (인자가 늘었다). Task 3 에서 고친다. Task 1 만 따로 리뷰할 때는 `test_rule_engine.py` 만 돌린다.

- [ ] **Step 5: 보고** — 변경 파일 `rule_engine.py`, `test_rule_engine.py`. git 은 사용자가 처리한다.

---

### Task 2: stage_profile — 단계별 기준선 표

**Files:**
- Modify: `backend/app/services/evaluation/stage_profile.py`
- Create: `backend/app/tests/test_stage_profile.py`

**Interfaces:**
- Consumes: Task 1 의 `QuantityBand`, `DailyTargets`, `StageProfile`
- Produces: `profile_for(stage: MedicationStage) -> StageProfile`, `STAGE_PROFILES: Final[dict[MedicationStage, StageProfile]]`. `emphasis_for` 는 그대로.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/app/tests/test_stage_profile.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest app/tests/test_stage_profile.py -v`
Expected: FAIL — `ImportError: cannot import name 'STAGE_PROFILES'`

- [ ] **Step 3: 구현**

`stage_profile.py` 의 모듈 독스트링을 교체한다:

```python
"""단계별 채점 기준선(`STAGE_PROFILES`)과 강조축(`STAGE_EMPHASIS`).

**둘은 역할이 다르다.**

- `STAGE_PROFILES` — **점수를 바꾸는** 값. 단계마다 어느 방향이 위험한지(spec 2장 위험 표)를
  보고 그 쪽 기준을 조였다. 수치와 근거는 `docs/be-qqs-scoring-rule.md` 1.4 표다.
- `STAGE_EMPHASIS` — **화면에서 어느 게이지를 앞세울지**만 정한다. 채점에 쓰지 않는다 (spec 1.3).

**절대 규칙 3(D9):** `total_score` 도 축별 가중치도 없다. 단계별 차이는 곱셈이 아니라
기준선의 엄격함으로 낸다. 이 표를 바꾸면 `services/evaluation` 의 `STAGE_RULE_VERSION` 을 올린다.
"""
```

import 에 추가:

```python
from decimal import Decimal

from app.services.evaluation.rule_engine import DailyTargets, QuantityBand, StageProfile
```

`STAGE_EMPHASIS` 위에 추가:

```python
_SODIUM_MG_PER_DAY: Final = Decimal("2300")
"""KDRI 2025 만성질환위험감소섭취량. 단계 무관."""
_FIBER_G_PER_DAY: Final = Decimal("25")
"""KDRI 2025 충분섭취량의 근거값(Reynolds 2019). 성별·나이 컬럼이 없어 공통값을 쓴다."""
_PROTEIN_ON_DRUG: Final = Decimal("1.2")
"""공동 권고(Mozaffarian 2025) 감량 중 1.2–1.6 g/kg/일의 하한. 단계별 기준은 권고에 없다."""


def _band(lo: str, hi: str, under: str, over: str) -> QuantityBand:
    return QuantityBand(Decimal(lo), Decimal(hi), Decimal(under), Decimal(over))


STAGE_PROFILES: Final[dict[MedicationStage, StageProfile]] = {
    # 위험 없음 → 전부 기본값. 단백질은 KDRI 2025 권장섭취량 기준.
    MedicationStage.PRE_DOSE: StageProfile(
        _band("85", "105", "30", "30"),
        DailyTargets(Decimal("0.91"), _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY),
    ),
    # 부족 쪽 엄격 — 부작용으로 너무 적게 먹는다. 시작 며칠 고섬유 회피 → 식이섬유 2/3.
    MedicationStage.INITIAL: StageProfile(
        _band("70", "100", "15", "30"),
        DailyTargets(_PROTEIN_ON_DRUG, _FIBER_G_PER_DAY * 2 / 3, _SODIUM_MG_PER_DAY),
    ),
    # 부족 쪽 엄격 — 올릴 때마다 섭취가 줄어든다 (1.0mg 에서 −24%).
    MedicationStage.TITRATION: StageProfile(
        _band("60", "90", "15", "30"),
        DailyTargets(_PROTEIN_ON_DRUG, _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY),
    ),
    # 공동 권고의 섭취 감소 16–39% → 평소의 61–84%. Quantity 는 위험 방향 없음.
    MedicationStage.MAINTENANCE: StageProfile(
        _band("60", "85", "30", "30"),
        DailyTargets(_PROTEIN_ON_DRUG, _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY),
    ),
    # 초과 쪽 엄격 — 용량을 내리면 식욕이 돌아온다 (간접 근거, spec 2.1).
    MedicationStage.REDUCED: StageProfile(
        _band("65", "90", "30", "15"),
        DailyTargets(_PROTEIN_ON_DRUG, _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY),
    ),
}
"""단계 → 채점 기준선. ENUM 전체를 덮는다."""


def profile_for(stage: MedicationStage) -> StageProfile:
    """그 단계의 채점 기준선."""
    return STAGE_PROFILES[stage]
```

(`STAGE_EMPHASIS` · `emphasis_for` 는 손대지 않는다.)

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest app/tests/test_stage_profile.py app/tests/test_rule_engine.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 보고** — 변경 파일 `stage_profile.py`, `test_stage_profile.py`.

---

### Task 3: services/evaluation — 입력 수집 · 저장 · target/state 조립

**Files:**
- Modify: `backend/app/services/evaluation/__init__.py`
- Modify: `backend/app/tests/factories.py` (`make_weight` 추가)
- Modify: `backend/app/tests/test_evaluations_api.py`

**Interfaces:**
- Consumes: Task 1 `evaluate`, `meal_targets`, `MealTargets`. Task 2 `profile_for`. 기존 `crud.user.get(db, user_id) -> User | None`, `crud.user_state.get_latest_weight_before(db, *, user_id, before) -> Decimal | None`, `crud.user_state.get_latest_weight(db, user_id) -> Decimal | None`, `crud.evaluation.sum_nutrients(db, meal_id) -> NutrientTotals`
- Produces: 응답 필드 값만 바뀐다. 스키마는 그대로다.
  - `scores.quantity` · `scores.quality`: 계산값 또는 null
  - `nutrients[].target`: 끼니 목표. 소수 1자리 float
  - `nutrients[].state`: `SHORT` · `OK` · `OVER` 또는 null
  - `evidence.stageRuleVersion` · `weightProfileVersion`: `"v2"`

- [ ] **Step 1: 팩토리 추가**

`backend/app/tests/factories.py` — import 에 `from datetime import UTC, datetime, timedelta`, `from app.crud import user_state as user_state_crud`, `from app.models.user import User, UserState` 를 반영하고, 파일 끝에 추가:

```python
def make_weight(
    db: Session,
    *,
    user_id: uuid.UUID,
    weight_kg: Decimal = Decimal("70.00"),
    recorded_at: datetime = EATEN_AT - timedelta(days=1),
) -> UserState:
    """체중 기록 1건. 기본값은 **식사 하루 전** — 먹을 때의 체중으로 읽히는 시각이다."""
    return user_state_crud.create(
        db, user_id=user_id, weight_kg=weight_kg, recorded_at=recorded_at
    )
```

- [ ] **Step 2: 기존 테스트 갱신 + 새 API 테스트 작성 (실패)**

`test_evaluations_api.py`:

(a) import 에 `make_weight` 추가.

(b) `test_nutrients_match_the_spec_shape` 를 교체한다. 기본 음식은 미역국 250g 이다(100g 당 단백질 3 · 식이섬유 0.5 · 나트륨 600 → 7.5g · 1.25g · 1500mg).

```python
def test_nutrients_match_the_spec_shape(client: TestClient, db: Session) -> None:
    """명세의 세 줄. 목표는 끼니 기준(하루 ÷ 3)이다. 체중이 없으면 단백질 목표만 null."""
    user, meal = _ready_meal(db)

    rows = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]["nutrients"]

    assert [r["code"] for r in rows] == ["PROTEIN", "FIBER", "SODIUM"]
    assert [r["unit"] for r in rows] == ["g", "g", "mg"]
    for row in rows:
        assert set(row) == {"code", "label", "current", "target", "unit", "state"}
        # 숫자로 나가야 한다 — Decimal 이 문자열로 새면 FE 비교가 깨진다
        assert isinstance(row["current"], (int, float))
    protein, fiber, sodium = rows
    assert protein["target"] is None and protein["state"] is None   # 체중 없음
    assert (fiber["target"], fiber["state"]) == (8.3, "SHORT")      # 25 ÷ 3
    assert (sodium["target"], sodium["state"]) == (766.7, "OVER")   # 2300 ÷ 3
```

(c) `test_unscored_axes_are_null_not_zero` 를 교체한다:

```python
def test_quality_is_null_without_weight(client: TestClient, db: Session) -> None:
    """단백질 목표가 체중에서 나온다. 체중을 모르면 0 이 아니라 null 이다."""
    user, meal = _ready_meal(db)

    scores = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]["scores"]

    assert scores["quality"] is None
    assert scores["satiety"] == 68
    # 125kcal / 평소 700kcal = 17.9% — 유지기 하한 60 에서 30%p 넘게 부족하다
    assert scores["quantity"] == 0
```

(d) 파일 끝(에러 섹션 앞)에 새 섹션 추가:

```python
# ── 채점 (docs/be-qqs-scoring-rule.md) ──────────────────────────


def _meal_with(
    db: Session,
    *,
    stage=MedicationStage.MAINTENANCE,
    weight=True,
    food_ref_id="KFD_TEST_01",
    **food,
):
    """음식 1건(250g)짜리 식사. `food` 는 100g 당 성분이다."""
    user = make_user(db)
    if weight:
        make_weight(db, user_id=user.id)                       # 70kg
    meal = make_meal(db, user_id=user.id, stage=stage, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(db, food_ref_id=food_ref_id, **food)
    make_meal_item(db, meal_id=meal.id, food_ref_id=food_ref_id)
    return user, meal


def _confirm(client: TestClient, user, meal) -> dict:
    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_same_meal_scores_differently_by_stage(client: TestClient, db: Session) -> None:
    """700kcal = 평소의 100%. 유지기는 15%p 초과 / T30 → 50, 감량기는 10%p 초과 / T15 → 33."""
    user, meal = _meal_with(db, calories=Decimal("280"))
    assert _confirm(client, user, meal)["scores"]["quantity"] == 50

    user, meal = _meal_with(
        db, stage=MedicationStage.REDUCED, food_ref_id="KFD_TEST_02", calories=Decimal("280")
    )
    assert _confirm(client, user, meal)["scores"]["quantity"] == 33


# 100g 당 단백질 5.6 · 식이섬유 1.6 · 나트륨 200 → 250g 에 14g · 4g · 500mg.
# 70kg 유지기 목표 28g · 8.33g · 766.7mg → 0.5 · 0.48 · 1 → 평균 0.66.
_BALANCED = dict(protein_g=Decimal("5.6"), fiber_g=Decimal("1.6"), sodium_mg=Decimal("200"))


def test_quality_is_scored_and_targets_filled(client: TestClient, db: Session) -> None:
    user, meal = _meal_with(db, **_BALANCED)

    data = _confirm(client, user, meal)

    assert data["scores"]["quality"] == 66
    rows = {r["code"]: r for r in data["nutrients"]}
    assert (rows["PROTEIN"]["target"], rows["PROTEIN"]["state"]) == (28.0, "SHORT")
    assert (rows["FIBER"]["target"], rows["FIBER"]["state"]) == (8.3, "SHORT")
    assert (rows["SODIUM"]["target"], rows["SODIUM"]["state"]) == (766.7, "OK")
    assert data["evidence"]["stageRuleVersion"] == "v2"


def test_evaluation_view_equals_confirm_with_targets(client: TestClient, db: Session) -> None:
    """목표치는 조회 때 다시 계산한다. 그래도 confirm 과 같아야 한다 (Review Focus 5)."""
    user, meal = _meal_with(db, **_BALANCED)
    confirmed = _confirm(client, user, meal)

    fetched = client.get(
        f"/api/v1/meals/{meal.id}/evaluation", headers=_h(user.id)
    ).json()["data"]

    assert {k: v for k, v in confirmed.items() if k != "feedbackStatus"} == fetched


def test_missing_fiber_is_dropped_not_zeroed(client: TestClient, db: Session) -> None:
    """공공 DB 에 식이섬유가 비어 있으면 그 항목만 뺀다 — (0.5 + 1) / 2 = 75 (Review Focus 3)."""
    user, meal = _meal_with(db, **{**_BALANCED, "fiber_g": None})

    data = _confirm(client, user, meal)

    assert data["scores"]["quality"] == 75
    fiber = next(r for r in data["nutrients"] if r["code"] == "FIBER")
    assert fiber["current"] is None and fiber["state"] is None


def test_excluded_item_is_scored_on_the_rest_with_warning(
    client: TestClient, db: Session
) -> None:
    """성분을 못 구한 음식은 빼고 매기되, 경고가 **같이** 나가야 한다 (Review Focus 1).

    결정 A (spec 4.2): 점수가 늘 나오는 대신 빠진 만큼 낮게 나올 수 있다. 경고가
    사용자에게 그 사실을 알리는 유일한 장치라 여기서 함께 못 박는다.
    """
    user, meal = _meal_with(db, calories=Decimal("280"), **_BALANCED)
    make_meal_item(db, meal_id=meal.id, display_name="이름모를음식")   # food_ref_id=None

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["error"]["code"] == "NUTRITION_NOT_MATCHED"
    # 매칭된 음식만: 700kcal(유지기 100% → 50), 품질은 _BALANCED 그대로 66
    assert body["data"]["scores"] == {"quantity": 50, "quality": 66, "satiety": 68}


def test_weight_recorded_after_meal_falls_back_to_latest(
    client: TestClient, db: Session
) -> None:
    """먹은 시각 이전 체중이 없으면 가장 최근 체중을 쓴다 (Review Focus 2)."""
    user, meal = _meal_with(db, weight=False, **_BALANCED)
    make_weight(db, user_id=user.id, recorded_at=meal.eaten_at + timedelta(days=1))

    assert _confirm(client, user, meal)["scores"]["quality"] == 66
```

- [ ] **Step 3: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest app/tests/test_evaluations_api.py -v`
Expected: FAIL — `TypeError: evaluate() got an unexpected keyword argument` 또는 `quality is None`. (Docker 가 켜져 있어야 한다 — testcontainers)

- [ ] **Step 4: 구현**

`backend/app/services/evaluation/__init__.py`:

(a) import 교체·추가:

```python
from app.crud import evaluation as evaluation_crud
from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.crud import satiety as satiety_crud
from app.crud import user as user_crud
from app.crud import user_state as user_state_crud
from app.crud.evaluation import NutrientTotals
from app.models.enums import (
    FeedbackStatus,
    MealStatus,
    MedicationStage,
    NutrientCode,
    NutrientState,
    NutritionSource,
)
...
from app.services.evaluation.rule_engine import MealTargets, evaluate, meal_targets
from app.services.evaluation.stage_profile import emphasis_for, profile_for
```

(b) 버전 상수:

```python
STAGE_RULE_VERSION: Final = "v2"
WEIGHT_PROFILE_VERSION: Final = "v2"
"""명세 `evidence` 의 상수들. 채점 기준선(`stage_profile`)을 바꾸면 올린다.

v2: Quantity · Quality 채점 도입 (`docs/be-qqs-scoring-rule.md`).
"""
```

(c) `_NUTRIENT_ROWS` 아래에 추가:

```python
_LIMIT_KEYS: Final = frozenset({"sodium_mg"})
"""목표가 아니라 **한도**인 성분. 넘으면 `OVER`, 모자란 건 문제가 아니다."""


def _weight_at(db: Session, meal: Meal) -> Decimal | None:
    """먹을 때의 체중. 그 이전 기록이 없으면 가장 최근 체중으로 대신한다.

    투약 단계를 "먹을 때의 단계" 로 읽는 것과 같은 원칙이다(`_stage_of`). 대신하는
    경우는 가입 직후 과거 시각으로 식사를 적었을 때다 — 그때 체중이 없다고 Quality 를
    비우면, 온보딩에서 받은 체중이 있는데도 점수가 안 나온다.
    """
    weight = user_state_crud.get_latest_weight_before(
        db, user_id=meal.user_id, before=meal.eaten_at
    )
    if weight is None:
        weight = user_state_crud.get_latest_weight(db, meal.user_id)
    return weight


def _targets(
    stage: MedicationStage, weight_kg: Decimal | None
) -> MealTargets:
    """그 단계 · 그때 체중의 끼니 목표. confirm 과 조회가 **같은 함수**로 구한다."""
    return meal_targets(profile_for(stage).daily, weight_kg)


def _usable(totals: NutrientTotals, key: str) -> Decimal | None:
    """채점에 쓰는 성분 합계. **화면의 `current` 와 같은 기준이다.**

    합산된 음식 중 그 성분 컬럼이 비어 있던 게 있으면(`missing`) None — 화면도 그
    합계를 안 내보낸다.

    **빠진 음식(`excluded > 0`)이 있어도 있는 것만으로 쓴다** (spec 4.2, 2026-09-30 결정).
    그 경우 응답에 `NUTRITION_NOT_MATCHED` 경고가 이미 붙는다
    (`api/v1/endpoints/evaluations.py::_respond`). 감수하는 것: 빠진 만큼 낮게 나온다.
    """
    if totals.missing.get(key):
        return None
    return getattr(totals, key)


def _state(
    current: Decimal | None, target: Decimal | None, *, is_limit: bool
) -> NutrientState | None:
    if current is None or target is None:
        return None
    if is_limit:
        return NutrientState.OVER if current > target else NutrientState.OK
    return NutrientState.SHORT if current < target else NutrientState.OK
```

(d) `_build_view` 에 `targets: MealTargets` 키워드 인자를 추가하고 `nutrients=` 부분을 교체:

```python
def _build_view(
    meal: Meal,
    *,
    stage: MedicationStage,
    totals: NutrientTotals,
    targets: MealTargets,
    scores: QqsScores,
    model: type[MealEvaluationResponse] = MealEvaluationResponse,
    **extra: object,
) -> MealEvaluationResponse:
```

```python
        nutrients=[
            NutrientRow(
                code=code,
                label=label,
                # **결측이 있으면 합계를 안 내보낸다.** (기존 주석 유지)
                current=(
                    None
                    if totals.missing.get(key)
                    else _as_float(getattr(totals, key))
                ),
                target=_as_target(getattr(targets, key)),
                unit=unit,
                # 상태는 **채점과 같은 입력**으로 판정한다 — 부분합으로 "부족" 을 말하지 않는다.
                state=_state(
                    _usable(totals, key), getattr(targets, key), is_limit=key in _LIMIT_KEYS
                ),
            )
            for code, key, label, unit in _NUTRIENT_ROWS
        ],
```

그리고 `_as_float` 아래에:

```python
def _as_target(value: Decimal | None) -> float | None:
    """목표치는 소수 1자리로 내보낸다. 8.333… g 을 그대로 주면 화면이 자리수를 다룬다."""
    return None if value is None else round(float(value), 1)
```

(e) `confirm` 의 채점 부분 교체 (기존 `# 기준선이 미정이라 …` 주석과 `scores = evaluate(...)` 두 줄):

```python
    stage = _stage_of(db, meal)
    totals = evaluation_crud.sum_nutrients(db, meal.id)
    user = user_crud.get(db, user_id)
    if user is None:  # _owned_meal 을 통과했으니 도달 불가. assert 는 -O 에서 지워진다
        raise RuntimeError(f"user {user_id} 가 없습니다.")
    weight = _weight_at(db, meal)
    targets = _targets(stage, weight)
    scores = evaluate(
        profile=profile_for(stage),
        satiety_after_pct=request.satiety_after_pct,
        meal_kcal=_usable(totals, "kcal"),
        baseline_meal_kcal=user.baseline_meal_kcal,
        protein_g=_usable(totals, "protein_g"),
        fiber_g=_usable(totals, "fiber_g"),
        sodium_mg=_usable(totals, "sodium_mg"),
        weight_kg=weight,
    )
```

같은 함수의 `_build_view(` 호출에 `targets=targets,` 를 추가한다.

(f) `get_view` 의 `_build_view(` 호출에 추가:

```python
            targets=_targets(row.stage_at_evaluation, _weight_at(db, meal)),
```

(g) `crud/evaluation.py` 의 `NutrientTotals.kcal` 독스트링에서 "지금은 읽는 곳이 없다 … 같이 지운다" 문단을 다음으로 바꾼다:

```python
    """Quantity 채점의 분자다 (평소 한 끼 대비 비율, 절대 규칙 4)."""
```

- [ ] **Step 5: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest app/tests/test_evaluations_api.py app/tests/test_rule_engine.py app/tests/test_stage_profile.py -v`
Expected: 전부 PASS

- [ ] **Step 6: 전체 회귀**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 전부 PASS. 실패하면 null 점수를 전제한 다른 테스트다. 특히 `test_meal_service.py`, `test_meals_api.py`, `test_dashboard_service.py` 를 확인한다. 그런 테스트를 발견하면 **고치기 전에 목록을 사용자에게 보고한다** — 다른 팀원 파일일 수 있다.

- [ ] **Step 7: 보고** — 변경 파일 `services/evaluation/__init__.py`, `crud/evaluation.py`(독스트링), `tests/factories.py`, `tests/test_evaluations_api.py`. 알려진 범위 밖: `GET /meals/{mealId}` 의 `nutrients[].target` 은 여전히 null 이다 (File Structure 의 "범위 밖").
