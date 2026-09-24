"""영양정보 폴백 API — 이름 매칭이 실패한 음식에 사용자가 직접 값을 붙이는 경로."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.nutrition import NutritionCandidatesResponse
from app.services import nutrition as nutrition_service

router = APIRouter()


@router.get(
    "/nutrition/candidates",
    response_model=ApiResponse[NutritionCandidatesResponse],
    responses=error_responses(401, 422),
)
def search_nutrition_candidates(
    # `min_length` 는 서비스의 규칙을 문서(OpenAPI)에 비추기 위한 것이고, 진짜
    # 검사는 정규화 뒤에 서비스가 한다 — 원문 길이로는 `"_"` 가 통과한다(지우고 나면
    # 아무것도 안 남는다). 상수를 참조하는 건 두 곳이 조용히 어긋나지 않게 하기
    # 위해서다.
    #
    # `max_length` 는 정규화 전 길이의 안전망이다. 실제 비용 상한은 토큰·조각 수로
    # 거는 쪽이 정확해서(`MAX_QUERY_TOKENS` · `MAX_FUZZY_FRAGMENTS`) 여기는
    # "사람이 식품명으로 칠 리 없는 길이" 를 막는 정도만 한다.
    q: str = Query(..., min_length=nutrition_service.MIN_QUERY_LENGTH, max_length=50),
    limit: int = Query(default=5, ge=1, le=20),
    # 쓰지 않는 값이지만 **지우면 안 된다** — 이 의존성이 인증 이음새를 태우는
    # 유일한 자리라, 빼면 라우트가 조용히 공개된다.
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[NutritionCandidatesResponse]:
    """사용자가 고를 공공 DB 후보를 검색한다.

    `matched: false` 로 나간 음식(`POST /meals/{mealId}/items` 독스트링 참고)에
    영양정보를 붙이는 폴백의 첫 단계다. 사용자가 여기서 고른 `foodRefId` 는
    `PUT /meals/{mealId}/items/{itemId}/nutrition` 으로 간다.

    ## 검색은 두 단계다

    1. **부분일치** — `q` 를 공백으로 쪼갠 토큰이 전부 들어간 이름
    2. **퍼지 폴백** — 1단계가 `limit` 을 못 채웠을 때만, 두 글자 조각이 겹치는
       순으로 채운다. `김치찌게` 같은 오타를 구제한다

    둘 다 33만건 순차 스캔이다 — `LIKE '%…%'` 는 인덱스를 타지 못한다. 1단계가
    목록을 채우면 2단계 쿼리는 아예 돌지 않으므로, 흔한 검색어일수록 싸다.
    왜 `pg_trgm` 이 아닌지는 `crud.food.search_by_bigrams` 에 적어 두었다.

    ## ⚠️ 비용 상한은 인증이 아니라 쿼리 쪽에 있다

    `X-User-Id` 는 **인증이 아니다** — `core.deps.get_current_user_id` 는 헤더의
    UUID 형식만 보고 DB 를 조회하지 않는다. 즉 아무 UUID 나 넣으면 통과하므로,
    "인증이 있으니 비싼 요청을 못 돌린다" 는 말은 성립하지 않는다.

    그래서 상한을 요청 내용에 직접 건다 — 단어 수(`MAX_QUERY_TOKENS`)와 퍼지 조각
    수(`MAX_FUZZY_FRAGMENTS`)다. 상한이 없을 때 48자 검색어 한 줄이 4.2초를 썼고,
    `limit` 은 이 비용을 전혀 줄이지 못한다(정렬 전에 모든 행을 평가해야 한다).

    ## ⚠️ 동의어를 모른다

    `계란` 과 `달걀` 은 다른 글자다. 공공 DB 의 삶은 달걀은 `달걀_삶은것` 이라
    `q=계란` 으로는 **어떤 방식으로도 나오지 않는다** — 명세서
    (`contracts/API.md`)의 예시가 정확히 이 경우다. 동의어 사전은 이 엔드포인트
    밖의 별도 작업이고, 그때까지 FE 는 사용자가 다른 이름으로 다시 검색하거나
    직접 입력으로 넘어갈 수 있게 두어야 한다.

    ## ⚠️ 같은 이름이 여러 건 나온다 — FE 가 구분해 보여줘야 한다

    공공 DB 에는 이름이 똑같고 성분이 다른 행이 흔하다(`김치찌개` 는 GENERAL 만
    5건, `미역국` 14건의 열량이 7~450 kcal). 그게 애초에 이름 매칭이 포기하고
    이 화면으로 넘어온 이유이므로 서버가 임의로 하나로 합치지 않는다
    (`crud.food.find_unique_by_name` 참고). **후보 목록에 이름만 띄우면 사용자는
    똑같은 줄 다섯 개를 보게 된다** — `nutrition.kcal` 을 함께 보여줘야 고를 수
    있다. 그 값이 `servingSizeG` 기준량이라는 점도 함께 적어야 한다
    (`schemas.nutrition.FoodCandidate`).

    ## 인증 이음새를 타는 이유

    공개 데이터를 읽을 뿐이지만 다른 모든 라우트와 같은 이음새를 탄다(README 절대
    규칙 6). JWT 가 붙으면 이 라우트도 함께 진짜 인증이 된다 — 그때까지 이 헤더는
    비용 방어가 아니라 자리만 잡아 두는 것이다(위 문단 참고).
    """
    try:
        candidates = nutrition_service.search_candidates(db, query=q, limit=limit)
    except ValueError as exc:
        # 선언적 `min_length` 는 원문 길이만 본다. 정규화 뒤 길이 규칙은 서비스에
        # 있고(`services.nutrition.MIN_QUERY_LENGTH`), 여기서 422 로 옮긴다 —
        # `endpoints/meals.py` 가 cursor 를 400 으로 옮기는 것과 같은 자리다.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ok(NutritionCandidatesResponse(candidates=candidates))
