"""`feedback.meal` — 끼니 하나에 대한 피드백 문장을 만든다.

`/short-feedback` `scope=MEAL` → `meal_feedbacks`.

**채점(Q/Q/S)은 이미 끝나 있다.** Rule Engine 이 확인 API 에서 동기로 돌려
`qqs_evaluations` 에 저장한 뒤다. 여기서는 그 점수를 문장으로 옮기기만 한다.
그래서 AI 가 죽어도 점수는 남는다.

이 작업은 **확인 API(`POST /meals/{mealId}/confirm`)가 큐에 넣는다** — 채점과 같은
트랜잭션이다(`services/evaluation/__init__.py::confirm`). 제안 화면을 열 때 넣지 않는
이유는 하루 피드백이 끼니 피드백을 근거로 쓰기 때문이다 — 화면을 안 연 끼니가 하루
요약에서 조용히 빠진다(`crud/daily_feedback.py::list_day_evidence`).

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `ShortFeedbackRequest` · `ShortFeedbackResponse` 다.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.crud import evaluation as evaluation_crud
from app.crud import feedback as feedback_crud
from app.crud import food as food_crud
from app.crud import meal as meal_crud
from app.crud import satiety as satiety_crud
from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask
from app.models.enums import MealStatus, SafetyStatus
from app.models.evaluation import QQSEvaluation
from app.models.meal import Meal, MealItem
from app.services.meal import item_nutrition, resolved_amount
from app.services.nutrition import resolve_by_food_ref_id

logger = logging.getLogger("worker.feedback_meal")


def _number(value: Decimal | int | None) -> float | None:
    # AI payload 는 JSON 으로 나간다 — Decimal 을 싣지 않는다. None 은 "모른다" 라 그대로 둔다.
    return None if value is None else float(value)


def _skip_reason(meal: Meal | None) -> str | None:
    """이 식사에 피드백을 쓰면 안 되는 이유. 써도 되면 None.

    둘 다 **낡은 작업**이지 실패가 아니다 — raise 하면 재시도가 AI 를 헛되이 부르다 DLQ 로 간다.

    - 없거나 지워졌다 — 쓸 곳이 없다
    - 확정 상태가 아니다 — 음식을 고쳐 재계산 대기로 돌아갔다. 지금 쓰면 고치기 전 끼니를
      보고 쓴 문장이 남는다. 다시 확정하면 그 확정이 새 작업을 넣는다
    """
    if meal is None or meal.deleted_at is not None:
        return "MEAL_NOT_FOUND"
    if meal.status is not MealStatus.EVALUATED:
        return "MEAL_NOT_EVALUATED"
    return None


def _scored_as(evaluation: QQSEvaluation) -> tuple[Any, ...]:
    """문장이 딛고 선 채점 — 쓰기 직전에 이게 그대로인지 본다.

    **id 도 넣는다.** 음식을 고치면 `mark_recalculating` 이 평가 행을 지우고 재확정이
    새로 넣는데, 점수는 같게 나올 수 있다 — Quantity 는 구간 안이면 100 에 머물고,
    체중이 없으면 Quality 는 None 이다. 점수만 비교하면 음식이 바뀐 끼니에 옛 음식으로
    쓴 문장이 들어간다.
    """
    return (
        evaluation.id,
        evaluation.stage_at_evaluation,
        evaluation.quantity_score,
        evaluation.quality_score,
        evaluation.satiety_score,
    )


def _item_payload(db: Session, item: MealItem) -> dict[str, Any]:
    """항목 하나 → `FeedbackItem`. 성분은 BE 가 채운다 — AI 는 음식을 지목만 했다.

    양과 성분은 **화면과 같은 함수로** 만든다(`resolved_amount` · `item_nutrition`).
    여기서 따로 계산하면 사용자가 본 숫자와 AI 가 보고 쓴 숫자가 갈라진다.
    """
    amount_g, amount, unit = resolved_amount(item)
    public_db = (
        None
        if item.food_ref_id is None
        else resolve_by_food_ref_id(db, food_ref_id=item.food_ref_id, amount_g=amount_g)
    )
    nutrition, _source = item_nutrition(item, public_db)
    return {
        "displayName": item.display_name,
        "amount": _number(amount),
        "unit": unit,
        "nutrition": None
        if nutrition is None
        else {
            "kcal": _number(nutrition.kcal),
            "proteinG": _number(nutrition.protein_g),
            "fatG": _number(nutrition.fat_g),
            "carbG": _number(nutrition.carb_g),
            "fiberG": _number(nutrition.fiber_g),
            "sodiumMg": _number(nutrition.sodium_mg),
        },
    }


def _satiety_payload(db: Session, meal_id: uuid.UUID) -> dict[str, Any] | None:
    """`satiety_logs` + `satiety_checkins` → `SatietyContext`. 기록이 하나도 없으면 None."""
    log = satiety_crud.get_by_meal(db, meal_id)
    checkins = satiety_crud.list_checkins(db, meal_id)
    if log is None and not checkins:
        return None
    return {
        "beforePct": log.satiety_before if log else None,
        "afterPct": log.satiety_after if log else None,
        "checkins": [
            {"checkinOffsetHours": row.checkin_offset_hours, "satietyPct": row.satiety_pct}
            for row in checkins
        ],
        "hungerReturnMinutes": log.hunger_return_minutes if log else None,
        "userComment": log.user_comment if log else None,
    }


def _stored_suggestions(db: Session, raw: object) -> list[dict[str, Any]] | None:
    """AI 제안을 저장할 모양으로 바꾼다 — `{foodName, advice, candidateFoodRefId}`.

    **`candidateFoodRefId` 는 `food_refs` 에 실재할 때만 남긴다.** 없는 id 는 읽는 쪽이
    성분을 못 채울 뿐이지만, 남겨 두면 "공공 DB 의 이 음식" 이라는 거짓 링크가 된다.
    제안 자체는 버리지 않는다 — "두부 반 모" 는 성분 숫자 없이도 쓸모가 있다.

    모양이 깨진 원소(dict 가 아닌 것)는 버린다. 문구가 빈 원소는 읽는 쪽
    (`services/feedback.py::_coerce`)이 거르므로 여기서 또 판단하지 않는다.
    """
    if not isinstance(raw, list):
        return None
    items = [item for item in raw if isinstance(item, dict)]
    # 문자열이 아닌 id 는 없는 것으로 본다 — list 는 해시가 안 돼 집합·`in` 에서 터진다.
    refs = [
        ref if isinstance(ref := item.get("candidateFoodRefId"), str) else None
        for item in items
    ]
    known = food_crud.existing_ids(db, {ref for ref in refs if ref is not None})
    return [
        {
            "foodName": item.get("foodName"),
            "advice": item.get("advice"),
            "candidateFoodRefId": ref if ref in known else None,
        }
        for item, ref in zip(items, refs, strict=True)
    ]


def run(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None:
    """
    순서:
      1. 식사를 읽는다. 없거나 지워졌거나 확정 상태가 아니면 AI 를 부르지 않고 끝낸다
      2. `qqs_evaluations` 에서 Q/Q/S 를 읽는다. 없으면 채점이 아직이다 — raise 해서
         재시도에 맡긴다
      3. `items` · 포만감 컨텍스트를 만들고 AI 를 부른다
      4. 식사를 잠그고 다시 본다 — AI 를 기다리는 사이 지워지거나 고쳐졌거나 다시
         채점됐으면 쓰지 않는다
      5. `meal_feedbacks` 에 upsert
    """
    meal_id = uuid.UUID(task.payload["mealId"])

    meal = meal_crud.get_meal_for_worker(db, meal_id)
    if (reason := _skip_reason(meal)) is not None:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=%s", meal_id, reason)
        return {"skipped": reason}

    evaluation = evaluation_crud.get_by_meal(db, meal.id)
    if evaluation is None:
        raise LookupError(f"meal {meal_id} 의 Q/Q/S 평가가 아직 없다")
    scored_as = _scored_as(evaluation)

    result = ai.short_feedback(
        {
            "scope": "MEAL",
            "userId": str(meal.user_id),
            # 채점 시점의 단계다. 점수와 같은 기준으로 문장을 써야 한다.
            "stage": evaluation.stage_at_evaluation.value,
            # 채점 못 한 축은 null 이다 — 0 으로 채우면 "못 쟀다" 가 "바닥이다" 가 된다.
            "qqs": {
                "quantity": _number(evaluation.quantity_score),
                "quality": _number(evaluation.quality_score),
                "satiety": _number(evaluation.satiety_score),
            },
            "mealId": str(meal.id),
            "items": [_item_payload(db, item) for item in meal.items],
            "satiety": _satiety_payload(db, meal.id),
        }
    )

    # AI 를 부르는 동안(최대 45초) 잠그지 않았다 — 그 사이 사용자가 지우거나 고쳤을 수 있다.
    # 다시 읽기 전에 identity map 을 비운다. 객체 하나가 아니라 전부다 — 그 사이 평가
    # 행이 지워졌으면 옛 객체는 이미 세션에서 떨어져 있을 수 있다. 잠금보다 먼저 해야
    # 방금 잠그며 새로 읽은 식사까지 만료시켜 SELECT 를 한 번 더 내지 않는다.
    db.expire_all()
    meal = meal_crud.lock_meal_for_worker(db, meal_id)
    if (reason := _skip_reason(meal)) is not None:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=%s", meal_id, reason)
        return {"skipped": reason}

    # 상태만으로는 부족하다 — 포만감만 고쳐 재확정하면 EVALUATED 그대로다. 그 재확정이
    # 넣은 작업이 다른 워커에서 먼저 끝날 수 있어서, 여기서 쓰면 옛 점수로 쓴 문장이
    # 새 문장을 덮고 영영 남는다. 확정이 식사 행을 먼저 잠그므로(`services/evaluation`)
    # 잠금을 얻은 지금 읽는 평가는 커밋이 끝난 값이다.
    current = evaluation_crud.get_by_meal(db, meal.id)
    if current is None or _scored_as(current) != scored_as:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=EVALUATION_CHANGED", meal_id)
        return {"skipped": "EVALUATION_CHANGED"}

    suggestions = _stored_suggestions(db, result.get("suggestions"))
    # safetyStatus 는 AI 가 준 그대로 저장한다. SAFE 로 올리지 않는다 (규칙 1).
    meal_feedback_id = feedback_crud.upsert(
        db,
        user_id=meal.user_id,
        meal_id=meal.id,
        body=result["body"],
        reasoning=result.get("reasoning"),
        suggestions=suggestions,
        model_version=result["modelVersion"],
        safety_status=SafetyStatus(result["safetyStatus"]),
    )

    # 커밋하지 않는다. 이 세션은 큐가 작업을 잠근 트랜잭션이고, 큐가 DONE 과 함께
    # 한 번에 커밋한다. 여기서 터지면 도메인 변경까지 통째로 롤백된다.

    # 로그·반환값(task_queue.result)에는 식별자와 개수만 — 음식명·본문·제안 문구는
    # 남기지 않는다 (규칙 6).
    logger.info(
        "끼니 피드백 완료 mealId=%s safetyStatus=%s",
        meal_id,
        result["safetyStatus"],
    )

    return {
        "mealFeedbackId": str(meal_feedback_id),
        "suggestionCount": len(suggestions or []),
    }
