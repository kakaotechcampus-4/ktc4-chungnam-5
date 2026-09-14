"""Worker 엔트리포인트.

    python -m app.worker_main

API 컨테이너와 **별도 프로세스**로 돈다. API 는 큐에 넣고 202 를 돌려줄 뿐이고,
꺼내서 처리하는 건 여기다 — AI 분석이 10~30초 걸리기 때문이다.

이 파일은 기동만 한다. 실제 내용은 `app/worker/` 에 있다:

    worker/loop.py       폴링 루프 + 삭제 시점
    worker/dispatch.py   작업 타입 → 핸들러
    worker/jobs/         작업 하나당 파일 하나
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

from app.infra.ai import build_ai_client
from app.infra.queue import build_task_queue
from app.worker import loop

logger = logging.getLogger("worker")


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

    loop.run(build_task_queue(), build_ai_client())


if __name__ == "__main__":
    main()
