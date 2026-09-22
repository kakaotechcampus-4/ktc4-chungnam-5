"""식사 구성 음식(meal item) 수정 API.

세 동작(PATCH · POST · DELETE)이 모두 식사를 재계산 대기로 표시한다 — 점수가 더는
유효하지 않다는 뜻이다.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.meal import (
    MealItemCreateRequest,
    MealItemCreateResponse,
    MealItemDeleteResponse,
    MealItemsUpdateRequest,
    MealItemsUpdateResponse,
    MealItemUpdate,
)
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
    `matched` 는 API 명세서의 정의대로 **"영양정보가 함께 나가는가"** 이지
    "공공 DB 에서 음식을 찾았는가" 가 아니다. 세 갈래다:

    | 결과 | `matched` | `nutrition` | DB `food_ref_id` |
    |---|---|---|---|
    | 하나로 좁혀짐 + g 환산 가능 | `true` | 있음 | 연결 |
    | 하나로 좁혀짐 + 환산 불가("2개") | `false` | `null` | **연결 유지** |
    | 못 좁힘 (없음 · "김치찌개" 28건) | `false` | `null` | `NULL` |

    두 번째가 링크를 남기는 건 사용자가 나중에 양을 g 으로 고치면 곧바로 환산되기
    때문이다. 매칭 규칙은 `crud.food.find_unique_by_name` 참고.

    ## `matched: false` 는 에러가 아니라 폴백 신호다

    항목은 **정상 저장된다(201).** 영양정보만 비어 있다. FE 는 이 값을 보고 API
    명세서의 "영양정보 폴백" 으로 넘어간다:

        matched: false
          → GET /nutrition/candidates?q=<displayName>&limit=5   후보 검색
          → 사용자가 고르거나 직접 입력
          → PUT /meals/{mealId}/items/{itemId}/nutrition

    이름 매칭을 일부러 좁게 잡았다 — 틀린 영양성분이 `food_ref_id` 로 박제돼
    Q/Q/S 채점까지 흘러가는 것보다 비어 있는 편이 낫기 때문이다(`quality_score`
    가 NULL 허용인 이유). 그래서 흔한 음식도 이 경로로 간다: 실측하면
    `김치찌개` · `미역국` · `제육볶음` · `현미밥` 이 전부 여기 해당한다.

    ⚠️ **`/nutrition/candidates` 와 `PUT .../nutrition` 이 붙기 전까지 사용자는
    영양정보를 채울 수단이 없다.** 항목과 양은 남으므로 데이터가 유실되지는
    않지만, 그 두 엔드포인트가 이 기능의 선행 조건이다.

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


def _resolve_update(
    db: Session, *, update: MealItemUpdate
) -> meal_service.ResolvedItemUpdate:
    """수정 요청 한 건의 g 환산과 공공 DB 매칭을 미리 풀어 둔다.

    이름이 그대로여도 일단 찾아 둔다 — 기존 링크를 유지할지는 DB 의 현재 이름을 아는
    `services.meal.update_items` 가 정한다. 여기서 미리 가를 수 없는 건 이 레이어가
    항목의 현재 값을 모르기 때문이고, `normalized_name` 에 인덱스가 있어 한 건 더
    찾는 비용이 그 순서를 뒤집을 만큼 크지 않다.
    """
    amount_g = meal_service.to_grams(update.amount, update.unit)
    match = nutrition_service.resolve_by_name(
        db, name=update.display_name, amount_g=amount_g
    )
    return meal_service.ResolvedItemUpdate(
        request=update,
        amount_g=amount_g,
        food_ref_id=match.food_ref_id if match is not None else None,
    )


@router.patch(
    "/meals/{meal_id}/items",
    response_model=ApiResponse[MealItemsUpdateResponse],
    responses=error_responses(401, 404, 409, 422),
)
def update_meal_items(
    meal_id: uuid.UUID,
    request: MealItemsUpdateRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealItemsUpdateResponse]:
    """사용자가 확인 화면에서 AI 인식 결과를 고친다.

    고친 항목만 배열에 담아 보낸다. 항목 하나는 통째로 교체된다 — 세 필드가 모두
    필수다(`MealItemUpdate` 참고).

    ## 이름이 바뀌거나 링크가 없을 때 공공 DB 를 다시 찾는다

    **이름이 그대로고 `food_ref_id` 가 이미 있으면** 그 링크를 지킨다. 이름 매칭
    (`crud.food.find_unique_by_name`)은 일부러 보수적이라 `김치찌개` 같은 흔한
    음식에 `None` 을 주는데, AI 가 `candidateFoodRefId` 로 정확히 연결해 둔 링크를
    "250g → 220g" 같은 수정이 끊어 버리면 영양정보가 이유 없이 사라진다.

    **이름이 그대로여도 `food_ref_id` 가 NULL 이면 새로 찾은 결과를 건다.** AI 가
    후보를 주지 못했다는 뜻이고 이름 매칭은 워커가 쓰지 않는 별개의 신호라, 지킬
    링크가 없는데 버릴 이유가 없다. "미역국 2개 → 200g" 처럼 이제 g 으로 환산되는
    수정이 영양정보를 얻는 경로다. 이름으로도 못 좁히면 NULL 그대로다.

    이름이 바뀌면 다른 음식이 된 것이므로 다시 찾고, 못 찾으면 링크를 끊는다
    (`matched: false` 폴백 경로로 간다 — `add_meal_item` 독스트링 참고).

    이름을 바꾸면 `confidence` 도 지운다 — AI 가 다른 이름에 매긴 신뢰도다. 남겨 두면
    사용자가 직접 써 넣은 이름이 FE 에서 `< 0.8` 로 강조된다. 지운 값은
    `user_corrections.original_value` 에 남아 인식 성능 평가에 쓸 수 있다.

    ## g 으로 환산되지 않는 양도 버리지 않는다

    `confirmed_amount_g` 는 환산된 값만 담으므로 "2개" 는 NULL 이 된다. 사용자가
    입력한 숫자·단위는 `confirmed_amount` · `confirmed_unit` 에 그대로 남는다 —
    출처(MODEL/USER)와 무관하게 같은 자리다. 이 컬럼들이 없으면 "계란 2개 → 3개" 같은
    수정이 200 을 받고도 아무 데도 남지 않는다.

    ## 값이 하나도 안 바뀌어도 재계산 대기로 옮긴다

    확인 화면이 고치지 않은 항목까지 보내면 `EVALUATED` 식사가 `ANALYZING` 으로
    되돌아가 점수가 무효 표시되고, 사용자는 [확인] 을 다시 눌러야 한다. 그래도 이렇게
    두는 건 명세서 응답이 `status: ANALYZING` 으로 고정이기 때문이다 — 바뀐 게 없을
    때만 상태를 유지하면 그 응답은 `status: EVALUATED` 인데 `steps` 는
    `DB_MATCHING: RUNNING` 이라, FE 가 믿을 값이 하나도 남지 않는다.

    **FE 는 고친 항목만 보낸다** — 명세서의 요청 모양(`items` 배열)이 이미 그 전제다.

    없는 식사 · 남의 식사 · 삭제된 식사는 전부 404 로 같게 응답한다. 요청한 항목 중
    하나라도 이 식사의 것이 아닐 때도 404 이며, 이 경우 **아무 항목도 반영되지
    않는다**(`services.meal.update_items` 참고).

    ## ⚠️ 이름을 바꾸면 영양정보가 조용히 비워질 수 있다

    새 이름을 공공 DB 에서 못 좁히면 링크가 끊기는데(위 참고) **이 응답에는 그 사실을
    알리는 필드가 없다** — 명세서가 `matched` 를 POST 응답에만 두기 때문이다. FE 는
    `GET /meals/{mealId}` 로 다시 읽어야 알 수 있다.

    ## ⚠️ 응답의 `steps` 는 고정값이다

    명세서(`contracts/API.md`)의 예시를 그대로 돌려준다 — 실시간 진행상황이
    아니다. POST 와 마찬가지로 여기서는 큐에 아무것도 넣지 않으므로
    (`schemas.meal.RECALCULATION_STEPS` 참고) `DB_MATCHING: RUNNING` 이라고 나가도
    실제로 도는 작업은 없다. 공공 DB 매칭은 이 요청을 처리하는 동안 끝난다.

    **FE 가 이 응답을 보고 `GET /meals/{mealId}` 폴링을 시작하면 상태는 영원히
    바뀌지 않는다.** `ANALYZING` + `is_recalculation = true` 를 푸는 것은 워커가
    아니라 사용자가 [확인] 을 누를 때 불리는 `POST /meals/{mealId}/confirm` 이다.
    """
    resolved = [_resolve_update(db, update=update) for update in request.items]

    try:
        response = meal_service.update_items(
            db, user_id=user_id, meal_id=meal_id, resolved=resolved
        )
    except (meal_service.MealNotFoundError, meal_service.MealItemNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except meal_service.MealNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ok(response)


@router.delete(
    "/meals/{meal_id}/items/{item_id}",
    response_model=ApiResponse[MealItemDeleteResponse],
    responses=error_responses(401, 404, 409, 422),
)
def delete_meal_item(
    meal_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealItemDeleteResponse]:
    """사용자가 AI 가 잘못 인식한 음식을 확인 화면에서 뺀다.

    없는 식사 · 남의 식사 · 삭제된 식사 · 이 식사의 것이 아닌 `itemId` 는 전부 404 로
    같게 응답한다 (`DELETE /meals/{mealId}` 와 같은 이유 — 소유권 누출 방지). 이미
    지운 항목을 다시 지우려는 요청도 여기 해당한다 — 멱등이 아니다.

    ⚠️ 다만 **같은 항목에 DELETE 두 건이 동시에 오면 둘 다 200 이다.** 각자 자기
    트랜잭션에서 `get_item` 을 통과한 뒤 DELETE 를 날리고, 뒤늦은 쪽은 0 행을
    지우는데 SQLAlchemy 는 이를 경고로만 처리한다(version_id_col 이 없는 DELETE 의
    rowcount 불일치는 raise 하지 않는다). 즉 "이미 지워진 항목" 요청의 응답이
    타이밍에 따라 404 도 200 도 된다. 데이터는 안전하고 사용자 영향은 확인 화면
    더블탭 시 에러 토스트가 뜰까 말까 하는 정도라 지금은 그대로 둔다 — FE 가 404 를
    보고 버그로 의심하기 전에 여기를 먼저 읽으라고 적어 둔다.

    ## ⚠️ hard delete 다 — 그 항목의 인식 이력이 함께 사라진다

    `meal_items` 에는 `deleted_at` 이 없다. 행을 지우면 `user_corrections` 도 따라
    사라지고(`MealItem.corrections` 관계의 `cascade="all, delete-orphan"` 이 먼저
    지운다 — `crud.meal.delete_item` 참고), `original_food_name` · `estimated_*` 에
    담긴 **AI 최초 추정값도 함께 없어진다.**

    그래서 **"AI 가 없는 음식을 인식했다" 는 오인식 신호는 어디에도 남지 않는다** —
    사용자가 이름이나 양을 고친 경우(PATCH)는 `user_corrections` 에 남는데, 통째로
    지운 경우만 증거가 없다. 인식 성능을 집계할 때 이 구멍을 기억해야 한다. 메우려면
    soft delete 로 바꾸거나 별도 감사 로그를 두어야 하고, 둘 다 이 엔드포인트만의
    변경으로 끝나지 않는다(모든 조회가 필터를 기억해야 한다).

    ## 응답에 `steps` 가 없다

    명세서(`contracts/API.md`)가 `{ status, isRecalculation }` 만 준다. PATCH 와
    달리 진행 단계 배열이 없는 건 이 응답의 정직한 모양이다 — 어차피 여기서도 큐에
    아무것도 넣지 않으므로(`add_meal_item` 독스트링의 "ANALYZING 은 '워커가 도는
    중' 이 아니다" 참고) 폴링할 작업 자체가 없다.

    ## 마지막 항목도 지울 수 있다 — 항목 0 개 식사가 남는다

    막으면 "잘못 인식된 유일한 항목을 지우고 올바른 걸 넣기" 가 POST 를 먼저 해야
    하는 순서 제약이 된다. 식사를 통째로 지우는 경로는 `DELETE /meals/{mealId}` 다.

    이 상태를 보는 곳이 둘이다.

    - **`GET /meals`** — 이미 돌고 있다. `crud.get_display_names` 가 항목 없는
      식사를 넣지 않아 `displayName` 이 `""` 로 나간다. 새 동작은 아니다 —
      `FAILED` 식사도 항목이 0 개라 목록은 전부터 이 경로를 탄다. 제목 없는 카드가
      문제라면 고칠 자리는 `services.meal.list_meals` 이지 여기가 아니다
      (`test_meal_with_no_items_left_still_lists_with_an_empty_name` 이 고정).
    - 🔗 **`POST /meals/{mealId}/confirm`** — 아직 없다. 항목 0 개 식사로 Q/Q/S 를
      채점하면 0 kcal 짜리 점수가 나가므로 이 경우를 따로 정해야 한다(거부할지,
      `FAILED` 로 보낼지). confirm 작업에 이 문단을 함께 전달할 것 —
      `add_meal_item` 독스트링의 의존성 항목과 같은 자리다.
    """
    try:
        response = meal_service.delete_item(
            db, user_id=user_id, meal_id=meal_id, item_id=item_id
        )
    except (meal_service.MealNotFoundError, meal_service.MealItemNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except meal_service.MealNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ok(response)
