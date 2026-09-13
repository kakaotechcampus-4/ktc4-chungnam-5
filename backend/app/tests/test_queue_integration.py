"""ElasticMQ 를 상대로 한 큐 통합 테스트.

컨테이너가 안 떠 있으면 통째로 건너뛴다:

    docker compose -f infra/docker-compose.queue.yml up -d

재시도·DLQ 는 흉내가 아니라 큐가 직접 하는 일이라, 이 테스트가 확인하는 건
우리 코드가 아니라 **설정값이 의도대로 먹는지**다. maxReceiveCount 를 잘못 적으면
배포 후에야 드러난다.
"""

from __future__ import annotations

import socket
import time
import uuid

import pytest

from app.infra.queue import QueueSettings, build_task_queue

ENDPOINT_HOST = "localhost"
ENDPOINT_PORT = 9324


def _elasticmq_is_up() -> bool:
    try:
        with socket.create_connection((ENDPOINT_HOST, ENDPOINT_PORT), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _elasticmq_is_up(),
    reason="ElasticMQ 가 떠 있지 않다. infra/docker-compose.queue.yml 로 띄울 것.",
)


@pytest.fixture
def settings() -> QueueSettings:
    base = f"http://{ENDPOINT_HOST}:{ENDPOINT_PORT}"
    return QueueSettings(
        QUEUE_TYPE="sqs",
        SQS_ENDPOINT_URL=base,
        SQS_QUEUE_URL=f"{base}/queue/glp1-tasks",
        SQS_DLQ_URL=f"{base}/queue/glp1-tasks-dlq",
    )


@pytest.fixture
def queue(settings):
    return build_task_queue(settings)


@pytest.fixture
def dlq(settings):
    return build_task_queue(settings, dlq=True)


def _drain(q) -> None:
    while batch := q.receive(max_count=10, wait_seconds=0):
        for task in batch:
            q.delete(task.receipt)


@pytest.fixture(autouse=True)
def _clean(queue, dlq):
    _drain(queue)
    _drain(dlq)
    yield
    _drain(queue)
    _drain(dlq)


def test_send_and_receive(queue):
    marker = str(uuid.uuid4())

    queue.send({"type": "meal.analyze", "mealId": marker})
    received = queue.receive(max_count=1, wait_seconds=5)

    assert len(received) == 1
    assert received[0].body == {"type": "meal.analyze", "mealId": marker}
    assert received[0].receive_count == 1

    queue.delete(received[0].receipt)


def test_deleted_message_does_not_come_back(queue):
    queue.send({"type": "meal.analyze", "mealId": str(uuid.uuid4())})

    first = queue.receive(wait_seconds=5)
    queue.delete(first[0].receipt)

    assert queue.receive(wait_seconds=1) == []


def test_receive_count_climbs_when_not_deleted(queue, settings):
    """지우지 않으면 visibility timeout 뒤 다시 배달되고 횟수가 오른다.

    conf 의 defaultVisibilityTimeout 이 60초라 그대로 기다릴 수 없어,
    점유를 즉시 풀어(visibility 0) 재배달을 앞당긴다.
    """
    import boto3

    client = boto3.client("sqs", endpoint_url=settings.SQS_ENDPOINT_URL, region_name="ap-northeast-2")
    queue.send({"type": "meal.analyze", "mealId": str(uuid.uuid4())})

    counts = []
    for _ in range(2):
        batch = queue.receive(wait_seconds=5)
        assert batch, "메시지가 재배달되지 않았다"
        counts.append(batch[0].receive_count)
        client.change_message_visibility(
            QueueUrl=settings.SQS_QUEUE_URL,
            ReceiptHandle=batch[0].receipt,
            VisibilityTimeout=0,
        )

    assert counts == [1, 2]


def test_message_lands_in_dlq_after_max_receive_count(queue, dlq, settings):
    """maxReceiveCount = 3 — 3번 배달되고도 안 지우면 DLQ 로 간다."""
    import boto3

    client = boto3.client("sqs", endpoint_url=settings.SQS_ENDPOINT_URL, region_name="ap-northeast-2")
    marker = str(uuid.uuid4())
    queue.send({"type": "meal.analyze", "mealId": marker})

    for _ in range(3):
        batch = queue.receive(wait_seconds=5)
        if not batch:
            break
        client.change_message_visibility(
            QueueUrl=settings.SQS_QUEUE_URL,
            ReceiptHandle=batch[0].receipt,
            VisibilityTimeout=0,
        )

    deadline = time.time() + 10
    dead = []
    while time.time() < deadline and not dead:
        dead = dlq.receive(max_count=10, wait_seconds=1)

    assert [task.body["mealId"] for task in dead] == [marker]
