"""food_refs 테이블 접근. 여기 말고는 아무도 FoodRef 를 직접 쿼리하지 않는다."""

from sqlalchemy import case, func, literal, select
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


def _contains(fragment: str):
    """이름 안에 `fragment` 가 들어 있는가. **와일드카드는 글자로 취급한다.**

    `%` 와 `_` 는 LIKE 의 메타문자다. 그대로 넘기면 사용자가 친 `%` 하나가
    "아무 글자나" 가 되어 33만건이 전부 후보로 올라온다. 역슬래시는 아래에서 지정한
    이스케이프 문자 자신이라 함께 막는다 — **순서가 중요하다.** 역슬래시를 나중에
    치환하면 방금 붙인 이스케이프까지 한 번 더 escape 되어 패턴이 어긋난다.

    (`normalize_name` 이 `_` 를 이미 지우지만 여기서 한 번 더 막는다 — 이 함수가
    정규화를 거치지 않은 문자열에 불려도 안전해야 한다.)
    """
    escaped = (
        fragment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return _normalized_name().like(f"%{escaped}%", escape="\\")


def search_by_tokens(db: Session, *, tokens: list[str], limit: int) -> list[FoodRef]:
    """토큰이 **전부** 들어간 이름을 찾는다. 사용자가 후보 목록에서 고를 때 쓴다.

    `find_unique_by_name` 과 정반대의 판단이다 — 저쪽은 하나로 좁혀지지 않으면
    포기하지만(틀린 값이 박제되므로), 여기서는 고르는 주체가 사람이라 여러 건을
    그대로 보여주는 것이 맞다.

    `LIKE '%…%'` 는 `ix_food_refs_name_normalized` 를 타지 못한다 — 33만건 순차
    스캔이다. 앞뒤 와일드카드를 지우면 인덱스를 타지만 `김밥_계란` 처럼 뒤집힌
    이름을 통째로 놓친다.

    비용은 **조각 수에 비례한다.** 개발용 컨테이너(`postgres:17-alpine`, 기본
    설정, 병렬 워커 2개, 캐시 워밍 후)에서 토큰 1~3개 검색이 90~300ms 였다.
    절대값은 장비·캐시 상태에 따라 쉽게 2~3배 흔들리니 "조각 하나 = 33만건 평가
    한 번" 이라는 비례 관계만 믿을 것.
    """
    matches_every_token = [_contains(token) for token in tokens]
    stmt = (
        select(FoodRef)
        .where(*matches_every_token)
        .order_by(*_candidate_order())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def _candidate_order():
    """후보 정렬 — GENERAL 먼저, 짧은 이름 먼저, 그다음은 결정적으로.

    GENERAL 을 먼저 두는 이유는 `find_unique_by_name` 과 같다: 사용자가 "배추김치"
    라 치면 브랜드 제품(PROCESSED 31.6만건)이 아니라 그 음식을 뜻한다.

    짧은 이름을 먼저 두는 건 검색어에 군더더기가 적게 붙은 쪽이 사용자가 친 것에
    가깝기 때문이다 — `계란` 에 `계란빵` 이 `부추넣은 계란말이` 보다 먼저 온다.

    마지막 `id` 는 동점일 때 순서를 고정한다. 없으면 같은 질의가 호출마다 다른
    후보를 보여준다 — 공공 DB 에는 이름·성분이 같은 행이 실제로 여럿 있다.
    """
    return (
        (FoodRef.category == FoodCategory.GENERAL).desc(),
        func.length(FoodRef.name),
        FoodRef.name,
        FoodRef.id,
    )


def search_by_bigrams(
    db: Session,
    *,
    bigrams: list[str],
    min_score: int,
    limit: int,
    exclude_ids: list[str],
) -> list[FoodRef]:
    """이름에 겹치는 두 글자가 많은 순으로 찾는다 — 오타를 구제하는 퍼지 검색.

    `김치찌게`(오타)의 두 글자 조각 `김치`·`치찌`·`찌게` 중 둘이 `김치찌개` 에
    들어 있어 후보로 올라온다. 부분일치가 0건일 때만 부르는 경로다.

    ## 왜 `pg_trgm` 이 아닌가

    **이 DB 에서 `pg_trgm` 은 한글에 아무 값도 내지 않는다.** 데이터베이스가
    `LC_COLLATE=C` · `LC_CTYPE=C` 로 만들어져 있어 pg_trgm 이 한글 바이트를
    alnum 으로 보지 않고 전부 버린다 — `show_trgm('계란')` 이 `{}` 이고
    `similarity('김치찌게','김치찌개')` 가 `0` 이다. 고치려면 DB 를 UTF-8 로케일로
    **재생성**해야 한다(마이그레이션으로는 안 된다). CJK 용 `pg_bigm` 은
    `postgres:17-alpine` 에 들어 있지 않다.

    그래서 두 글자 조각 비교를 SQL 로 직접 적는다. 확장 기능도 인덱스도 필요
    없지만 33만건 순차 스캔이고, 조각 수에 비례해 비싸진다 — 같은 컨테이너에서
    조각 3개 300ms, 8개 580ms, (상한을 걸기 전) 41개가 4.2초였다. 부분일치가
    후보를 채우면 이 쿼리는 아예 돌지 않는다.

    `exclude_ids` 는 부분일치가 이미 집은 행이다 — 빼지 않으면 같은 음식이 목록에
    두 번 나온다.

    `min_score` 는 "조각이 몇 개나 겹쳐야 후보로 치는가" 다. 기준을 정하는 건
    호출부이고(`services.nutrition.search_candidates`), 여기서는 세기만 한다.
    """
    hits = [_contains(bigram) for bigram in bigrams]
    # 맞은 조각 수가 곧 점수다. bool 을 그대로 더하지 않는 건 Postgres 가 boolean
    # 덧셈을 지원하지 않기 때문이다.
    score = sum((case((hit, 1), else_=0) for hit in hits), start=literal(0))

    # `min_score` 로 거른다 — 조각 하나가 우연히 겹친 이름은 후보가 아니다.
    # `OR` 로만 거르면 `존재하지않는음식` 이 `굳지않는송편` 을 데려온다(실측).
    # 조건이 곧 "겹친 조각 수" 라 OR 는 따로 두지 않는다(점수가 1 이상이면 포함이다).
    not_already_found = [FoodRef.id.notin_(exclude_ids)] if exclude_ids else []
    stmt = (
        select(FoodRef)
        .where(score >= min_score, *not_already_found)
        .order_by(score.desc(), *_candidate_order())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())
