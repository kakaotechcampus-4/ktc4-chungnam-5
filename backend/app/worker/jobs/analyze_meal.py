"""`meal.analyze` — 사진·텍스트에서 음식을 인식해 `meal_items` 에 저장한다.

`ANALYZING → REVIEW_REQUIRED`. 사용자가 인식 결과를 확인하면 거기서 채점으로 이어진다.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `AnalyzeMealRequest` · `AnalyzeMealResponse` 다.
"""

from __future__ import annotations

import logging

from app.db.session import SessionLocal
from app.infra.ai import AiClient
from app.infra.queue import ReceivedTask

logger = logging.getLogger("worker.analyze_meal")


def run(task: ReceivedTask, ai: AiClient) -> None:
    """
    SQS 는 at-least-once 라 같은 메시지가 두 번 올 수 있다. `meals.status` 가
    `ANALYZING` 이 아니면 이미 처리된 것으로 보고 넘어간다 — 그러지 않으면
    재배달마다 `meal_items` 가 중복으로 쌓인다.

    구현 순서:
      1. `meals` 를 읽는다. 없으면 raise — 재시도해도 생기지 않는다(삭제된 식사)
      2. `status` 가 ANALYZING 이 아니면 return
      3. AI 호출
      4. `candidateFoodRefId` 가 `food_refs` 에 실재하는지 확인한다.
         없는 FK 를 그대로 넣으면 커밋이 통째로 깨진다
      5. 이 식사의 기존 모델 생성 항목을 지우고 새로 넣는다(재배달 대비)
      6. `unit` 을 g 으로 환산한다 — `app.services.meal.to_grams`.
         환산이 안 되면 None 을 넣고 원본 단위는 `raw_ai_result` 에 남긴다
      7. `status` 를 REVIEW_REQUIRED 로 옮긴다
    """
    body = task.body
    meal_id = body["mealId"]

    with SessionLocal() as db:
        # TODO: meals 조회. 없으면 raise ValueError(f"식사를 찾을 수 없다: {meal_id}")
        # TODO: status 가 ANALYZING 이 아니면 return  ← 멱등성. 지우지 말 것

        result = ai.analyze_meal(
            {
                "mealId": meal_id,
                "mealType": body["mealType"],
                "eatenAt": body["eatenAt"],
                "stage": body["stage"],
                "imageUrl": body.get("imageUrl"),  # presigned URL. AI 는 S3 권한이 없다
                "rawText": body.get("rawText"),
            }
        )

        # TODO: result["items"] 의 candidateFoodRefId 중 food_refs 에 실재하는 것만 추린다
        # TODO: 이 식사의 기존 모델 생성 meal_items 를 지운다
        # TODO: to_grams 로 환산해 meal_items 에 넣는다
        # TODO: meals.status 를 REVIEW_REQUIRED 로

        db.commit()  # 작업 하나가 트랜잭션 하나다. 중간에 터지면 전부 롤백된다

    # 포즈 정보는 민감 건강정보다. 음식명·이미지 키를 로그에 남기지 않는다(규칙 6).
    logger.info(
        "분석 완료 mealId=%s items=%d safetyStatus=%s",
        meal_id,
        len(result["items"]),
        result["safetyStatus"],
    )

    # 구현이 끝나면 이 줄을 지우고 dispatch._HANDLERS 에 등록한다.
    # 먼저 지우면 loop.py 가 성공으로 보고 메시지를 큐에서 지운다 — 작업이 조용히 사라진다.
    raise NotImplementedError("meal.analyze 미구현")
