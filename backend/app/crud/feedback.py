"""meal_feedbacks 접근 + 제안 음식의 성분 조회.

`meal_feedbacks` 는 `(meal_id)` UNIQUE 라 식사당 1행이다. 지금은 읽기만 한다 —
쓰는 쪽은 워커(`worker/jobs/feedback_meal.py`)이고 아직 스텁이다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Final, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import NutrientCode
from app.models.feedback import MealFeedback
from app.models.food import FoodRef


def get_by_meal(db: Session, meal_id: uuid.UUID) -> MealFeedback | None:
    stmt = select(MealFeedback).where(MealFeedback.meal_id == meal_id)
    return db.execute(stmt).scalar_one_or_none()


class SuggestedNutrients(NamedTuple):
    """제안 음식 하나가 채워 주는 성분. 키는 명세 `NutrientCode`."""

    amounts: dict[NutrientCode, Decimal]


_SUGGESTION_NUTRIENTS: Final[tuple[tuple[NutrientCode, str], ...]] = (
    (NutrientCode.PROTEIN, "protein_g"),
    (NutrientCode.FIBER, "fiber_g"),
)
"""제안에 실을 성분.

명세 예시는 제안마다 한 줄씩만 보여 준다(두부→PROTEIN, 나물→FIBER) — **어느 성분을
고르는지는 적혀 있지 않고** AI 도 알려 주지 않는다(`ai-stub/schemas.py::Suggestion`
에는 `foodName` · `advice` · `candidateFoodRefId` 뿐이다).

그래서 "값이 있는 것을 싣는다" 로 했고, 대상은 **채우라고 권할 수 있는 성분 둘**이다.
나트륨은 제안의 근거가 되지 않는다 — "나트륨을 더 채워요" 는 이 앱이 할 말이 아니다
(절대 규칙 1, 의료 판단 금지에 닿는다).

⚠️ 명세 확인 필요: 성분 선택 규칙이 없다. 팀 안건으로 올릴 것.
"""


def nutrients_by_food_ref(
    db: Session, food_ref_ids: list[str]
) -> dict[str, SuggestedNutrients]:
    """제안 음식들의 성분을 **한 번에** 읽는다. 없는 id 는 결과에서 빠진다.

    **`IN` 한 번이다.** 제안마다 조회하면 항목 수만큼 쿼리가 나가는데, 이 응답은
    제안이 몇 개뿐이라 한 문장이면 끝난다.

    **`food_refs` 를 읽지만 이 파일에 둔다.** `crud/evaluation.py::sum_nutrients` 와
    같은 이유다 — 남의 엔티티 파일에 넣으면 동시 작업 충돌이 나고(`crud/__init__.py`
    의 "한 파일 한 엔티티" 가 막으려는 것이 그 충돌이다), 피드백 전용 조회라 다른
    도메인이 쓸 일도 없다.

    **양은 기준량(`serving_size`) 당 값 그대로다.** 제안에는 "얼마나" 가 없다 —
    AI 가 `foodName` 과 `advice` 만 주므로 환산할 분량이 없다. `advice` 문구("두부
    반 모")가 분량을 말하고 숫자는 그 음식의 성분량을 보여 주는 구조다.
    """
    if not food_ref_ids:
        return {}

    columns = [getattr(FoodRef, field) for _, field in _SUGGESTION_NUTRIENTS]
    stmt = select(FoodRef.id, *columns).where(FoodRef.id.in_(set(food_ref_ids)))

    resolved: dict[str, SuggestedNutrients] = {}
    for row in db.execute(stmt):
        amounts = {
            code: value
            for (code, _), value in zip(_SUGGESTION_NUTRIENTS, row[1:], strict=True)
            # NULL 은 "모른다" 다. 0 으로 채우면 "이 음식에는 단백질이 없다" 가 된다.
            if value is not None
        }
        resolved[row.id] = SuggestedNutrients(amounts=amounts)
    return resolved
