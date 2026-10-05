"""큐 폴링 루프.

**커밋 시점이 이 파일의 전부다.** 작업 하나를 세 단계로 돌린다:

    load      queue.read()        읽기만 한다. 끝나면 롤백
    call_ai   (세션 없음)          AI 를 기다리는 동안 커넥션을 쥐지 않는다
    apply     queue.complete()    도메인 쓰기 + DONE 을 한 트랜잭션으로 커밋

어디서든 예외가 나면 `queue.fail` 로 넘긴다 — 큐가 PENDING(재시도) 이나 FAILED(격리) 로
돌린다. 종료 신호면 `queue.release` 로 시도를 세지 않고 놓는다. lease 를 잃었으면 아무것도
기록하지 않는다.

작업 핸들러와 파일을 나눈 건 이 때문이다. 여기가 틀리면 작업이 조용히 사라지는데,
핸들러를 붙이다가 실수로 건드리기 쉬운 자리에 두고 싶지 않다.
"""

from __future__ import annotations

import logging
import time

from app.infra.ai import AiClient
from app.infra.queue import Lease, LeaseLostError, QueueSettings, TaskQueue
from app.worker.dispatch import get_job
from app.worker.job import Skip

logger = logging.getLogger("worker")

_running = True


def request_stop() -> None:
    """루프를 멈춘다. 처리 중인 작업은 끝까지 간다."""
    global _running
    _running = False


def process_one(queue: TaskQueue, ai: AiClient, lease: Lease) -> None:
    """빌려 온 작업 하나를 세 단계로 처리한다. 트랜잭션은 단계마다 따로 연다."""
    task = lease.task
    job = get_job(task.type)

    with queue.read() as db:
        ctx = job.load(db, task)

    if isinstance(ctx, Skip):
        with queue.complete(lease) as done:
            done.result = ctx.result
        return

    try:
        response = job.call_ai(ctx, ai)
    except Exception as exc:
        if job.on_ai_error is None:
            raise
        with queue.complete(lease) as done:
            done.result = job.on_ai_error(done.db, task, ctx, exc)
        return

    with queue.complete(lease) as done:
        done.result = job.apply(done.db, task, ctx, response)


def run(queue: TaskQueue, ai: AiClient, settings: QueueSettings | None = None) -> None:
    settings = settings or QueueSettings()
    logger.info("워커 시작. 큐를 폴링한다.")

    while _running:
        try:
            lease = queue.claim()
        except Exception:
            # DB 다운 · task_queue 테이블 부재 · 커넥션 풀 고갈. 안 쉬면 이 실패만 CPU 를
            # 태우며 타이트 루프로 돈다.
            logger.exception("작업을 집지 못했다. 잠시 뒤 다시 시도한다.")
            time.sleep(settings.QUEUE_POLL_INTERVAL_SEC)
            continue

        if lease is None:
            # 롱 폴링이 없으니 직접 쉰다. claim 은 이미 커넥션을 돌려줬다.
            time.sleep(settings.QUEUE_POLL_INTERVAL_SEC)
            continue

        try:
            process_one(queue, ai, lease)
        except LeaseLostError:
            logger.warning(
                "lease 를 잃었다 type=%s — 다른 워커가 회수해 맡았다. 이번 결과는 버린다.",
                lease.task.type,
            )
            continue
        except (KeyboardInterrupt, SystemExit):
            queue.release(lease)
            raise
        except Exception as exc:
            # 포즈 정보가 로그에 남지 않도록 본문은 찍지 않는다(규칙 6).
            logger.exception("작업 처리 실패 type=%s. 재시도에 맡긴다.", lease.task.type)
            queue.fail(lease, exc)
            continue

        # DONE 커밋이 끝난 뒤에 찍어야 로그와 사실이 맞는다.
        logger.info("작업 완료 type=%s", lease.task.type)

    logger.info("워커 종료.")
