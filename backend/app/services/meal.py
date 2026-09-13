"""식사 도메인 계산.

DB·네트워크 의존이 없는 순수 함수만 둔다. 상태는 `crud/` 가 들고,
조합은 `worker/` · `api/` 가 한다.
"""

from __future__ import annotations

from decimal import Decimal

# 그대로 g 으로 볼 수 있는 단위.
# ml 은 물 기준 1ml ≈ 1g 로 근사한다. 국·음료가 대부분이라 오차를 감수할 만하다.
_GRAM_EQUIVALENT_UNITS = {"g", "G", "그램", "ml", "mL", "ML", "밀리리터"}


def to_grams(amount: float | Decimal | None, unit: str | None) -> Decimal | None:
    """AI 가 준 `amount` + `unit` 을 g 으로 옮긴다. 옮길 수 없으면 None.

    **환산표가 없다.** `food_refs.serving_size` 는 "영양성분함량기준량"(성분값이
    어느 양 기준인지, 보통 100g)이지 "1개 = 50g" 이 아니다. 즉 "계란 2개" 를 g 으로
    바꿀 근거가 DB 에 없다.

    없는 근거를 지어내지 않는다. 지어낸 g 으로 Q/Q/S 를 채점하면 점수가 조용히
    틀리고, 사용자는 어디서 틀렸는지 알 수 없다. 대신 None 을 돌려주고
    원본 단위는 `raw_ai_result` 에 남긴다 — 사용자가 `REVIEW_REQUIRED` 단계에서
    실제 양을 확인해 `confirmed_amount_g` 를 채우는 것이 이 상태의 존재 이유다.
    """
    if amount is None or unit is None:
        return None
    if unit.strip() not in _GRAM_EQUIVALENT_UNITS:
        return None

    value = Decimal(str(amount))
    if value < 0:
        return None
    # 컬럼이 Numeric(8, 2) 다
    return value.quantize(Decimal("0.01"))
