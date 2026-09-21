"""food_refs 테이블 접근. 여기 말고는 아무도 FoodRef 를 직접 쿼리하지 않는다."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import FoodCategory
from app.models.food import FoodRef


def normalize_name(name: str) -> str:
    """이름 비교용 정규화 — 공백과 밑줄을 지운다.

    공공 DB 는 구분자로 밑줄을 쓴다(`달걀_삶은것`). 사용자는 띄어쓰기로 친다
    (`달걀 삶은것`). 둘 다 지워 붙이면 같은 문자열이 되어 매칭된다.

    **`_normalized_name` 과 같은 변환이어야 한다.** 한쪽만 바뀌면 매칭이 조용히
    전부 빗나간다 — 에러가 아니라 "못 찾음" 으로 나와서 알아채기 어렵다.
    """
    return name.replace(" ", "").replace("_", "")


def _normalized_name():
    """`normalize_name` 의 SQL 판.

    `ix_food_refs_name_normalized` 가 이 식 그대로 걸려 있다. 식이 한 글자라도
    달라지면 인덱스를 타지 못하고 33만건을 전부 스캔한다.
    """
    return func.replace(func.replace(FoodRef.name, " ", ""), "_", "")


def find_unique_by_name(db: Session, name: str) -> FoodRef | None:
    """이름이 **딱 하나로 좁혀질 때만** 그 행을 준다. 0건이거나 여러 건이면 None.

    ## 왜 여러 건이면 포기하는가

    이름이 같아도 영양성분은 제각각이다 — 실측하면 `미역국` 14건의 열량이
    7~450 kcal(64배), `김치찌개` 28건이 16~140 kcal 이다. 그중 하나를 골라 주면
    그 값이 `meal_items.food_ref_id` 로 박제되고 Q/Q/S 채점까지 흘러간다.
    사용자는 자기 점수가 왜 그런지 알 방법이 없다.

    **정렬로 하나를 집으면 "결정적으로 틀린" 답이 된다.** 결정성은 재현성을 줄 뿐
    정확도를 주지 않는다. 영양정보가 비는 건 설계된 정상 경로지만(Rule Engine 이
    NULL 을 처리한다 — `qqs_evaluations.quality_score` 가 NULL 허용인 이유),
    틀린 영양정보는 조용한 오염이다.

    못 좁힌 경우는 계약서의 폴백 흐름으로 넘어간다 — 응답의 `matched: false` 가
    FE 에게 `GET /nutrition/candidates` 로 후보를 띄우라는 신호다. 퍼지 검색은
    **사람이 고르는** 그 자리에서 하면 안전하다.

    ## GENERAL 을 먼저 보는 이유

    사용자가 "배추김치" 라 쓰면 브랜드 제품이 아니라 그 음식을 뜻한다. 동명 행은
    대부분 `PROCESSED`(가공식품 31.6만건)라, GENERAL 에 딱 하나만 있으면 그게
    사용자가 말한 것이다 — `배추김치` 는 전체 75건이지만 GENERAL 은 1건이다.

    GENERAL 에 한 건도 없을 때만 전체에서 다시 찾는다. GENERAL 이 여러 건이면
    거기서 포기한다 — PROCESSED 를 더 봐도 더 애매해질 뿐이다.

    **기대는 낮게 잡을 것.** 이 분기가 실제로 구제하는 건 모호한 이름 31,101 개
    중 149 개(0.5%)다. `미역국`(GENERAL 5건) · `김치찌개`(GENERAL 5건)처럼
    GENERAL 안에서도 여러 건인 이름이 훨씬 많아 대부분은 그대로 포기한다.
    좁히는 것만으로 매칭률이 오르지는 않는다 — 나머지는 폴백의 몫이다.

    (측정: `dataset_version='20260828'` 기준. 정규화한 이름으로 묶어
    `total > 1 AND general = 1` 인 그룹 수를 셌다.)
    """
    matches_name = _normalized_name() == normalize_name(name)

    def _only(*extra_where) -> list[FoodRef]:
        # LIMIT 2 면 "유일한가" 를 판단하기에 충분하다. 33만건 테이블에서 일치하는
        # 행을 전부 ORM 객체로 만들 이유가 없다.
        stmt = select(FoodRef).where(matches_name, *extra_where).limit(2)
        return list(db.execute(stmt).scalars().all())

    general = _only(FoodRef.category == FoodCategory.GENERAL)
    if general:
        return general[0] if len(general) == 1 else None

    every = _only()
    return every[0] if len(every) == 1 else None
