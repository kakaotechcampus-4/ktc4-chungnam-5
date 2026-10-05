"""Worker 엔트리포인트.

    python -m app.worker_main

API 컨테이너와 **별도 프로세스**로 돈다. API 는 큐에 넣고 202 를 돌려줄 뿐이고,
꺼내서 처리하는 건 여기다 — AI 분석이 10~30초 걸리기 때문이다.

큐는 PostgreSQL 테이블(`task_queue`)이다. 컨테이너를 따로 띄우지 않는다.
작업은 lease 로 빌린다 — AI 를 기다리는 동안 DB 커넥션을 쥐지 않는다.

이 파일은 기동만 한다. 실제 내용은 `app/worker/` 에 있다:

    worker/loop.py       폴링 루프 + 커밋 시점
    worker/dispatch.py   작업 타입 → 핸들러
    worker/jobs/         작업 하나당 파일 하나
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

from app.infra.ai import AiSettings, build_ai_client
from app.infra.queue import QueueSettings, build_task_queue
from app.worker import loop

logger = logging.getLogger("worker")


def check_lease(queue_settings: QueueSettings, ai_settings: AiSettings) -> None:
    """lease 가 AI 타임아웃보다 길어야 한다. 아니면 정상 작업이 처리 도중 회수돼 두 번 돈다."""
    if queue_settings.QUEUE_LEASE_SEC <= ai_settings.AI_TIMEOUT_SEC:
        raise ValueError(
            f"QUEUE_LEASE_SEC({queue_settings.QUEUE_LEASE_SEC}) 는 "
            f"AI_TIMEOUT_SEC({ai_settings.AI_TIMEOUT_SEC}) 보다 커야 한다"
        )


def _stop(signum: int, frame: FrameType | None) -> None:
    logger.info("종료 신호를 받았다. 현재 작업을 마치고 멈춘다.")
    loop.request_stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    queue_settings = QueueSettings()
    ai_settings = AiSettings()
    check_lease(queue_settings, ai_settings)

    loop.run(build_task_queue(queue_settings), build_ai_client(ai_settings), queue_settings)


if __name__ == "__main__":
    main()
