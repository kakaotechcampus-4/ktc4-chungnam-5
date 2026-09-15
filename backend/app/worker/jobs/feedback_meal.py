"""`feedback.meal` — 끼니 하나에 대한 피드백 문장을 만든다.

`/short-feedback` `scope=MEAL` → `meal_feedbacks`.

**채점(Q/Q/S)은 이미 끝나 있다.** Rule Engine 이 확인 API 에서 동기로 돌려
`qqs_evaluations` 에 저장한 뒤다. 여기서는 그 점수를 문장으로 옮기기만 한다.
그래서 AI 가 죽어도 점수는 남는다.

이 작업은 사용자가 "다음 끼니 제안 보기" 를 눌렀을 때 그 API 가 큐에 넣는다.
채점에 자동으로 이어 붙이지 않는다 — 아무도 안 볼 피드백까지 AI 를 부르게 된다.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `ShortFeedbackRequest` · `ShortFeedbackResponse` 다.
"""

from __future__ import annotations

import logging

from app.db.session import SessionLocal
from app.infra.ai import AiClient
from app.infra.queue import ReceivedTask

logger = logging.getLogger("worker.feedback_meal")


def run(task: ReceivedTask, ai: AiClient) -> None:
    """
    `meal_feedbacks` 는 `(meal_id)` UNIQUE 다. create 가 아니라 **upsert** 로 쓴다 —
    재배달되면 두 번째 INSERT 가 제약에 걸려 커밋이 깨진다.

    구현 순서:
      1. `qqs_evaluations` 에서 이 식사의 Q/Q/S 를 읽는다.
         없으면 채점이 아직이다 — raise 해서 재배달에 맡긴다
      2. `meal_items` + `food_refs` 로 `items` 를 만든다.
         성분은 BE 가 채운다 — AI 는 음식을 지목만 했다
      3. `satiety_logs` 로 포만감 컨텍스트를 만든다 (없으면 None)
      4. AI 호출
      5. `meal_feedbacks` 에 upsert. `suggestions` 의 `candidateFoodRefId` 는
         `food_refs` 에 실재하는 것만 남긴다
    """
    body = task.body
    meal_id = body["mealId"]

    with SessionLocal() as db:
        # TODO: qqs_evaluations 에서 Q/Q/S 조회. 없으면 raise (채점이 먼저다)
        # TODO: meal_items + food_refs → items 목록
        # TODO: satiety_logs → 포만감 컨텍스트
        # TODO: users 에서 현재 stage

        result = ai.short_feedback(
            {
                "scope": "MEAL",
                "userId": ...,  # TODO
                "stage": ...,  # TODO: PRE_DOSE | INITIAL | TITRATION | MAINTENANCE
                "qqs": ...,  # TODO: {"quantity": float, "quality": float, "satiety": float}
                "mealId": meal_id,
                # TODO: [{"displayName": str, "amount": float, "unit": str,
                #         "nutrition": {"kcal": .., "proteinG": .., "carbG": ..}}]
                "items": [],
                # TODO: {"beforePct": int, "afterPct": int, "checkins": [...],
                #        "hungerReturnMinutes": int, "userComment": str}
                "satiety": None,
            }
        )

        # TODO: meal_feedbacks 에 upsert — body · modelVersion · safetyStatus · suggestions
        # TODO: suggestions 의 candidateFoodRefId 는 실재 확인 후에만 넣는다 (FK)

        db.commit()

    # 음식명·제안 문구를 로그에 남기지 않는다(규칙 6).
    logger.info(
        "끼니 피드백 완료 mealId=%s safetyStatus=%s",
        meal_id,
        result["safetyStatus"],
    )

    # 구현이 끝나면 이 줄을 지우고 dispatch._HANDLERS 에 등록한다.
    raise NotImplementedError("feedback.meal 미구현")
