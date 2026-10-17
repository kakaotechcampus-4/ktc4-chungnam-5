"""meal_feedbacks 접근 + 제안 음식의 성분 조회.

`meal_feedbacks` 는 `(meal_id)` UNIQUE 라 식사당 1행이다. 내용을 **채우는** 쪽은
워커(`worker/jobs/feedback_meal.py`)이고 `upsert` 로 쓴다. API 쪽은 읽기와 무효화만 한다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Final, NamedTuple

from sqlalchemy import Numeric, and_, cast, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.enums import NutrientCode, NutrientUnit, SafetyStatus, TaskStatus
from app.models.feedback import MealFeedback
from app.models.food import FoodRef
from app.models.task import Task

FEEDBACK_TASK_TYPE: Final = "feedback.meal"
"""끼니 피드백 작업의 큐 타입. 넣는 쪽(`services/evaluation::confirm`)과 찾는 쪽
(`latest_task_status`)이 이 값 하나를 쓴다 — 따로 적으면 한쪽만 바뀌었을 때 작업을 못 찾아
FAILED · GENERATING 이 조용히 PENDING 이 된다. 워커 등록(`worker/dispatch.py`)도 같은 문자열이다.

crud 에 두는 건 services → crud 방향만 import 가 허용되기 때문이다
(`crud/daily_feedback.py::REFRESH_TASK_TYPE` 과 같은 자리)."""


def get_by_meal(db: Session, meal_id: uuid.UUID) -> MealFeedback | None:
    stmt = select(MealFeedback).where(MealFeedback.meal_id == meal_id)
    return db.execute(stmt).scalar_one_or_none()


def latest_task_status(db: Session, meal_id: uuid.UUID) -> TaskStatus | None:
    """이 끼니의 가장 최근 `feedback.meal` 작업 상태. 작업이 없으면 None.

    가장 최근 것만 본다 — 재확정은 실패한 작업 위에 새 작업을 넣고, 그때부터는
    새 작업의 결과를 기다리는 중이다.

    "가장 최근" 은 `created_at`(트랜잭션 시작 시각) 순이다. 한 트랜잭션에서 넣은 작업끼리는
    값이 같아 순서가 정해지지 않는데, `confirm` 은 트랜잭션마다 많아야 하나를 넣으므로
    운영에서는 생기지 않는다.
    """
    stmt = (
        select(Task.status)
        .where(
            Task.type == FEEDBACK_TASK_TYPE,
            Task.payload["mealId"].astext == str(meal_id),
        )
        .order_by(Task.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID,
    meal_id: uuid.UUID,
    body: str,
    reasoning: str | None,
    suggestions: list[dict[str, Any]] | None,
    model_version: str | None,
    safety_status: SafetyStatus,
) -> uuid.UUID:
    """있으면 덮고 없으면 만든다. 행 id 를 돌려준다. flush 까지만 — 커밋은 부르는 쪽(워커는 큐).

    **`ON CONFLICT` 한 문장이다.** 같은 끼니 작업이 두 번 등록되면(재확정 · 재시도) 두 번째
    INSERT 가 `(meal_id)` UNIQUE 에 걸려 커밋이 통째로 깨진다.

    **덮어써도 id 는 그대로다.** `invalidate_by_meal` 이 행을 지우지 않고 비우는 이유와
    같다 — `daily_feedback_sources.meal_feedback_id` 가 이 id 를 물고 있다.

    `safety_status` 는 **빠짐없이 매번** 쓴다. 비운 행은 `REVIEW_REQUIRED` 로 되돌아가
    있지만, 여기서 빠뜨리면 검수 안 된 새 문장이 옛 판정을 물려받는다 (절대 규칙 1).
    """
    values = {
        "body": body,
        "reasoning": reasoning,
        "suggestions": suggestions,
        "model_version": model_version,
        "safety_status": safety_status,
    }
    stmt = (
        insert(MealFeedback)
        .values(user_id=user_id, meal_id=meal_id, **values)
        .on_conflict_do_update(index_elements=[MealFeedback.meal_id], set_=values)
        .returning(MealFeedback.id)
    )
    meal_feedback_id = db.execute(stmt).scalar_one()
    db.flush()
    return meal_feedback_id


class SuggestedNutrient(NamedTuple):
    """제안 음식이 채워 주는 성분 한 줄. 응답 `{ code, amount, unit }` 그대로다."""

    code: NutrientCode
    amount: Decimal
    unit: NutrientUnit


class SuggestedNutrients(NamedTuple):
    """제안 음식 하나가 채워 주는 성분들. `_SUGGESTION_NUTRIENTS` 순서다."""

    nutrients: list[SuggestedNutrient]


_SUGGESTION_NUTRIENTS: Final[tuple[tuple[NutrientCode, str, NutrientUnit], ...]] = (
    (NutrientCode.CALORIE, "calories", NutrientUnit.KCAL),
    (NutrientCode.PROTEIN, "protein_g", NutrientUnit.G),
    (NutrientCode.FAT, "fat_g", NutrientUnit.G),
    (NutrientCode.CARB, "carbohydrate_g", NutrientUnit.G),
    (NutrientCode.FIBER, "fiber_g", NutrientUnit.G),
    (NutrientCode.SODIUM, "sodium_mg", NutrientUnit.MG),
)
"""제안에 실을 성분 — `NutrientCode` 전부다. 이 음식이 채워 주는 성분을 빠짐없이 보여 준다.

AI 는 성분을 알려 주지 않으므로(`ai-stub/schemas.py::Suggestion` 에는 `foodName` ·
`advice` · `candidateFoodRefId` 뿐이다) `food_refs` 에서 값이 있는 것을 싣는다.

**값은 컬럼 단위 그대로다 — 단위 변환을 하지 않는다**(100g 환산만 한다). 세 번째 칸이
그 컬럼의 단위이고 응답 `unit` 으로 그대로 나간다. 끼니 피드백(`suggestions[].nutrients[]`)과
챗봇 카드(`effects[]`)가 이 표 하나를 쓴다 — 단위를 따로 적으면 한쪽만 틀린다.
"""


def invalidate_by_meal(db: Session, meal_id: uuid.UUID) -> None:
    """이 식사의 피드백 **내용만** 비운다. 커밋은 `services/` 가 한다 (절대 규칙 5).

    음식을 고쳐 다시 확정하면 점수는 `evaluation_crud.upsert` 가 덮지만 AI 가 쓴
    문장은 아무도 안 건드린다. 닭가슴살을 더해 재확정해도 "단백질 비중이 낮았어요"
    가 그대로 나가는 이유다. 상태 가드(`services/feedback.py::get_feedback`)는
    확정 상태가 아닌 동안만 막아서, 재확정으로 `EVALUATED` 가 되는 순간 풀린다.

    **행은 지우지 않는다.** `daily_feedback_sources.meal_feedback_id` 가
    `ON DELETE CASCADE` 라, 지우면 일일 피드백의 출처 링크가 조용히 사라지고 문장만
    남는다. 점수(`qqs_evaluations`)를 행째 지우는 것과 다른 점이 여기다 — 그쪽에는
    이런 자식이 없다.

    내용을 비우면 `get_feedback` 이 `body is None` 을 보고 `PENDING` 을 낸다.
    워커가 새 문장을 채우면 다시 `READY` 가 된다.

    `safety_status` 는 `REVIEW_REQUIRED` 로 되돌린다 — 판정이 내용에 붙은 것이라서다.

    `model_version` 은 남긴다 — 어느 모델이 쓴 문장이었는지는 지운 뒤에도 쓸모가
    있고, 무효 여부는 `body` 가 말한다.

    행이 없으면 0 행 UPDATE 다. 호출부가 "피드백이 있었나" 를 따질 필요가 없다.
    """
    db.execute(
        update(MealFeedback)
        .where(MealFeedback.meal_id == meal_id)
        .values(
            body=None,
            reasoning=None,
            suggestions=None,
            # **판정도 함께 되돌린다.** `safety_status` 는 내용에 붙은 판정이라
            # 내용이 무효면 같이 무효다. 모델이 `server_default` 를
            # `REVIEW_REQUIRED` 로 두고 "가드레일을 통과해야만 SAFE" 라는
            # fail-closed 를 약속하는데, 내용만 비우면 `body = NULL` 인데
            # `SAFE` 인 어긋난 상태가 남는다 — 워커가 나중에 upsert 할 때
            # `safety_status` 를 빠뜨리면 검수 안 된 문장이 옛 `SAFE` 를
            # 물려받아 그대로 노출된다 (절대 규칙 1).
            safety_status=SafetyStatus.REVIEW_REQUIRED,
        )
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
        for _, field, _ in _SUGGESTION_NUTRIENTS
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
        nutrients = [
            SuggestedNutrient(code=code, amount=value, unit=unit)
            for (code, _, unit), value in zip(_SUGGESTION_NUTRIENTS, row[1:], strict=True)
            # NULL 은 "모른다" 다. 0 으로 채우면 "이 음식에는 단백질이 없다" 가 된다.
            #
            # NaN 도 같이 거른다 — `'NaN'::numeric` 은 성분 컬럼에 그냥 저장되고
            # `is not None` 을 통과한다. 그대로 두면 `float('nan')` 이 되어 JSON 에
            # `null` 로 직렬화되는데, `amount: float` 는 **required non-nullable**
            # 이라 응답이 자기 OpenAPI 계약을 어긴다. 생성 클라이언트가
            # `amount: number` 로 받아 그대로 계산하면 FE 가 터진다.
            if value is not None and value.is_finite()
        ]
        resolved[row.id] = SuggestedNutrients(nutrients=nutrients)
    return resolved


class StoredSuggestion(NamedTuple):
    """`meal_feedbacks.suggestions` 원소 하나를 쓸 수 있는 모양으로 바꾼 것."""

    food_name: str
    advice: str
    ref_id: str | None


def parse_suggestion(item: object) -> StoredSuggestion | None:
    """저장된 제안 한 건을 쓸 수 있는 모양으로 바꾼다. 못 쓰면 `None`.

    **이 값은 신뢰할 수 없는 입력이다.** 컬럼이 JSONB 라 DB 는 아무 JSON 이나 받고,
    쓰는 쪽은 AI 응답을 담을 워커다. 계약이 바뀌거나 부분 응답이 오면 모양이 어긋난다.
    검증 없이 `item.get(...)` 을 부르면 문자열 원소 하나에 `AttributeError` 가 나고,
    **그 끼니 피드백 전체(요약·근거까지)를 못 읽는다.** 한 건이 깨졌다고 나머지를
    버릴 이유는 없다.

    `or ""` 를 쓰는 건 `{"foodName": null}` 때문이다 — 키가 있으면
    `get(k, "")` 의 기본값이 안 먹어 `None` 이 그대로 나오고, `food_name: str` 에서
    터진다.

    **crud 에 두는 이유:** 끼니 피드백 화면(`services/feedback.py`)과 챗봇 첫 메시지
    (`services/chat.py`)가 같은 규칙으로 걸러야 한다. services 끼리는 서로 부르지
    않으므로(절대 규칙 5) 둘 다 내려다보는 이 컬럼의 파일에 둔다.
    """
    if not isinstance(item, dict):
        return None

    food_name = item.get("foodName") or ""
    advice = item.get("advice") or ""
    if not isinstance(food_name, str) or not isinstance(advice, str):
        return None
    # **하나라도 비면 버린다.** 명세의 카드는 음식 이름과 조언 문구가 짝이라
    # (`{foodName, nutrients, advice}`), 한쪽이 없으면 FE 가 반쪽 카드를 그린다.
    if not food_name or not advice:
        return None

    ref_id = item.get("candidateFoodRefId")
    # str 이 아닌 건 조회에 넘기지 않는다 — int 는 `varchar = integer` 로 DB 가
    # 거부하고, list 는 `set()` 에서 unhashable 로 터진다.
    return StoredSuggestion(food_name, advice, ref_id if isinstance(ref_id, str) else None)


def parse_suggestions(stored: object) -> list[StoredSuggestion]:
    """저장된 제안 배열 전체. 배열이 아니면 `[]`, 깨진 원소만 빠진다."""
    if not isinstance(stored, list):
        return []
    return [parsed for item in stored if (parsed := parse_suggestion(item)) is not None]
