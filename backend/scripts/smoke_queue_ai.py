r"""큐 ↔ ai-stub 왕복 확인.

PowerShell 창 셋으로 띄운다:

    docker compose -f infra\docker-compose.queue.yml up -d       # ElasticMQ

    cd ai-stub
    .\.venv\Scripts\Activate.ps1
    uvicorn main:app --port 8001                                 # 스텁

    cd backend
    .\.venv\Scripts\Activate.ps1
    python -m scripts.smoke_queue_ai

정상 경로와 실패 경로를 한 번씩 돌린다. 실패 경로가 핵심이다 —
AI 가 죽었을 때 메시지가 지워지지 않고, 재배달되고, 3회를 넘기면 DLQ 로 빠지는지.
"""

from __future__ import annotations

import socket
import sys
import time
import uuid
from typing import Any

import boto3

from app.infra.ai import AiSettings, build_ai_client
from app.infra.queue import QueueSettings, build_task_queue
from app.worker_main import handle


def _check(label: str, host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"  {label} OK ({host}:{port})")
    except OSError:
        sys.exit(f"  {label} 에 붙지 못했다 ({host}:{port}). 띄우고 다시 실행할 것.")


def _raw_client(settings: QueueSettings):
    return boto3.client(
        "sqs",
        endpoint_url=settings.SQS_ENDPOINT_URL or None,
        region_name=settings.AWS_DEFAULT_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def _purge(client, *urls: str) -> None:
    """큐를 통째로 비운다.

    receive + delete 로는 부족하다 — 앞선 실행이 남긴 메시지가 visibility timeout
    동안 보이지 않아 그때는 못 지우고, 나중에 되살아나 확인을 방해한다.
    """
    for url in urls:
        try:
            client.purge_queue(QueueUrl=url)
        except Exception as exc:  # 연속 purge 는 쿨다운에 걸릴 수 있다
            print(f"  purge 건너뜀 ({type(exc).__name__})")


def _task(marker: str) -> dict[str, Any]:
    return {
        "type": "meal.analyze",
        "mealId": marker,
        "mealType": "LUNCH",
        "eatenAt": "2026-08-21T12:40:00+09:00",
        "stage": "MAINTENANCE",
        "rawText": "김밥 한 줄",
    }


def _one_pass(queue, ai):
    """작업 하나를 꺼내 처리한다. 성공하면 지운다.

    처리한 작업을 돌려준다 — 실패한 메시지는 visibility timeout 동안 보이지 않으므로,
    점유를 풀려면 다시 receive 하는 게 아니라 이 receipt 를 써야 한다.
    """
    batch = queue.receive(max_count=1, wait_seconds=5)
    if not batch:
        print("    꺼낼 작업이 없다")
        return None, False

    task = batch[0]
    try:
        handle(task, ai)
    except Exception as exc:
        print(f"    배달 {task.receive_count}회째 — 실패: {type(exc).__name__}. 지우지 않는다")
        return task, False

    queue.delete(task.receipt)
    print(f"    배달 {task.receive_count}회째 — 성공. 메시지 삭제")
    return task, True


def main() -> None:
    queue_settings = QueueSettings()
    print("전제 조건")
    _check("ElasticMQ", "localhost", 9324)
    _check("ai-stub", "localhost", 8001)

    queue = build_task_queue(queue_settings)
    dlq = build_task_queue(queue_settings, dlq=True)
    client = _raw_client(queue_settings)

    _purge(client, queue_settings.SQS_QUEUE_URL, queue_settings.SQS_DLQ_URL)

    # ── 정상 경로 ────────────────────────────────────────────
    print("\n[1] 정상 경로")
    marker = f"smoke-ok-{uuid.uuid4().hex[:6]}"
    queue.send(_task(marker))
    print(f"    큐에 작업 투입 mealId={marker}")

    ai = build_ai_client(AiSettings())
    _, ok = _one_pass(queue, ai)
    assert ok, "정상 경로가 실패했다"
    left = [t.body.get("mealId") for t in queue.receive(max_count=10, wait_seconds=1)]
    assert marker not in left, f"처리했는데 큐에 남아 있다: {marker}"
    print("    큐에서 사라짐 ✓")

    # ── 실패 경로 ────────────────────────────────────────────
    print("\n[2] 실패 경로 — ai-stub 이 500 을 낸다 (X-Stub-Scenario: ERROR_500)")
    marker = f"smoke-fail-{uuid.uuid4().hex[:6]}"
    queue.send(_task(marker))
    print(f"    큐에 작업 투입 mealId={marker}")

    failing_ai = build_ai_client(AiSettings(AI_STUB_SCENARIO="ERROR_500"))
    for _ in range(3):
        task, _ok = _one_pass(queue, failing_ai)
        if task is None:
            break
        # visibility timeout 60초를 그대로 기다릴 수 없어 재배달을 앞당긴다.
        # 실제 운영에서는 이 줄이 없고 큐가 알아서 60초 뒤 재배달한다.
        client.change_message_visibility(
            QueueUrl=queue_settings.SQS_QUEUE_URL,
            ReceiptHandle=task.receipt,
            VisibilityTimeout=0,
        )

    # 큐가 통째로 비었는지는 보지 않는다. 앞선 실행이 남긴 메시지가 점유 중이면
    # 시작할 때 비우지 못하고, 나중에 되살아나 이 확인을 방해한다. 마커로만 판단한다.
    print("    4번째 수신 시도 — 큐가 DLQ 로 옮긴다")
    still_here = [t.body.get("mealId") for t in queue.receive(max_count=10, wait_seconds=2)]
    assert marker not in still_here, f"메인 큐에 아직 남아 있다: {marker}"

    deadline = time.time() + 10
    found = False
    while time.time() < deadline and not found:
        for task in dlq.receive(max_count=10, wait_seconds=1):
            if task.body.get("mealId") == marker:
                found = True
            dlq.delete(task.receipt)

    assert found, f"DLQ 에 도착하지 않았다: {marker}"
    print(f"    DLQ 도착 ✓ mealId={marker}")

    print("\n왕복 확인 완료.")


if __name__ == "__main__":
    main()
