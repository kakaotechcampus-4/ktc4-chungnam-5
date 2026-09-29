"""food_refs 이름 매칭과 섭취량 비례 환산.

사용자가 직접 입력한 음식명에는 AI 후보(`candidateFoodRefId`)가 없다. 이름으로
찾는 것이 유일한 단서라, 그 규칙을 여기서 고정한다.
"""

from decimal import Decimal

from app.models.enums import FoodCategory
from app.services import nutrition as nutrition_service
from app.tests.factories import make_food_ref


def test_resolve_returns_none_when_no_food_ref_has_that_name(db):
    make_food_ref(db, name="미역국")

    match = nutrition_service.resolve_by_name(
        db, name="존재하지않는음식", amount_g=Decimal("200")
    )

    assert match is None


def test_resolve_scales_nutrition_to_the_eaten_amount(db):
    """공공 DB 값은 `serving_size` 기준량이다. 먹은 g 만큼 비례로 늘린다."""
    make_food_ref(
        db,
        food_ref_id="KFD_MIYEOK",
        name="미역국",
        serving_size=Decimal("100.000"),
        calories=Decimal("50.000"),
        protein_g=Decimal("3.000"),
        fat_g=Decimal("1.500"),
        carbohydrate_g=Decimal("4.000"),
        fiber_g=Decimal("0.500"),
        sodium_mg=Decimal("600.000"),
    )

    match = nutrition_service.resolve_by_name(db, name="미역국", amount_g=Decimal("200"))

    assert match.food_ref_id == "KFD_MIYEOK"
    assert match.nutrition.kcal == Decimal("100.00")
    assert match.nutrition.protein_g == Decimal("6.00")
    assert match.nutrition.fat_g == Decimal("3.00")
    assert match.nutrition.carb_g == Decimal("8.00")
    assert match.nutrition.fiber_g == Decimal("1.00")
    assert match.nutrition.sodium_mg == Decimal("1200.00")


def test_resolve_leaves_nutrition_empty_when_amount_is_not_in_grams(db):
    """"계란 2개" 처럼 g 환산 근거가 없으면 영양성분을 지어내지 않는다.

    음식은 찾았으므로 `food_ref_id` 는 돌려준다 — 사용자가 확인 화면에서 실제 양을
    채우면 그때 환산된다. `services.meal.to_grams` 의 독스트링과 같은 판단이다.
    """
    make_food_ref(db, food_ref_id="KFD_EGG", name="삶은 계란")

    match = nutrition_service.resolve_by_name(db, name="삶은 계란", amount_g=None)

    assert match.food_ref_id == "KFD_EGG"
    assert match.nutrition is None


def test_resolve_leaves_nutrition_empty_when_serving_size_is_unknown(db):
    """기준량이 없으면 성분값이 어느 양에 대한 건지 알 수 없다 — 비례 계산이 불가능하다."""
    make_food_ref(db, food_ref_id="KFD_NO_BASIS", name="기준량없는음식", serving_size=None)

    match = nutrition_service.resolve_by_name(
        db, name="기준량없는음식", amount_g=Decimal("200")
    )

    assert match.food_ref_id == "KFD_NO_BASIS"
    assert match.nutrition is None


def test_resolve_gives_up_when_the_name_is_ambiguous(db):
    """동명 음식이 여러 건이면 **고르지 않는다.**

    실측하면 `미역국` 14건의 열량이 7~450 kcal(64배), `김치찌개` 28건이
    16~140 kcal 이다. 정렬로 하나를 집으면 결정적이긴 해도 결정적으로 틀린 답이
    되고, 그 값이 `meal_items.food_ref_id` 로 박제돼 Q/Q/S 채점까지 흘러간다.
    영양정보가 비는 건 Rule Engine 이 처리하지만, 틀린 영양정보는 조용한 오염이다.
    """
    make_food_ref(db, food_ref_id="KFD_AAA", name="된장국", calories=Decimal("19.000"))
    make_food_ref(db, food_ref_id="KFD_ZZZ", name="된장국", calories=Decimal("140.000"))

    assert nutrition_service.resolve_by_name(
        db, name="된장국", amount_g=Decimal("100")
    ) is None


def test_resolve_matches_across_spacing_and_underscores(db):
    """공공 DB 는 `달걀_삶은것`, 사용자는 `달걀 삶은것` 이라 친다 — 같은 음식이다."""
    make_food_ref(db, food_ref_id="KFD_EGG", name="달걀_삶은것")

    match = nutrition_service.resolve_by_name(
        db, name="달걀 삶은것", amount_g=Decimal("100")
    )

    assert match is not None
    assert match.food_ref_id == "KFD_EGG"


def test_resolve_prefers_the_general_row_over_processed_products(db):
    """"미역국" 은 브랜드 제품이 아니라 그 요리를 뜻한다.

    편차는 대부분 가공식품(31.6만건)에서 나온다 — GENERAL 로 좁히면 `미역국` 의
    열량 범위가 7~450 kcal 에서 7~12 kcal 이 된다.
    """
    make_food_ref(
        db, food_ref_id="KFD_DISH", name="미역국",
        category=FoodCategory.GENERAL, calories=Decimal("12.000"),
    )
    make_food_ref(
        db, food_ref_id="KFD_POUCH_1", name="미역국",
        category=FoodCategory.PROCESSED, calories=Decimal("450.000"),
    )
    make_food_ref(
        db, food_ref_id="KFD_POUCH_2", name="미역국",
        category=FoodCategory.PROCESSED, calories=Decimal("8.000"),
    )

    match = nutrition_service.resolve_by_name(db, name="미역국", amount_g=Decimal("100"))

    assert match is not None
    assert match.food_ref_id == "KFD_DISH"


def test_resolve_gives_up_when_general_itself_is_ambiguous(db):
    """GENERAL 이 여러 건이면 거기서 포기한다 — 가공식품을 더 봐도 더 애매해질 뿐이다."""
    make_food_ref(db, food_ref_id="KFD_G1", name="김치찌개", category=FoodCategory.GENERAL)
    make_food_ref(db, food_ref_id="KFD_G2", name="김치찌개", category=FoodCategory.GENERAL)
    make_food_ref(db, food_ref_id="KFD_P1", name="김치찌개", category=FoodCategory.PROCESSED)

    assert nutrition_service.resolve_by_name(
        db, name="김치찌개", amount_g=Decimal("100")
    ) is None


def test_resolve_falls_back_to_processed_when_no_general_row_exists(db):
    """GENERAL 에 한 건도 없을 때만 전체에서 다시 찾는다. 유일하면 그건 써도 된다."""
    make_food_ref(
        db, food_ref_id="KFD_ONLY", name="아메리카노",
        category=FoodCategory.PROCESSED, calories=Decimal("5.000"),
    )

    match = nutrition_service.resolve_by_name(
        db, name="아메리카노", amount_g=Decimal("100")
    )

    assert match is not None
    assert match.food_ref_id == "KFD_ONLY"


def test_resolve_survives_a_broken_serving_size(db):
    """기준량이 NaN 이면 배율이 NaN 이 되어 모든 성분을 물들인다.

    `_scale` 은 곱해질 값만 검사하므로 여기서 막지 않으면 NaN 이 그대로 통과해
    `NutritionInfo` 검증에서 터진다 — 사용자에게는 500 이다. Postgres NUMERIC 은
    NaN 을 담을 수 있어 공공 DB 적재분에 한 건만 섞여도 재현된다.
    """
    make_food_ref(db, food_ref_id="KFD_NAN", name="기준량깨진음식",
                  serving_size=Decimal("NaN"), calories=Decimal("50.000"))

    match = nutrition_service.resolve_by_name(
        db, name="기준량깨진음식", amount_g=Decimal("200")
    )

    assert match is not None
    assert match.food_ref_id == "KFD_NAN"
    assert match.nutrition is None


def test_search_finds_foods_containing_every_token(db):
    """`q` 를 공백으로 쪼갠 토큰이 **전부** 들어간 이름만 후보다.

    공백·밑줄을 지우고 비교하므로(`crud.food.normalize_name`) 어순이 달라도 잡힌다 —
    공공 DB 는 `김밥_계란` 처럼 뒤집어 적는 이름이 흔하다.
    """
    make_food_ref(db, food_ref_id="KFD_GIMBAP_EGG", name="김밥_계란")
    make_food_ref(db, food_ref_id="KFD_EGG_BREAD", name="계란빵")

    # `limit=1` 이라 퍼지 폴백이 돌지 않는다 — 부분일치만으로 목록이 차기 때문이다.
    # 폴백까지 켜지면 토큰 하나만 걸친 `계란빵` 도 따라 들어와 AND 를 검증하지 못한다
    # (그 채움 동작은 아래 `test_search_pads_...` 가 따로 고정한다).
    candidates = nutrition_service.search_candidates(db, query="계란 김밥", limit=1)

    assert [candidate.food_ref_id for candidate in candidates] == ["KFD_GIMBAP_EGG"]


def test_search_falls_back_to_fuzzy_matching_when_no_name_contains_the_query(db):
    """오타로 부분일치가 0건이면 바이그램으로 비슷한 이름을 찾는다.

    `김치찌게` 는 `김치찌개` 의 흔한 오타다. 부분일치만으로는 빈 목록이 나가고,
    사용자는 자기가 오타를 냈다는 걸 모른 채 "공공 DB 에 없는 음식" 으로 오해한다.
    """
    make_food_ref(db, food_ref_id="KFD_KIMCHI_JJIGAE", name="김치찌개")

    candidates = nutrition_service.search_candidates(db, query="김치찌게", limit=5)

    assert [candidate.food_ref_id for candidate in candidates] == ["KFD_KIMCHI_JJIGAE"]


def test_search_pads_the_list_with_similar_names_when_exact_matches_fall_short(db):
    """부분일치가 `limit` 을 못 채우면 비슷한 이름으로 채운다 — 부분일치가 앞이다.

    순서가 곧 관련도다. 검색어를 통째로 담은 이름이 두 글자만 겹치는 이름보다 뒤로
    가면, 사용자는 목록 맨 위를 보고 "내가 찾는 게 없다" 고 판단한다.
    """
    make_food_ref(db, food_ref_id="KFD_GIMBAP_EGG", name="김밥_계란")
    make_food_ref(db, food_ref_id="KFD_EGG_BREAD", name="계란빵")

    candidates = nutrition_service.search_candidates(db, query="계란 김밥", limit=5)

    assert [candidate.food_ref_id for candidate in candidates] == [
        "KFD_GIMBAP_EGG",
        "KFD_EGG_BREAD",
    ]


def test_search_treats_a_percent_sign_as_a_literal_character(db):
    """`%` 는 사용자가 친 글자다 — LIKE 와일드카드로 새면 안 된다.

    새면 검색어와 아무 상관 없는 33만건이 전부 후보가 되어, 사용자는 자기가 친 것과
    무관한 음식을 고르게 된다.
    """
    make_food_ref(db, food_ref_id="KFD_MIYEOK", name="미역국")

    candidates = nutrition_service.search_candidates(db, query="%역%", limit=5)

    assert [candidate.food_ref_id for candidate in candidates] == []


def test_search_drops_fuzzy_matches_that_share_too_few_fragments(db):
    """조각 몇 개가 우연히 겹쳤을 뿐인 이름은 후보가 아니다 — 빈 목록이 정직한 답이다.

    실데이터에서 `존재하지않는음식` 이 `굳지않는송편` 을 후보로 냈다(`지않`·`않는`
    두 조각이 겹친다). 사용자는 공공 DB 에 자기 음식이 없다는 걸 알아야 직접 입력
    으로 넘어가는데, 엉뚱한 후보 다섯 줄이 뜨면 그 갈래가 가려진다.
    """
    make_food_ref(db, food_ref_id="KFD_SONGPYEON", name="굳지않는송편")

    candidates = nutrition_service.search_candidates(
        db, query="존재하지않는음식", limit=5
    )

    assert [candidate.food_ref_id for candidate in candidates] == []


def test_bigrams_are_capped_so_one_query_cannot_scan_without_bound():
    """긴 검색어의 조각 수에 상한을 둔다 — 조각 하나가 33만건 평가 한 번이다.

    결과로는 드러나지 않는 비용이라(같은 후보가 나온다) 순수 함수를 직접 본다.
    상한이 없으면 48자 검색어 하나가 조각 41개로 4.2초를 쓴다(실측).

    퍼지는 근사 검색이라 앞쪽 조각만 써도 성격이 바뀌지 않는다 — 부분일치(1단계)는
    토큰을 하나도 버리지 않으므로 정확도를 책임지는 쪽은 그대로다.
    """
    long_token = nutrition_service._tokenize("가나다라마바사아자차카타파하거너더러머버서")

    bigrams = nutrition_service._bigrams(long_token)

    assert len(bigrams) == nutrition_service.MAX_FUZZY_FRAGMENTS
    assert bigrams[0] == "가나"


def test_search_puts_general_foods_before_processed_products(db):
    """이름이 같으면 일반음식이 먼저다.

    사용자가 `김치찌개` 라 치면 브랜드 제품(PROCESSED 31.6만건)이 아니라 그 음식을
    뜻한다(`crud.food.find_unique_by_name` 과 같은 판단). 후보 목록 맨 위 몇 줄이
    가공식품으로 채워지면 사용자는 자기가 찾는 음식이 없다고 본다.

    `id` 는 일부러 가공식품 쪽이 먼저 오도록 지었다 — GENERAL 우선 규칙을 빼면
    마지막 정렬 기준인 `id` 가 이겨서 이 테스트가 깨진다.
    """
    make_food_ref(
        db, food_ref_id="A_PROCESSED", name="김치찌개", category=FoodCategory.PROCESSED
    )
    make_food_ref(
        db, food_ref_id="Z_GENERAL", name="김치찌개", category=FoodCategory.GENERAL
    )

    candidates = nutrition_service.search_candidates(db, query="김치찌개", limit=5)

    assert [candidate.food_ref_id for candidate in candidates] == [
        "Z_GENERAL",
        "A_PROCESSED",
    ]


def test_search_puts_shorter_names_first(db):
    """검색어에 군더더기가 적게 붙은 이름이 사용자가 친 것에 가깝다.

    이름을 일부러 가나다순과 길이순이 어긋나게 지었다 — 길이 기준을 빼면 이름순이
    이겨서 `가나다라계란` 이 먼저 온다.
    """
    make_food_ref(db, food_ref_id="KFD_LONG", name="가나다라계란")
    make_food_ref(db, food_ref_id="KFD_SHORT", name="계란빵")

    candidates = nutrition_service.search_candidates(db, query="계란", limit=5)

    assert [candidate.food_ref_id for candidate in candidates] == [
        "KFD_SHORT",
        "KFD_LONG",
    ]
