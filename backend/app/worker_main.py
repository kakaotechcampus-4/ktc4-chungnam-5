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

from app.infra.ai import AiClient, build_ai_client
from app.infra.queue import ReceivedTask, TaskQueue, build_task_queue

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
    """식사 분석. AI 를 부르고 결과를 받는다.

    아직 DB 에 저장하지 않는다 — 모델이 이 브랜치에 없다.
    저장이 붙으면 meal_items INSERT 와 status=REVIEW_REQUIRED 가 여기 들어간다.
    """
    body = task.body
    result = ai.analyze_meal(
        {
            "mealId": body["mealId"],
            "mealType": body["mealType"],
            "eatenAt": body["eatenAt"],
            "stage": body["stage"],
            "imageUrl": body.get("imageUrl"),
            "rawText": body.get("rawText"),
        }
    )

    # 포즈 정보는 민감 건강정보다. 음식명·이미지 키를 로그에 남기지 않는다.
    logger.info(
        "분석 완료 mealId=%s items=%d safetyStatus=%s",
        result["mealId"],
        len(result["items"]),
        result["safetyStatus"],
    )


def handle(task: ReceivedTask, ai: AiClient) -> None:
    """작업 하나를 처리한다."""
    task_type = task.body.get("type")

    if task_type == "meal.analyze":
        return analyze_meal(task, ai)
    if task_type == "meal.evaluate":
        raise NotImplementedError("Q/Q/S 평가 파이프라인 미구현")
    if task_type == "feedback.generate":
        raise NotImplementedError("피드백 생성 파이프라인 미구현")

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
