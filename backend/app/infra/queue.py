"""비동기 작업 큐.

D13 — `infra/` 는 `Protocol` 뒤에 구현을 숨긴다. 도메인은 어느 구현이 붙는지 모른다.

로컬 개발에서도 **SQS 구현을 그대로 쓴다.** ElasticMQ(SQS API 호환 서버)를 컨테이너로
띄우고 `SQS_ENDPOINT_URL` 로 가리키면 된다. 별도의 LocalQueue 를 두지 않는 이유는,
visibility timeout · 재시도 · DLQ 재배치를 흉내 낸 구현은 결국 진짜와 어긋나기 때문이다.
로컬에서 통과한 재시도 로직이 배포 후에 다르게 도는 것보다, 같은 구현을 쓰는 편이 낫다.

프로덕션에서는 `SQS_ENDPOINT_URL` 을 비운다. boto3 가 실제 AWS 로 붙고
인증은 인스턴스 역할이 한다 — 액세스 키를 두지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class ReceivedTask:
    body: dict[str, Any]
    receipt: str
    # 이 메시지가 배달된 횟수. maxReceiveCount 를 넘기면 큐가 DLQ 로 옮긴다.
    receive_count: int


class TaskQueue(Protocol):
    """작업 큐. 도메인은 이 모양만 안다."""

    def send(self, body: dict[str, Any]) -> None: ...

    def receive(self, max_count: int = 1, wait_seconds: int = 5) -> list[ReceivedTask]: ...

    def delete(self, receipt: str) -> None: ...


class QueueSettings(BaseSettings):
    """큐 설정.

    `core.Settings` 에 넣지 않고 여기 둔 건, 이 어댑터가 전역 설정을 import 하지 않게
    하려는 것이다. 쓰는 쪽이 만들어서 넘긴다.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    QUEUE_TYPE: str = "sqs"
    SQS_QUEUE_URL: str = ""
    SQS_DLQ_URL: str = ""
    # 로컬 ElasticMQ 주소. 프로덕션에서는 비운다 (실제 AWS 엔드포인트를 쓴다).
    SQS_ENDPOINT_URL: str = ""
    AWS_DEFAULT_REGION: str = "ap-northeast-2"


class SqsQueue:
    """SQS(및 API 호환 서버) 구현."""

    def __init__(
        self,
        queue_url: str,
        *,
        endpoint_url: str | None = None,
        region_name: str = "ap-northeast-2",
    ) -> None:
        self._url = queue_url
        self._client = boto3.client(
            "sqs",
            # 빈 문자열이면 None 으로 넘겨야 boto3 가 실제 AWS 엔드포인트를 쓴다
            endpoint_url=endpoint_url or None,
            region_name=region_name,
        )

    def send(self, body: dict[str, Any]) -> None:
        self._client.send_message(
            QueueUrl=self._url,
            MessageBody=json.dumps(body, ensure_ascii=False),
        )

    def receive(self, max_count: int = 1, wait_seconds: int = 5) -> list[ReceivedTask]:
        response = self._client.receive_message(
            QueueUrl=self._url,
            MaxNumberOfMessages=max_count,
            WaitTimeSeconds=wait_seconds,
            MessageAttributeNames=["All"],
            AttributeNames=["ApproximateReceiveCount"],
        )
        return [
            ReceivedTask(
                body=json.loads(message["Body"]),
                receipt=message["ReceiptHandle"],
                receive_count=int(message["Attributes"]["ApproximateReceiveCount"]),
            )
            for message in response.get("Messages", [])
        ]

    def delete(self, receipt: str) -> None:
        """처리에 **성공했을 때만** 부른다.

        실패했는데 지우면 재시도도 DLQ 도 일어나지 않는다.
        지우지 않으면 visibility timeout 이 지난 뒤 다시 배달된다.
        """
        self._client.delete_message(QueueUrl=self._url, ReceiptHandle=receipt)


def build_task_queue(settings: QueueSettings | None = None, *, dlq: bool = False) -> TaskQueue:
    settings = settings or QueueSettings()

    if settings.QUEUE_TYPE != "sqs":
        raise NotImplementedError(
            f"QUEUE_TYPE={settings.QUEUE_TYPE} 은 구현돼 있지 않다. "
            "로컬에서도 sqs 를 쓴다 — infra/docker-compose.queue.yml 로 ElasticMQ 를 띄울 것."
        )

    url = settings.SQS_DLQ_URL if dlq else settings.SQS_QUEUE_URL
    if not url:
        name = "SQS_DLQ_URL" if dlq else "SQS_QUEUE_URL"
        raise ValueError(f"{name} 이 비어 있다.")

    return SqsQueue(
        url,
        endpoint_url=settings.SQS_ENDPOINT_URL,
        region_name=settings.AWS_DEFAULT_REGION,
    )
