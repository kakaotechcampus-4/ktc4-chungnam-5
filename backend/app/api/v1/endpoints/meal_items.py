"""식사 구성 음식(meal item) 수정 API.

세 동작(PATCH · POST · DELETE)이 모두 식사를 재계산 대기로 표시한다 — 점수가 더는
유효하지 않다는 뜻이다. 지금은 POST 만 구현돼 있다.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.meal import MealItemCreateRequest, MealItemCreateResponse
from app.services import meal as meal_service
from app.services import nutrition as nutrition_service

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
) -> ApiResponse[MealItemCreateResponse]:
    """사용자가 빠진 음식을 직접 더한다.

    AI 가 준 후보(`candidateFoodRefId`)가 없으므로 공공 DB 를 이름으로 찾는다.
    하나로 좁혀졌고 섭취량을 g 으로 옮길 수 있으면 `matched: true` 와 환산한
    영양성분이 함께 나간다. 좁혀지지 않았거나("김치찌개" 28건) 환산이 안 되면
    ("2개") `matched: false` 다 — API 명세서가 `matched` 를 "영양정보 유무" 로
    정의하기 때문이다. 그때도 DB 의 `food_ref_id` 링크는 남아, 나중에 g 으로
    고치면 곧바로 환산된다. 매칭 규칙은 `crud.food.find_unique_by_name` 참고.

    없는 식사 · 남의 식사 · 삭제된 식사는 전부 404 로 같게 응답한다
    (`DELETE /meals/{mealId}` 와 같은 이유 — 소유권 누출 방지).

    여기서 조합한다: g 환산 → 공공 DB 매칭 → 저장. `services/` 끼리는 서로 부르지
    않으므로(README 절대 규칙 5) 순서를 아는 건 이 레이어뿐이다.

    ## ANALYZING 은 "워커가 도는 중" 이 아니다

    응답의 `status: ANALYZING` · `isRecalculation: true` 는 API 명세서
    (`contracts/API.md` POST /meals/{mealId}/items)를 그대로 따른다. 항목 하나가
    아니라 **식사 전체**의 상태이며, 여기서는 **"점수가 아직 유효하지 않다"** 는
    뜻이다 — 백그라운드 작업이 도는 중이라는 뜻이 아니다.

    명세서의 비동기 규약은 `202 + 폴링` 이고 그 자리는 `POST /meals` 다(사진에서
    음식을 인식하는 진짜 AI 작업, `steps` 배열로 진행상황까지 준다). 이 엔드포인트는
    `201` 이라 폴링 대상이 아니다. 사용자는 확인 화면에 그대로 머물러 음식을 계속
    고치고, 다 되면 [확인] 을 누른다.

    그래서 여기서는 큐에 아무것도 넣지 않는다. 사용자가 음식명과 양을 직접
    알려줬으므로 AI 에게 물을 것이 없고, 다시 계산할 Q/Q/S 는 순수 함수라
    0.01 초면 끝난다(README 절대 규칙 2). 확인 화면은 [확인] 전까지 점수를 보여
    주지도 않는다.

    ## 🔗 의존성 — `POST /meals/{mealId}/confirm` 이 이 상태를 해소한다

    **confirm 은 `status = ANALYZING` + `is_recalculation = true` 인 식사를 정상
    입력으로 받아 채점하고 `EVALUATED` 로 옮겨야 한다.**

    이 조합은 "사용자가 확인 화면에서 음식을 고쳤다" 는 뜻이지 "워커가 분석 중"
    이 아니다. confirm 이 `ANALYZING` 을 통째로 막으면(예: 409 NOT_CONFIRMED)
    **사용자는 음식을 고친 뒤 영원히 확정하지 못한다** — 이 상태를 풀어 줄 다른
    경로가 없기 때문이다. confirm 작업에 이 문단을 함께 전달할 것.

    최초 분석(`is_recalculation = false`)의 `ANALYZING` 은 반대로 워커가 푼다
    (`jobs/analyze_meal.py` 8단계). 두 `ANALYZING` 의 해소 주체가 다르다 —
    가르는 건 `is_recalculation` 이다(`services.meal._is_editable` 참고).
    """
    amount_g = meal_service.to_grams(request.amount, request.unit)
    match = nutrition_service.resolve_by_name(
        db, name=request.display_name, amount_g=amount_g
    )

    try:
        response = meal_service.add_item(
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

    return ok(response)
