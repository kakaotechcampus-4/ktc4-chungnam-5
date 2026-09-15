"""`feedback.daily` — 하루치 끼니를 모아 일일 피드백을 만든다.

`/short-feedback` `scope=DAILY` → `daily_feedbacks` + `daily_feedback_sources`.

**`feedback.meal` 과 타입을 묶지 않는다.** AI 는 같은 엔드포인트를 쓰지만(하는 일이
"Q/Q/S 를 짧은 문장으로 옮긴다" 로 같다) 모으는 데이터도 쓰는 테이블도 다르다.
묶으면 DLQ 에서 "feedback 10건 실패" 밖에 안 보인다.

들어오는 경로가 둘이다 — `POST /insights/daily/refresh` 가 큐에 넣거나,
Worker 의 스케줄 배치가 직접 시작한다. 어느 쪽이든 이 함수가 같은 일을 한다.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `ShortFeedbackRequest` · `ShortFeedbackResponse` 다.
"""

from __future__ import annotations

import logging

from app.db.session import SessionLocal
from app.infra.ai import AiClient
from app.infra.queue import ReceivedTask

logger = logging.getLogger("worker.feedback_daily")


def run(task: ReceivedTask, ai: AiClient) -> None:
    """
    `daily_feedbacks` 는 `(user_id, feedback_date)` UNIQUE 다. **upsert** 로 쓴다.
    근거 링크(`daily_feedback_sources`)도 다시 만들기 전에 기존 것을 지운다 —
    안 지우면 재배달마다 같은 근거가 쌓인다.

    구현 순서:
      1. 그날의 `meal_feedbacks` 를 모은다. 하나도 없으면 만들 게 없다 — return
      2. 끼니별 요약과 Q/Q/S 로 `meals` 목록을 만든다
      3. 하루 전체 Q/Q/S 를 집계한다 (개별 끼니 점수의 집계 — 새로 채점하지 않는다)
      4. AI 호출
      5. `daily_feedbacks` upsert + `daily_feedback_sources` 재생성.
         근거는 이 피드백이 어느 끼니에서 나왔는지 되짚는 링크다
    """
    body = task.body
    user_id = body["userId"]
    date = body["date"]

    with SessionLocal() as db:
        # TODO: 그날의 meal_feedbacks + qqs_evaluations 조회. 비어 있으면 return
        # TODO: 끼니별 요약 목록 구성
        # TODO: 하루 Q/Q/S 집계
        # TODO: users 에서 현재 stage

        result = ai.short_feedback(
            {
                "scope": "DAILY",
                "userId": user_id,
                "stage": ...,  # TODO
                "qqs": ...,  # TODO: 하루 집계 {"quantity": .., "quality": .., "satiety": ..}
                "date": date,
                # TODO: [{"mealType": "LUNCH", "summary": str,
                #         "qqs": {"quantity": .., "quality": .., "satiety": ..}}]
                "meals": [],
            }
        )

        # TODO: daily_feedbacks 에 upsert (scope=DAILY 면 suggestions 는 null 이다)
        # TODO: 기존 daily_feedback_sources 를 지우고 이번 근거로 다시 만든다

        db.commit()

    logger.info(
        "일일 피드백 완료 userId=%s date=%s safetyStatus=%s",
        user_id,
        date,
        result["safetyStatus"],
    )

    # 구현이 끝나면 이 줄을 지우고 dispatch._HANDLERS 에 등록한다.
    raise NotImplementedError("feedback.daily 미구현")
