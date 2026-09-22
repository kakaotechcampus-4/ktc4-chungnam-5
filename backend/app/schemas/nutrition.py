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


class FoodCandidate(CamelModel):
    """`GET /nutrition/candidates` 의 후보 한 건.

    ⚠️ **여기의 `nutrition` 은 `servingSizeG` 기준량의 값이다** — 사용자가 먹은
    양으로 환산된 값이 아니다. 같은 `NutritionInfo` 모델을 쓰지만 의미가 다르다:
    meal item 응답의 `nutrition` 은 이미 섭취량만큼 환산돼 있다
    (`services.nutrition.resolve_by_name`).

    이 엔드포인트는 사용자가 무엇을 얼마나 먹었는지 모른다. 양이 붙는 건 후보를
    고른 뒤 `PUT /meals/{mealId}/items/{itemId}/nutrition` 에서다. FE 가 후보
    목록에 열량을 보여줄 때 "100g 당" 같은 기준을 함께 적어야 하는 이유다.
    """

    food_ref_id: str

    name: str
    """공공 DB 이름 **그대로**. 밑줄을 공백으로 바꾸지 않는다.

    실제 값은 `달걀_삶은것` 인데 명세서(`contracts/API.md`) 예시는 `달걀 (삶은 것)`
    이라 모양이 다르다. 그래도 원본을 내보내는 건, 사용자가 고른 후보와 DB 행이
    이름으로도 같아야 나중에 "무엇을 골랐는지" 를 추적할 수 있기 때문이다.
    표시용으로 다듬는 건 FE 의 몫이다 — 서버가 다듬으면 같은 음식의 이름이 화면과
    DB 에서 달라진다."""

    serving_size_g: Decimal | None
    nutrition: NutritionInfo

    @field_serializer("serving_size_g")
    def _serving_size_as_number(self, value: Decimal | None) -> float | None:
        """`NutritionInfo` 와 같은 이유로 숫자로 내보낸다(문자열 `"100.000"` 방지)."""
        return float(value) if value is not None else None


class NutritionCandidatesResponse(CamelModel):
    """후보가 없어도 빈 배열로 나간다 — 에러가 아니다.

    "공공 DB 에 없는 음식" 은 정상 흐름이고, 그때 사용자는 직접 입력으로 간다
    (`PUT .../nutrition` 의 `manual`). 404 로 응답하면 FE 가 그 갈래를 에러 화면과
    구분하지 못한다.
    """

    candidates: list[FoodCandidate]
