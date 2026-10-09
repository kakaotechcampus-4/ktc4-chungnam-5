"""Worker 엔트리포인트.

    python -m app.worker_main

API 컨테이너와 **별도 프로세스**로 돈다. API 는 큐에 넣고 202 를 돌려줄 뿐이고,
꺼내서 처리하는 건 여기다 — AI 분석이 10~30초 걸리기 때문이다.

큐는 PostgreSQL 테이블(`task_queue`)이다. 컨테이너를 따로 띄우지 않는다.
작업은 lease 로 빌린다 — AI 를 기다리는 동안 DB 커넥션을 쥐지 않는다.

폴링 루프를 스레드 `QUEUE_WORKER_THREADS` 개로 돌린다. 작업 시간이 거의 AI 응답 대기라
GIL 이 걸림돌이 되지 않고, 컨테이너를 여러 개 띄우는 것보다 커넥션·메모리가 적게 든다.
같은 작업을 두 스레드가 집지 않는 건 큐(SKIP LOCKED + lease)가 보장한다.

이 파일은 기동만 한다. 실제 내용은 `app/worker/` 에 있다:

    worker/loop.py       폴링 루프 + 커밋 시점
    worker/dispatch.py   작업 타입 → 핸들러
    worker/jobs/         작업 하나당 파일 하나
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType
from typing import Protocol

from app.core.config import get_settings
from app.infra.ai import AiClient, AiSettings, build_ai_client
from app.infra.queue import QueueSettings, TaskQueue, build_task_queue
from app.worker import loop

logger = logging.getLogger("worker")


def check_lease(queue_settings: QueueSettings, ai_settings: AiSettings) -> None:
    """lease 가 AI 타임아웃보다 길어야 한다. 아니면 정상 작업이 처리 도중 회수돼 두 번 돈다."""
    if queue_settings.QUEUE_LEASE_SEC <= ai_settings.AI_TIMEOUT_SEC:
        raise ValueError(
            f"QUEUE_LEASE_SEC({queue_settings.QUEUE_LEASE_SEC}) 는 "
            f"AI_TIMEOUT_SEC({ai_settings.AI_TIMEOUT_SEC}) 보다 커야 한다"
        )


class _PoolSettings(Protocol):
    DB_POOL_SIZE: int
    DB_MAX_OVERFLOW: int


def check_threads(queue_settings: QueueSettings, db_settings: _PoolSettings) -> None:
    """스레드 수가 1 이상이고 DB 풀 상한 이하여야 한다.

    스레드 하나는 한순간에 커넥션을 하나씩만 쓴다(claim · read · complete 가 차례로 연다).
    상한을 넘기면 남는 스레드는 커넥션을 기다리며 서 있을 뿐이다.
    """
    limit = db_settings.DB_POOL_SIZE + db_settings.DB_MAX_OVERFLOW
    threads = queue_settings.QUEUE_WORKER_THREADS
    if not 1 <= threads <= limit:
        raise ValueError(
            f"QUEUE_WORKER_THREADS({threads}) 는 1 이상, "
            f"DB_POOL_SIZE + DB_MAX_OVERFLOW({limit}) 이하여야 한다"
        )


def run_threads(queue: TaskQueue, ai: AiClient, settings: QueueSettings) -> int:
    """폴링 루프를 스레드 N 개로 돌리고 전부 끝날 때까지 기다린다.

    모두 정상으로 멈추면 0, 하나라도 예상치 못하게 죽으면 1 이다. 하나가 죽으면 나머지도
    멈춘다 — 조용히 줄어든 처리량으로 계속 도는 것보다, 프로세스가 끝나 compose 의
    `restart: unless-stopped` 가 다시 띄우는 쪽이 낫다.
    """
    crashed: list[str] = []

    def target() -> None:
        try:
            loop.run(queue, ai, settings)
        except BaseException:
            logger.exception("워커 스레드가 죽었다. 나머지도 멈춘다.")
            crashed.append(threading.current_thread().name)
            loop.request_stop()

    threads = [
        threading.Thread(target=target, name=f"worker-{n}")
        for n in range(1, settings.QUEUE_WORKER_THREADS + 1)
    ]
    for thread in threads:
        thread.start()
    # 시그널 핸들러는 메인 스레드에서만 돈다. timeout 없이 join 하면 플랫폼에 따라(Windows)
    # 끝날 때까지 SIGINT 처리가 미뤄진다. 짧게 끊어 기다린다.
    for thread in threads:
        while thread.is_alive():
            thread.join(timeout=1.0)
    return 1 if crashed else 0


def _stop(signum: int, frame: FrameType | None) -> None:
    logger.info("종료 신호를 받았다. 현재 작업을 마치고 멈춘다.")
    loop.request_stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    queue_settings = QueueSettings()
    ai_settings = AiSettings()
    check_lease(queue_settings, ai_settings)
    check_threads(queue_settings, get_settings())

    logger.info("워커 스레드 %d개로 시작한다.", queue_settings.QUEUE_WORKER_THREADS)
    sys.exit(run_threads(build_task_queue(queue_settings), build_ai_client(ai_settings), queue_settings))


if __name__ == "__main__":
    main()
