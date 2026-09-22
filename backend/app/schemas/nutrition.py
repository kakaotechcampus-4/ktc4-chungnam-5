"""영양성분 스키마.

`meal_items` 응답과 `/nutrition/candidates` 가 같은 모양을 쓴다. 명세서의 키
이름(`kcal` · `carbG`)은 `food_refs` 컬럼명(`calories` · `carbohydrate_g`)과
다르다 — 변환은 `services/nutrition.py` 가 한 곳에서만 한다.
"""

from decimal import Decimal

from pydantic import field_serializer

from app.schemas.base import CamelModel


class NutritionInfo(CamelModel):
    """섭취량만큼 환산된 영양성분. 공공 DB 에 값이 없는 항목은 None."""

    kcal: Decimal | None
    protein_g: Decimal | None
    fat_g: Decimal | None
    carb_g: Decimal | None
    fiber_g: Decimal | None
    sodium_mg: Decimal | None

    @field_serializer(
        "kcal", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg"
    )
    def _as_number(self, value: Decimal | None) -> float | None:
        """Decimal 을 JSON 숫자로 내보낸다.

        계산은 Decimal 로 한다 — 비례 환산을 float 로 하면 오차가 누적된다. 하지만
        pydantic 은 Decimal 을 문자열(`"100.00"`)로 직렬화하고, 명세의 nutrition 은
        숫자다. FE 가 `kcal > 500` 같은 비교를 하므로 문자열이 새어 나가면 안 된다.

        필드를 `"*"` 로 받지 않고 하나씩 적는다 — 나중에 이 모델에 문자열 필드
        (`nutritionSource` 등)가 붙으면 `float("PUBLIC_DB")` 로 터지기 때문이다.
        """
        return float(value) if value is not None else None
