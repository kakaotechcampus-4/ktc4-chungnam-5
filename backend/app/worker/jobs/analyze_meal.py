"""`meal.analyze` — 사진·텍스트에서 음식을 인식해 `meal_items` 에 저장한다.

`ANALYZING → REVIEW_REQUIRED`. 사용자가 인식 결과를 확인하면 거기서 채점으로 이어진다.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `AnalyzeMealRequest` · `AnalyzeMealResponse` 다.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask

logger = logging.getLogger("worker.analyze_meal")


def run(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None:
    """
    실패한 시도의 도메인 변경은 큐 트랜잭션과 함께 롤백되므로, 재시도할 때 이전
    시도의 흔적은 없다. 그래도 `meals.status` 가 `ANALYZING` 이 아니면 넘어간다 —
    같은 작업이 두 번 등록되는 경우(사용자 더블 탭 등)는 큐 구현과 무관하게 남는다.

    구현 순서:
      1. `meals` 를 읽는다. 행이 없으면 raise — 재시도해도 생기지 않는다
      2. `deleted_at` 이 NULL 이 아니면 return. **meals 는 soft delete 라 삭제된
         식사도 행은 그대로 있다** — 이걸 빠뜨리면 사용자가 지운 식사를 분석해서
         `meal_items` 를 채운다 (`crud/__init__.py` 의 soft delete 절 참고)
      3. `status` 가 ANALYZING 이 아니면 return
      4. AI 호출
      5. `candidateFoodRefId` 가 `food_refs` 에 실재하는지 확인한다.
         없는 FK 를 그대로 넣으면 커밋이 통째로 깨진다
      6. 이 식사의 기존 모델 생성 항목을 지우고 새로 넣는다(두 번 등록된 경우 대비)
      7. `unit` 을 g 으로 환산한다 — `app.services.meal.to_grams`.
         환산이 안 되면 None 을 넣고 원본 단위는 `raw_ai_result` 에 남긴다
      8. `status` 를 REVIEW_REQUIRED 로 옮긴다
    """
    body = task.payload
    meal_id = body["mealId"]

    # TODO: meals 조회. 없으면 raise ValueError(f"식사를 찾을 수 없다: {meal_id}")
    # TODO: deleted_at 이 NULL 이 아니면 return  ← soft delete 된 식사는 분석하지 않는다
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

    # 커밋하지 않는다. 이 세션은 큐가 작업을 잠근 트랜잭션이고, 큐가 DONE 과 함께
    # 한 번에 커밋한다. 여기서 터지면 도메인 변경까지 통째로 롤백된다.

    # 포즈 정보는 민감 건강정보다. 음식명·이미지 키를 로그에 남기지 않는다(규칙 6).
    logger.info(
        "분석 완료 mealId=%s items=%d safetyStatus=%s",
        meal_id,
        len(result["items"]),
        result["safetyStatus"],
    )

    # 구현이 끝나면 이 줄을 지우고 dispatch._HANDLERS 에 등록한다.
    # 먼저 지우면 loop.py 가 성공으로 보고 DONE 을 커밋한다 — 작업이 조용히 사라진다.
    raise NotImplementedError("meal.analyze 미구현")
