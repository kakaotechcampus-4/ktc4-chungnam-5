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
