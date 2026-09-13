"""Worker 엔트리포인트.

큐를 롱 폴링해 작업을 꺼내 처리한다. API 컨테이너와 별도 프로세스로 돈다.

    python -m app.worker_main

**삭제 시점이 이 루프의 전부다.** 처리에 성공했을 때만 지운다.
실패하면 지우지 않고 넘어가 visibility timeout 뒤 재배달되게 하고,
maxReceiveCount(3회)를 넘기면 큐가 DLQ 로 옮긴다.
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

from decimal import Decimal

from sqlalchemy.orm import Session

from app.crud import food as crud_food
from app.crud import meal as crud_meal
from app.db.session import SessionLocal
from app.infra.ai import AiClient, build_ai_client
from app.infra.queue import ReceivedTask, TaskQueue, build_task_queue
from app.models.enums import MealStatus
from app.services.meal import to_grams

logger = logging.getLogger("worker")

RECEIVE_BATCH = 1
RECEIVE_WAIT_SECONDS = 5

_running = True


def _stop(signum: int, frame: FrameType | None) -> None:
    """처리 중인 작업을 끝내고 루프를 빠져나온다."""
    global _running
    logger.info("종료 신호를 받았다. 현재 작업을 마치고 멈춘다.")
    _running = False


def analyze_meal(task: ReceivedTask, ai: AiClient) -> None:
    """식사 분석. AI 를 부르고 결과를 meal_items 에 저장한다.

    **재배달에 견뎌야 한다.** SQS 는 at-least-once 라 같은 메시지가 두 번 올 수 있다.
    상태가 ANALYZING 이 아니면 이미 처리된 것으로 보고 넘어간다 — 그러지 않으면
    재배달마다 항목이 중복으로 쌓인다.
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

        crud_meal.delete_model_items(db, meal)

        unconverted = 0
        for item in items:
            amount_g = to_grams(item.get("estimatedAmount"), item.get("unit"))
            if amount_g is None:
                unconverted += 1

            confidence = item.get("confidence")
            crud_meal.add_item(
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

    # 포즈 정보는 민감 건강정보다. 음식명·이미지 키를 로그에 남기지 않는다.
    logger.info(
        "분석 완료 mealId=%s items=%d 매칭=%d g환산불가=%d safetyStatus=%s",
        meal_id,
        len(items),
        len(known),
        unconverted,
        result["safetyStatus"],
    )


def handle(task: ReceivedTask, ai: AiClient) -> None:
    """작업 하나를 처리한다.

    피드백 세 종류를 한 타입으로 묶지 않는다. AI 쪽은 끼니와 하루를 같은
    `/short-feedback` 으로 받지만(하는 일이 같다), Worker 쪽은 다르다 —
    모으는 데이터도, 쓰는 테이블도 셋이 전부 다르다. 묶으면 진짜 구분자가
    본문 안에 숨고 DLQ 에 쌓였을 때 어느 피드백이 죽었는지도 알 수 없다.
    """
    task_type = task.body.get("type")

    if task_type == "meal.analyze":
        return analyze_meal(task, ai)
    if task_type == "meal.evaluate":
        # Rule Engine(순수 함수) → qqs_evaluations. AI 를 부르지 않는다
        raise NotImplementedError("Q/Q/S 평가 파이프라인 미구현")
    if task_type == "feedback.meal":
        # /short-feedback scope=MEAL → meal_feedbacks
        raise NotImplementedError("끼니 피드백 파이프라인 미구현")
    if task_type == "feedback.daily":
        # /short-feedback scope=DAILY → daily_feedbacks
        raise NotImplementedError("일일 피드백 파이프라인 미구현")
    if task_type == "feedback.long":
        # /long-feedback → long_term_feedbacks
        raise NotImplementedError("장기 피드백 파이프라인 미구현")

    raise ValueError(f"알 수 없는 작업 타입: {task_type!r}")


def run(queue: TaskQueue, ai: AiClient) -> None:
    logger.info("워커 시작. 큐를 폴링한다.")

    while _running:
        for task in queue.receive(max_count=RECEIVE_BATCH, wait_seconds=RECEIVE_WAIT_SECONDS):
            try:
                handle(task, ai)
            except Exception:
                # 지우지 않는다 → visibility timeout 뒤 재배달 → 3회 넘으면 DLQ.
                # 포즈 정보가 로그에 남지 않도록 본문은 찍지 않는다.
                logger.exception(
                    "작업 처리 실패 (배달 %d회째). 재시도에 맡긴다.",
                    task.receive_count,
                )
                continue

            queue.delete(task.receipt)
            logger.info("작업 완료 (배달 %d회째)", task.receive_count)

    logger.info("워커 종료.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    run(build_task_queue(), build_ai_client())


if __name__ == "__main__":
    main()
