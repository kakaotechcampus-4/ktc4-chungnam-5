"""식사 구성 음식(meal item) 수정 API.

세 동작(PATCH · POST · DELETE)이 모두 재계산을 유발한다 — 식사를 `ANALYZING` 으로
되돌리고 Worker 에 재분석을 맡긴다. 지금은 POST 만 구현돼 있다.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, get_task_queue
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.infra.queue import TaskQueue
from app.schemas.meal import MealItemCreateRequest, MealItemCreateResponse
from app.services import meal as meal_service
from app.services import nutrition as nutrition_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/meals/{meal_id}/items",
    status_code=201,
    response_model=ApiResponse[MealItemCreateResponse],
    responses=error_responses(401, 404, 409, 422),
)
def add_meal_item(
    meal_id: uuid.UUID,
    request: MealItemCreateRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
    queue: TaskQueue = Depends(get_task_queue),
) -> ApiResponse[MealItemCreateResponse]:
    """사용자가 빠진 음식을 직접 더한다.

    AI 가 준 후보(`candidateFoodRefId`)가 없으므로 공공 DB 를 이름으로 찾는다.
    찾으면 `matched: true` 와 섭취량만큼 환산한 영양성분이 함께 나간다.

    없는 식사 · 남의 식사 · 삭제된 식사는 전부 404 로 같게 응답한다
    (`DELETE /meals/{mealId}` 와 같은 이유 — 소유권 누출 방지).

    여기서 조합한다: g 환산 → 공공 DB 매칭 → 저장 → 큐 적재. `services/` 끼리는
    서로 부르지 않으므로(README 절대 규칙 5) 순서를 아는 건 이 레이어뿐이다.

    ## 지금 이 API 를 쓰면 식사가 ANALYZING 에 갇힌다

    `worker/jobs/analyze_meal.py` 가 아직 미구현이라 `dispatch.handle` 이
    `NotImplementedError` 를 던진다 → 루프가 메시지를 지우지 않는다 → 재배달을
    거쳐 **DLQ 로 간다.** 그 사이 식사는 `ANALYZING` 이고, 이 엔드포인트의 상태
    가드가 `ANALYZING` 을 막으므로 사용자는 더 고칠 수도 확정할 수도 없다
    (삭제만 가능). 워커가 붙으면 이 엔드포인트는 수정 없이 정상 동작한다.
    배포 전에 워커가 먼저 병합돼야 한다.
    """
    amount_g = meal_service.to_grams(request.amount, request.unit)
    match = nutrition_service.resolve_by_name(
        db, name=request.display_name, amount_g=amount_g
    )

    try:
        outcome = meal_service.add_item(
            db,
            user_id=user_id,
            meal_id=meal_id,
            request=request,
            amount_g=amount_g,
            food_ref_id=match.food_ref_id if match is not None else None,
            nutrition=match.nutrition if match is not None else None,
        )
    except meal_service.MealNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except meal_service.MealNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # 커밋이 먼저다 — `add_item` 안에서 이미 끝났다.
    try:
        queue.send(outcome.analyze_task)
    except Exception:
        # 데이터는 정합하지만 **사용자는 막다른 길에 놓인다**: 항목은 저장됐고
        # 식사는 ANALYZING 인데 워커에게 갈 메시지가 없어 영원히 분석 중이다.
        # 상태 가드 때문에 재시도해도 409 라 스스로 복구할 수 없다(중복 항목이
        # 생기지는 않는다 — 가드가 두 번째 요청을 막는다).
        # 그래서 조용히 삼키지 않고 500 으로 올린다. 운영이 mealId 로 찾아
        # 수동 재적재할 수 있도록 로그를 남긴다.
        # TODO: outbox 테이블 또는 "ANALYZING 이 N분 이상 지속된 식사" 재적재
        #       스윕 잡으로 자동 복구. 워커 작업과 함께 정한다.
        # 음식명·이미지 키는 남기지 않는다 — 민감 건강정보다(README 절대 규칙 6).
        logger.exception("재분석 작업 적재 실패 — 수동 재적재 필요 mealId=%s", meal_id)
        raise

    return ok(outcome.response)
