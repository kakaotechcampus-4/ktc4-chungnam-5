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
    찾았고 섭취량을 g 으로 옮길 수 있으면 `matched: true` 와 환산한 영양성분이
    함께 나간다. 음식은 찾았지만 환산이 안 되면("2개") `matched: false` 다 —
    계약서가 `matched` 를 "영양정보 유무"로 정의하기 때문이다. 그때도 DB 의
    `food_ref_id` 링크는 남아, 나중에 g 으로 고치면 곧바로 환산된다.

    없는 식사 · 남의 식사 · 삭제된 식사는 전부 404 로 같게 응답한다
    (`DELETE /meals/{mealId}` 와 같은 이유 — 소유권 누출 방지).

    여기서 조합한다: g 환산 → 공공 DB 매칭 → 저장 → 큐 적재. `services/` 끼리는
    서로 부르지 않으므로(README 절대 규칙 5) 순서를 아는 건 이 레이어뿐이다.

    응답의 `status` · `isRecalculation` 은 API 명세서(`contracts/API.md` POST
    /meals/{mealId}/items)를 그대로 따른다 — 항목 하나가 아니라 **식사 전체**의
    상태다. 확인 화면에서는 음식을 여러 번 고친 뒤 "확인" 을 누르므로, 재분석을
    기다리는 동안에도 편집은 계속 허용된다(`services.meal._is_editable`).

    ## 지금 이 API 를 쓰면 식사가 ANALYZING 에 갇힌다

    `worker/jobs/analyze_meal.py` 가 아직 미구현이라 `dispatch.handle` 이
    `NotImplementedError` 를 던진다 → 루프가 메시지를 지우지 않는다 → 재배달을
    거쳐 **DLQ 로 간다.** 그 사이 식사는 `ANALYZING` 에 머문다. 음식을 더 고치는
    것과 삭제는 되지만 확정은 못 한다. 워커가 붙으면 이 엔드포인트는 수정 없이
    정상 동작한다. 배포 전에 워커가 먼저 병합돼야 한다.

    ## 미해결 — 워커 작업에서 정할 것

    수정 한 번에 재분석 작업 하나가 쌓인다. 음식 3개를 고치면 같은 식사에 대한
    `meal.analyze` 가 3건이다. SQS 는 어차피 at-least-once 라 워커가 중복 배달을
    견뎌야 하지만(`jobs/analyze_meal.py` 독스트링), 여기서 나가는 건 재배달이
    아니라 **서로 다른 진짜 작업**이라 "이미 처리함" 으로 넘길 수 없다.

    더불어 `meal.analyze` 는 사진에서 음식을 **다시 인식**하는 작업이다. 사용자가
    방금 이름을 직접 알려줬는데 같은 사진으로 AI 를 또 부르는 셈이라, 사용자가
    넣은 음식을 AI 가 또 인식해 중복될 수 있다. 재인식을 건너뛰는 작업 타입이
    필요한지 워커 작업에서 판단한다.
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
