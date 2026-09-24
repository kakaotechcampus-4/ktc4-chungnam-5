"""meal_feedbacks 접근 + 제안 음식의 성분 조회.

`meal_feedbacks` 는 `(meal_id)` UNIQUE 라 식사당 1행이다. 지금은 읽기만 한다 —
쓰는 쪽은 워커(`worker/jobs/feedback_meal.py`)이고 아직 스텁이다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Final, NamedTuple

from sqlalchemy import Numeric, and_, cast, select, update
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


def invalidate_by_meal(db: Session, meal_id: uuid.UUID) -> None:
    """이 식사의 피드백 **내용만** 비운다. 커밋은 `services/` 가 한다 (절대 규칙 5).

    음식을 고쳐 다시 확정하면 점수는 `evaluation_crud.upsert` 가 덮지만 AI 가 쓴
    문장은 아무도 안 건드린다. 닭가슴살을 더해 재확정해도 "단백질 비중이 낮았어요"
    가 그대로 나가는 이유다. 상태 가드(`services/feedback.py::get_feedback`)는
    `ANALYZING` 구간만 막아서, 재확정으로 `EVALUATED` 가 되는 순간 풀린다.

    **행은 지우지 않는다.** `daily_feedback_sources.meal_feedback_id` 가
    `ON DELETE CASCADE` 라, 지우면 일일 피드백의 출처 링크가 조용히 사라지고 문장만
    남는다. 점수(`qqs_evaluations`)를 행째 지우는 것과 다른 점이 여기다 — 그쪽에는
    이런 자식이 없다.

    내용을 비우면 `get_feedback` 이 `body is None` 을 보고 `PENDING` 을 낸다.
    워커가 새 문장을 채우면 다시 `READY` 가 된다.

    `model_version` 은 남긴다 — 어느 모델이 쓴 문장이었는지는 지운 뒤에도 쓸모가
    있고, 무효 여부는 `body` 가 말한다.

    행이 없으면 0 행 UPDATE 다. 호출부가 "피드백이 있었나" 를 따질 필요가 없다.
    """
    db.execute(
        update(MealFeedback)
        .where(MealFeedback.meal_id == meal_id)
        .values(body=None, reasoning=None, suggestions=None)
    )


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

    **100g 기준으로 환산해 낸다.** `food_refs` 의 성분은 `serving_size`
    (영양성분함량기준량) 당 값인데 그 값이 행마다 다르다 — 음료 200ml 짜리 행을 그대로
    내보내면 표시 숫자가 배수로 틀린다. 응답에 `serving_size` 를 싣지 않으므로 FE 는
    기준을 알 방법이 없고, 기준이 하나로 고정돼야 음식끼리 비교도 된다.
    `crud/evaluation.py::_scaled` 가 같은 이유로 `column * amount / serving_size` 를 쓴다.

    제안에는 "얼마나" 가 없다 — AI 가 `foodName` 과 `advice` 만 준다. 그래서 먹을
    분량으로는 환산할 수 없고, `advice` 문구("두부 반 모")가 분량을 말하고 숫자는
    100g 당 성분량을 보여 주는 구조다.

    **기준량이 쓸 수 없으면 그 음식을 통째로 뺀다**(NULL · 0 · NaN). 나눗셈의 분모라
    0 이면 터지고 NaN 이면 결과가 전부 NaN 이 된다. 빠지면 `nutrients` 가 `[]` 가
    되고 제안 문구는 그대로 나간다 — 틀린 숫자를 보여 주는 것보다 낫다.
    """
    if not food_ref_ids:
        return {}

    # 폭을 넉넉히 잡는다. 성분과 `serving_size` 가 둘 다 `Numeric(10, 3)` 이라
    # 최악이 `9999999.999 * 100 / 0.001` = 약 1e12 인데, `Numeric(12, 3)` 은
    # 999999999.999 까지라 **넘치면 500** 이 난다. `food_refs` 는 외부 DB 에서 온
    # 값이라 그런 행이 없다고 장담할 수 없고, 그 한 행 때문에 끼니 피드백 전체를
    # 못 읽게 된다 — NaN · 0 기준량을 거른 것과 같은 이유다.
    columns = [
        cast(getattr(FoodRef, field) * 100 / FoodRef.serving_size, Numeric(18, 3))
        for _, field in _SUGGESTION_NUTRIENTS
    ]
    stmt = select(FoodRef.id, *columns).where(
        and_(
            FoodRef.id.in_(set(food_ref_ids)),
            FoodRef.serving_size.isnot(None),
            FoodRef.serving_size > 0,
            # `'NaN'::numeric > 0` 은 Postgres 에서 TRUE 라 위 조건으로 안 걸린다.
            FoodRef.serving_size != Decimal("NaN"),
        )
    )

    resolved: dict[str, SuggestedNutrients] = {}
    for row in db.execute(stmt):
        amounts = {
            code: value
            for (code, _), value in zip(_SUGGESTION_NUTRIENTS, row[1:], strict=True)
            # NULL 은 "모른다" 다. 0 으로 채우면 "이 음식에는 단백질이 없다" 가 된다.
            #
            # NaN 도 같이 거른다 — `'NaN'::numeric` 은 성분 컬럼에 그냥 저장되고
            # `is not None` 을 통과한다. 그대로 두면 `float('nan')` 이 되어 JSON 에
            # `null` 로 직렬화되는데, `amount_g: float` 는 **required non-nullable**
            # 이라 응답이 자기 OpenAPI 계약을 어긴다. 생성 클라이언트가
            # `amountG: number` 로 받아 그대로 계산하면 FE 가 터진다.
            if value is not None and value.is_finite()
        }
        resolved[row.id] = SuggestedNutrients(amounts=amounts)
    return resolved
