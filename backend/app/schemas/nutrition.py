"""영양성분 스키마.

`meal_items` 응답과 `/nutrition/candidates` 가 같은 모양을 쓴다. 명세서의 키
이름(`kcal` · `carbG`)은 `food_refs` 컬럼명(`calories` · `carbohydrate_g`)과
다르다 — 변환은 `services/nutrition.py` 가 한 곳에서만 한다.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_serializer, model_validator

from app.models.enums import MealStatus
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


# 사람이 한 끼 항목에 적어 넣을 수 있는 범위를 넉넉히 넘긴 값. 상한을 두는 건
# 오타(`150` 을 `1500000` 으로)가 Q/Q/S 채점까지 흘러가지 않게 하려는 것이다.
_MAX_NUTRIENT = Decimal("1000000")

ManualNutrient = Annotated[
    Decimal, Field(ge=0, le=_MAX_NUTRIENT, max_digits=10, decimal_places=3)
]
"""직접 입력한 성분값 하나.

`max_digits` · `decimal_places` 는 `meal_items.manual_*`(`Numeric(10, 3)`)와 **같은
모양이어야 한다.** 느슨하면 `150.123456789` 가 응답에는 그대로 실리고 DB 에는
`150.123` 으로 들어가, 같은 값을 다시 읽었을 때 달라 보인다. 조용히 자르지 않고
거절하는 건 사용자가 적은 숫자가 말없이 바뀌는 것보다 낫기 때문이다.
"""


class ManualNutrition(CamelModel):
    """사용자가 직접 적어 넣은 영양성분.

    ⚠️ **섭취량 기준 총량이다** — `FoodCandidate.nutrition` 과 달리 기준량
    (`servingSizeG`) 기준이 아니다. 사용자는 자기가 먹은 만큼의 값을 적으므로
    서버가 환산하지 않는다.

    여섯 필드가 모두 선택이다 — 포장지에 나트륨이 없는 경우처럼 사용자가 모르는
    성분이 있다. 모르는 값은 0 이 아니라 `null` 이어야 한다(`NutritionInfo` 와 같은
    규약). 다만 **전부 비어 있으면 거절한다**(`NutritionUpdateRequest`) — 그 요청은
    폴백을 해소하지 못한 채 200 을 받는다.
    """

    kcal: ManualNutrient | None = None
    protein_g: ManualNutrient | None = None
    fat_g: ManualNutrient | None = None
    carb_g: ManualNutrient | None = None
    fiber_g: ManualNutrient | None = None
    sodium_mg: ManualNutrient | None = None


class NutritionUpdateRequest(CamelModel):
    """PUT /meals/{mealId}/items/{itemId}/nutrition 요청.

    명세서(`contracts/API.md`)의 두 갈래를 그대로 받는다 — 후보 선택(`foodRefId`)
    이거나 직접 입력(`manual`) 이다.
    """

    # 컬럼이 `food_refs.id VARCHAR(64)` 다. 빈 문자열·공백만인 값도 여기서 막는다 —
    # 통과시키면 "공공 DB 에 없다"(404)로 끝나 클라이언트가 원인을 오해한다.
    food_ref_id: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
        ]
        | None
    ) = None
    manual: ManualNutrition | None = None

    @model_validator(mode="after")
    def _require_exactly_one_branch(self) -> "NutritionUpdateRequest":
        """둘 중 **정확히 하나**여야 한다.

        둘 다 오면 어느 쪽을 쓸지 서버가 정할 근거가 없다 — 하나를 골라 주면
        사용자가 보낸 값 하나가 조용히 무시된다(`MealItemsUpdateRequest` 의
        중복 itemId 를 거절하는 것과 같은 이유).

        하나도 없으면 폴백을 해소하지 못한 채 200 이 나간다. 값이 전부 비어 있는
        `manual` 도 같다 — 모양만 갖춘 빈 요청이다.
        """
        manual = self.manual

        # 구조를 먼저 본다. 둘 다 보낸 요청에 "직접 입력이 비었다" 고 답하면 더 큰
        # 잘못(갈래를 둘 다 보냄)을 가린다.
        if (self.food_ref_id is None) == (manual is None):
            raise ValueError("foodRefId 와 manual 중 하나만 보내야 합니다.")

        if manual is not None and not any(
            value is not None for value in manual.model_dump().values()
        ):
            raise ValueError("직접 입력에 값이 하나도 없습니다.")
        return self


class NutritionUpdateResponse(CamelModel):
    """PUT /meals/{mealId}/items/{itemId}/nutrition 응답.

    `status` · `isRecalculation` 은 항목이 아니라 **식사 전체**의 상태다 — 형제
    엔드포인트(`POST`/`PATCH`/`DELETE /meals/{mealId}/items`)와 같은 규약이다.
    """

    item_id: uuid.UUID
    matched: bool
    nutrition_source: Literal["PUBLIC_DB", "USER_INPUT"] | None
    nutrition: NutritionInfo | None
    status: MealStatus
    is_recalculation: bool
