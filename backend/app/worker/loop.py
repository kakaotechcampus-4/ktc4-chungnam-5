"""큐 폴링 루프.

**삭제 시점이 이 파일의 전부다.** 처리에 성공했을 때만 지운다. 실패하면 지우지 않고
넘어가 visibility timeout 뒤 재배달되게 하고, maxReceiveCount(3회)를 넘기면 큐가
DLQ 로 옮긴다.

작업 핸들러와 파일을 나눈 건 이 때문이다. 여기가 틀리면 작업이 조용히 사라지는데,
핸들러를 붙이다가 실수로 건드리기 쉬운 자리에 두고 싶지 않다.
"""

from __future__ import annotations

import logging

from app.infra.ai import AiClient
from app.infra.queue import TaskQueue
from app.worker.dispatch import handle

logger = logging.getLogger("worker")

RECEIVE_BATCH = 1
RECEIVE_WAIT_SECONDS = 5

_running = True


def request_stop() -> None:
    """루프를 멈춘다. 처리 중인 작업은 끝까지 간다."""
    global _running
    _running = False


def run(queue: TaskQueue, ai: AiClient) -> None:
    logger.info("워커 시작. 큐를 폴링한다.")

    while _running:
        for task in queue.receive(max_count=RECEIVE_BATCH, wait_seconds=RECEIVE_WAIT_SECONDS):
            try:
                handle(task, ai)
            except Exception:
                # 지우지 않는다 → 재배달 → 3회 넘으면 DLQ.
                # 포즈 정보가 로그에 남지 않도록 본문은 찍지 않는다(규칙 6).
                logger.exception(
                    "작업 처리 실패 (배달 %d회째). 재시도에 맡긴다.",
                    task.receive_count,
                )
                continue

            queue.delete(task.receipt)
            logger.info("작업 완료 (배달 %d회째)", task.receive_count)

    logger.info("워커 종료.")
