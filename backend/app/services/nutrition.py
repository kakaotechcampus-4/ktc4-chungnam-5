"""영양정보 도메인 로직 — 음식명으로 공공 DB 를 찾고 섭취량만큼 환산한다.

DB 세션은 직접 다루지 않고 `crud/` 를 통해서만 접근한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.crud import food as food_crud
from app.schemas.nutrition import NutritionInfo


@dataclass(frozen=True)
class NutritionMatch:
    """공공 DB 에 붙은 결과. `nutrition` 은 환산이 가능했을 때만 채워진다."""

    food_ref_id: str
    nutrition: NutritionInfo | None


_QUANTUM = Decimal("0.01")


def _scale(value: Decimal | None, factor: Decimal) -> Decimal | None:
    """기준량 값에 배율을 곱한다. 공공 DB 에 값이 없으면 지어내지 않고 None.

    NaN·Infinity 도 None 으로 떨군다. Postgres NUMERIC 은 NaN 을 담을 수 있고,
    공공 DB 적재분에 한 건만 섞여 있어도 `NutritionInfo` 검증이 터져 500 이 된다
    (pydantic 은 비유한 Decimal 을 거부한다). 결측과 똑같이 "값이 없다" 로 본다.
    """
    if value is None or not value.is_finite():
        return None
    return (value * factor).quantize(_QUANTUM)


def _nutrition_from_food_ref(food_ref, amount_g: Decimal | None) -> NutritionInfo | None:
    """food_ref 하나를 amount_g 만큼 환산한다. 근거가 없으면 None.

    # 먹은 양을 모르거나(g 환산 불가) 기준량을 모르면 비례 계산의 근거가 없다.
    # 0 도 걸러진다 — 나누면 터진다.
    #
    # 기준량의 NaN 을 `_scale` 이 잡아 주지 못한다는 점에 주의: `_scale` 은 곱해질
    # 값만 보는데, 기준량이 NaN 이면 배율 자체가 NaN 이 되어 모든 성분이 NaN 으로
    # 물든다. `not Decimal("NaN")` 은 False 라(NaN 은 truthy) 여기서 걸러야 한다.
    """
    serving_size = food_ref.serving_size
    if (
        amount_g is None
        or serving_size is None
        or not serving_size.is_finite()
        or serving_size <= 0
    ):
        return None

    factor = amount_g / serving_size
    return NutritionInfo(
        kcal=_scale(food_ref.calories, factor),
        protein_g=_scale(food_ref.protein_g, factor),
        fat_g=_scale(food_ref.fat_g, factor),
        carb_g=_scale(food_ref.carbohydrate_g, factor),
        fiber_g=_scale(food_ref.fiber_g, factor),
        sodium_mg=_scale(food_ref.sodium_mg, factor),
    )


def resolve_by_name(
    db: Session, *, name: str, amount_g: Decimal | None
) -> NutritionMatch | None:
    """음식명으로 공공 DB 를 찾는다. **하나로 좁혀지지 않으면 None.**

    못 찾은 것과 여러 건이라 고르지 못한 것을 구분하지 않는다 — 호출부가 할 일이
    `matched: false` 로 같기 때문이다. 그 신호를 받은 FE 는 `GET /nutrition/candidates`
    로 후보를 띄우고 사용자가 고른다. 왜 임의로 하나를 집지 않는지는
    `crud.food.find_unique_by_name` 참고.
    """
    food_ref = food_crud.find_unique_by_name(db, name)
    if food_ref is None:
        return None

    nutrition = _nutrition_from_food_ref(food_ref, amount_g)
    return NutritionMatch(food_ref_id=food_ref.id, nutrition=nutrition)


def resolve_by_food_ref_id(
    db: Session, *, food_ref_id: str, amount_g: Decimal | None
) -> NutritionInfo | None:
    """이미 연결된 food_ref_id 로 바로 환산한다. 이름 재검색이 필요 없을 때 쓴다.

    `GET /meals/{mealId}` 처럼 항목이 이미 `food_ref_id` 를 갖고 있고, 현재 확정된
    양으로 영양정보만 다시 계산하면 되는 경우가 이 경로다.
    """
    food_ref = food_crud.get_by_id(db, food_ref_id)
    if food_ref is None:
        return None
    return _nutrition_from_food_ref(food_ref, amount_g)
