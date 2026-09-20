"""큐 폴링 루프.

**커밋 시점이 이 파일의 전부다.** 처리에 성공했을 때만 `claim` 블록을 정상으로
빠져나가 DONE 이 커밋된다. 예외가 나면 롤백돼 작업이 PENDING 으로 되돌아가고,
QUEUE_MAX_ATTEMPTS 를 넘기면 큐가 FAILED 로 격리한다.

작업 핸들러와 파일을 나눈 건 이 때문이다. 여기가 틀리면 작업이 조용히 사라지는데,
핸들러를 붙이다가 실수로 건드리기 쉬운 자리에 두고 싶지 않다.
"""

from __future__ import annotations

import logging
import time

from app.infra.ai import AiClient
from app.infra.queue import QueueSettings, TaskQueue
from app.worker.dispatch import handle

logger = logging.getLogger("worker")

_running = True


def request_stop() -> None:
    """루프를 멈춘다. 처리 중인 작업은 끝까지 간다."""
    global _running
    _running = False


def run(queue: TaskQueue, ai: AiClient, settings: QueueSettings | None = None) -> None:
    settings = settings or QueueSettings()
    logger.info("워커 시작. 큐를 폴링한다.")

    while _running:
        try:
            with queue.claim() as claim:
                if claim is None:
                    # 롱 폴링이 없으니 직접 쉰다. 안 쉬면 빈 테이블을 쉬지 않고 때린다.
                    time.sleep(settings.QUEUE_POLL_INTERVAL_SEC)
                    continue

                claim.result = handle(claim.db, claim.task, ai)
                logger.info("작업 완료 type=%s", claim.task.type)
        except Exception:
            # 큐가 롤백하고 실패를 기록했다. 여기서는 로그만 남긴다.
            # 포즈 정보가 로그에 남지 않도록 본문은 찍지 않는다(규칙 6).
            logger.exception("작업 처리 실패. 재시도에 맡긴다.")

    logger.info("워커 종료.")
