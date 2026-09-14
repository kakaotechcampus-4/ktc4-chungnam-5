r"""큐 상태 보기.

    cd backend
    .\.venv\Scripts\Activate.ps1
    python -m scripts.queue_status

ElasticMQ 에는 웹 UI 가 없다. 대신 GetQueueAttributes 로 메시지 수를 읽어 온다.
"""

from __future__ import annotations

import boto3

from app.infra.queue import QueueSettings

_ATTRS = {
    "ApproximateNumberOfMessages": "대기",
    "ApproximateNumberOfMessagesNotVisible": "처리중",
    "ApproximateNumberOfMessagesDelayed": "지연",
}


def main() -> None:
    settings = QueueSettings()
    client = boto3.client(
        "sqs",
        endpoint_url=settings.SQS_ENDPOINT_URL or None,
        region_name=settings.AWS_DEFAULT_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )

    base = settings.SQS_QUEUE_URL.rsplit("/", 1)[0]
    names = ["glp1-tasks", "glp1-tasks-dlq", "glp1-tasks-test", "glp1-tasks-test-dlq"]

    print(f"{'큐':<22} {'대기':>6} {'처리중':>7} {'지연':>6}")
    print("-" * 45)
    for name in names:
        try:
            attrs = client.get_queue_attributes(
                QueueUrl=f"{base}/{name}",
                AttributeNames=list(_ATTRS),
            )["Attributes"]
        except Exception as exc:
            print(f"{name:<22} 조회 실패 ({type(exc).__name__})")
            continue

        counts = [attrs.get(key, "0") for key in _ATTRS]
        print(f"{name:<22} {counts[0]:>6} {counts[1]:>7} {counts[2]:>6}")

    print("\n대기 = 꺼낼 수 있는 것 · 처리중 = 누가 꺼내갔고 아직 안 지운 것")
    print("DLQ 에 쌓인 게 있으면 3번 실패한 작업이다.")


if __name__ == "__main__":
    main()
