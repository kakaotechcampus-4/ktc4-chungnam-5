"""food_refs 이름 매칭과 섭취량 비례 환산.

사용자가 직접 입력한 음식명에는 AI 후보(`candidateFoodRefId`)가 없다. 이름으로
찾는 것이 유일한 단서라, 그 규칙을 여기서 고정한다.
"""

from decimal import Decimal

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


def test_resolve_is_deterministic_when_names_collide(db):
    """동명이인 음식이 있어도 항상 같은 행을 고른다.

    공공 DB 는 33만건이고 가공식품은 제조사만 다른 동명 행이 흔하다. 정렬이 없으면
    같은 입력이 시점에 따라 다른 kcal 을 돌려주고, 그 값이 `food_ref_id` 로 박제돼
    Q/Q/S 점수까지 달라진다 — 사용자는 어디서 틀렸는지 알 수 없다.
    """
    make_food_ref(db, food_ref_id="KFD_ZZZ", name="된장국")
    make_food_ref(db, food_ref_id="KFD_AAA", name="된장국")

    picked = {
        nutrition_service.resolve_by_name(
            db, name="된장국", amount_g=Decimal("100")
        ).food_ref_id
        for _ in range(3)
    }

    assert picked == {"KFD_AAA"}


def test_resolve_prefers_a_row_that_actually_has_nutrition(db):
    """결측 행을 먼저 집으면 매칭에 성공하고도 영양성분이 비어 나간다.

    원본 데이터에 결측이 많다 — `외식` 15,225건은 지방 87% · 탄수화물 84% 가
    비어 있다(README "공공 영양DB" 절). 이름이 같다면 값이 있는 행을 고른다.
    """
    make_food_ref(db, food_ref_id="KFD_AAA_EMPTY", name="순두부찌개", calories=None)
    make_food_ref(db, food_ref_id="KFD_ZZZ_FULL", name="순두부찌개", calories=Decimal("80.000"))

    match = nutrition_service.resolve_by_name(
        db, name="순두부찌개", amount_g=Decimal("100")
    )

    assert match.food_ref_id == "KFD_ZZZ_FULL"
    assert match.nutrition.kcal == Decimal("80.00")
