"""`feedback.long` — 여러 날을 모아 장기 추세 피드백을 만든다.

`/long-feedback` → `long_term_feedbacks` + `long_term_feedback_sources`.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `LongFeedbackRequest` · `LongFeedbackResponse` 다.
응답에 `chartData` 는 없다 — 차트는 BE 가 `qqs_evaluations` 를 집계해 채운다.
AI 는 문장만 쓴다.

들어오는 경로가 둘이다 — `POST /insights/long-term/refresh` 또는 스케줄 배치.
"""

from __future__ import annotations

import logging

from app.db.session import SessionLocal
from app.infra.ai import AiClient
from app.infra.queue import ReceivedTask

logger = logging.getLogger("worker.feedback_long")


def run(task: ReceivedTask, ai: AiClient) -> None:
    """
    `long_term_feedbacks` 는 `(user_id, period_type, period_start)` UNIQUE 다.
    **upsert** 로 쓰고, 근거 링크도 다시 만들기 전에 기존 것을 지운다.

    `periodType` 주의: 공개 API 의 `?period=all` 은 `ALL` 로 오지만 DB ENUM 에는
    아직 `ALL` 이 없다. 저장 전에 어떻게 다룰지 정해야 한다.

    구현 순서:
      1. 기간 안의 `qqs_evaluations` 를 날짜별로 모아 시계열을 만든다
      2. 같은 기간 `daily_feedbacks` 의 본문을 모은다
      3. 데이터가 너무 적으면(예: 하루치) 추세를 말할 수 없다 — 기준을 정해 return
      4. AI 호출
      5. `long_term_feedbacks` upsert + `long_term_feedback_sources` 재생성
    """
    body = task.body
    user_id = body["userId"]
    period_type = body["periodType"]

    with SessionLocal() as db:
        # TODO: 기간 안 qqs_evaluations → 날짜별 시계열
        # TODO: 같은 기간 daily_feedbacks 본문 목록
        # TODO: 데이터가 부족하면 return (기준을 정할 것)
        # TODO: users 에서 현재 stage

        result = ai.long_feedback(
            {
                "userId": user_id,
                "periodType": period_type,  # WEEKLY | MONTHLY | ALL
                "periodStart": body["periodStart"],
                "periodEnd": body["periodEnd"],
                "stage": ...,  # TODO
                # TODO: [{"date": "2026-08-01", "quantity": .., "quality": .., "satiety": ..}]
                "series": [],
                "dailySummaries": [],  # TODO: daily_feedbacks 본문 목록
            }
        )

        # TODO: long_term_feedbacks 에 upsert — trendSummary · recommendation
        # TODO: 기존 long_term_feedback_sources 를 지우고 이번 근거로 다시 만든다

        db.commit()

    logger.info(
        "장기 피드백 완료 userId=%s periodType=%s safetyStatus=%s",
        user_id,
        period_type,
        result["safetyStatus"],
    )

    # 구현이 끝나면 이 줄을 지우고 dispatch._HANDLERS 에 등록한다.
    raise NotImplementedError("feedback.long 미구현")
