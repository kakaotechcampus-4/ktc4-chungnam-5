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
    """개발용 큐(glp1-tasks)가 아니라 **전용 테스트 큐**를 쓴다.

    Worker 컨테이너가 돌고 있으면 개발용 큐의 메시지를 먼저 채가서 테스트가 깨진다.
    테스트가 컨테이너 상태에 따라 통과했다 말았다 하면 안 되므로 큐를 분리한다.
    설정(visibility timeout · maxReceiveCount)은 개발용과 같게 맞춰 둔다.
    """
    base = f"http://{ENDPOINT_HOST}:{ENDPOINT_PORT}"
    return QueueSettings(
        QUEUE_TYPE="sqs",
        SQS_ENDPOINT_URL=base,
        SQS_QUEUE_URL=f"{base}/queue/glp1-tasks-test",
        SQS_DLQ_URL=f"{base}/queue/glp1-tasks-test-dlq",
    )


@pytest.fixture
def queue(settings):
    return build_task_queue(settings)


@pytest.fixture
def dlq(settings):
    return build_task_queue(settings, dlq=True)


def _raw_client(settings: QueueSettings):
    """점유를 즉시 푸는 데만 쓰는 저수준 클라이언트.

    `release` 를 TaskQueue 에 두지 않은 건 의도적이다. 실패할 때마다 즉시 반환하면
    재시도가 밀리초 단위로 일어나 maxReceiveCount 를 순식간에 태운다 —
    visibility timeout 이 곧 백오프다. 테스트에서만 앞당긴다.
    """
    import boto3

    return boto3.client(
        "sqs",
        endpoint_url=settings.SQS_ENDPOINT_URL or None,
        region_name=settings.AWS_DEFAULT_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


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
    client = _raw_client(settings)
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
    """maxReceiveCount = 3 — 3번 배달되고도 안 지우면 DLQ 로 간다.

    이동은 **다음 수신 시도**가 트리거한다. 3번 배달된 뒤 4번째로 꺼내려 할 때
    큐가 옮기고 메인 큐는 빈 결과를 준다. 타이머가 아니므로, 워커가 폴링을 멈추면
    메시지는 메인 큐에 그대로 남는다.
    """
    client = _raw_client(settings)
    marker = str(uuid.uuid4())
    queue.send({"type": "meal.analyze", "mealId": marker})

    deliveries = []
    for _ in range(5):  # 3회 배달 + 이동을 트리거할 1회. 넉넉히 잡는다
        batch = queue.receive(wait_seconds=2)
        if not batch:
            break
        deliveries.append(batch[0].receive_count)
        client.change_message_visibility(
            QueueUrl=settings.SQS_QUEUE_URL,
            ReceiptHandle=batch[0].receipt,
            VisibilityTimeout=0,
        )

    assert deliveries == [1, 2, 3], "maxReceiveCount 만큼만 배달돼야 한다"

    deadline = time.time() + 10
    dead: list = []
    while time.time() < deadline and not dead:
        dead = dlq.receive(max_count=10, wait_seconds=1)

    assert [task.body["mealId"] for task in dead] == [marker]
