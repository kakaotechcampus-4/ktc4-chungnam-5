"""`meal.analyze` — 사진·텍스트에서 음식을 인식해 `meal_items` 에 저장한다.

`ANALYZING → REVIEW_REQUIRED`. 사용자가 인식 결과를 확인하면 거기서 `meal.evaluate` 로 이어진다.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from app.crud import food as crud_food
from app.crud import meal as crud_meal
from app.crud import meal_item as crud_meal_item
from app.db.session import SessionLocal
from app.infra.ai import AiClient
from app.infra.queue import ReceivedTask
from app.models.enums import MealStatus
from app.services.meal import to_grams

logger = logging.getLogger("worker.analyze_meal")


def run(task: ReceivedTask, ai: AiClient) -> None:
    """**재배달에 견뎌야 한다.**

    SQS 는 at-least-once 라 같은 메시지가 두 번 올 수 있다. 상태가 ANALYZING 이 아니면
    이미 처리된 것으로 보고 넘어간다 — 그러지 않으면 재배달마다 항목이 중복으로 쌓인다.
    """
    body = task.body
    meal_id = body["mealId"]

    with SessionLocal() as db:
        meal = crud_meal.get(db, meal_id)
        if meal is None:
            # 재시도해도 생기지 않는다. 삭제된 식사일 수 있다.
            raise ValueError(f"식사를 찾을 수 없다: {meal_id}")

        if meal.status is not MealStatus.ANALYZING:
            logger.info("이미 처리된 식사다. 건너뛴다 status=%s", meal.status.value)
            return

        result = ai.analyze_meal(
            {
                "mealId": meal_id,
                "mealType": body["mealType"],
                "eatenAt": body["eatenAt"],
                "stage": body["stage"],
                "imageUrl": body.get("imageUrl"),
                "rawText": body.get("rawText"),
            }
        )

        items = result["items"]
        # 존재하지 않는 food_ref_id 를 그대로 넣으면 FK 위반으로 커밋이 통째로 깨진다.
        known = crud_food.get_many(db, (item.get("candidateFoodRefId") for item in items))

        crud_meal_item.delete_model_items(db, meal)

        unconverted = 0
        for item in items:
            amount_g = to_grams(item.get("estimatedAmount"), item.get("unit"))
            if amount_g is None:
                unconverted += 1

            confidence = item.get("confidence")
            crud_meal_item.add(
                db,
                meal,
                original_food_name=item["originalFoodName"],
                estimated_amount_g=amount_g,
                confidence=None if confidence is None else Decimal(str(confidence)),
                food_ref_id=(
                    item["candidateFoodRefId"] if item.get("candidateFoodRefId") in known else None
                ),
                raw_ai_result=item,
            )

        crud_meal.set_status(db, meal, MealStatus.REVIEW_REQUIRED)
        db.commit()

    # 포즈 정보는 민감 건강정보다. 음식명·이미지 키를 로그에 남기지 않는다(규칙 6).
    logger.info(
        "분석 완료 mealId=%s items=%d 매칭=%d g환산불가=%d safetyStatus=%s",
        meal_id,
        len(items),
        len(known),
        unconverted,
        result["safetyStatus"],
    )
