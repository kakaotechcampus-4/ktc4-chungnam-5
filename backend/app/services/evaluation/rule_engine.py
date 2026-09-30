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
"""하루 기준량을 끼니 목표로 나누는 수. 하루 세 끼 가정이다."""


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
    부족 쪽과 초과 쪽을 나눈 건 단계마다 위험한 방향이 달라서다.
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
        # 한도의 2배에서 0 이다 (제품 결정).
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
