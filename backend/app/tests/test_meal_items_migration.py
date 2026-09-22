"""`36301603847a` 의 백필 SQL 검증.

이 컬럼들이 생기기 전의 `POST /meals/{mealId}/items` 는 사용자 입력을
`confirmed_amount_g`(환산값)와 `raw_ai_result`(원본 숫자·단위)에 나눠 썼다. 그 행을
새 읽기 규칙이 읽으려면 `confirmed_amount` · `confirmed_unit` 이 채워져 있어야 한다.

마이그레이션 전체를 되감는 대신 SQL 상수를 그대로 실행한다 — 이 스위트는
`alembic upgrade head` 가 끝난 DB 위에서 도는데, 되감으면 세션 픽스처가 깨진다.
"""

from decimal import Decimal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.models.enums import MealItemSource
from app.models.meal import MealItem
from app.services.meal import _stored_amount
from app.tests.factories import make_meal, make_user


def _backfill_sql() -> str:
    """마이그레이션 파일이 실제로 실행하는 SQL 을 그대로 가져온다.

    복사해 두면 마이그레이션만 고쳤을 때 테스트가 옛 SQL 을 통과시킨다.
    """
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    return script.get_revision("36301603847a").module.BACKFILL_USER_AMOUNTS


def _legacy_user_item(db, meal_id) -> MealItem:
    """컬럼이 생기기 전 `POST` 가 만들던 모양 그대로."""
    item = MealItem(
        meal_id=meal_id,
        original_food_name="미역국",
        display_name="미역국",
        confirmed_amount_g=Decimal("200.00"),
        source=MealItemSource.USER,
        raw_ai_result={"amount": "200", "unit": "g"},
    )
    db.add(item)
    db.flush()
    return item


def test_legacy_user_row_loses_its_amount_without_the_backfill(db):
    """왜 백필이 필요한지 — 이 행은 새 규칙에서 양이 통째로 사라진다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = _legacy_user_item(db, meal.id)

    # confirmed_amount 가 NULL 이라 estimated_* 로 폴백하는데 USER 행은 그쪽이 전부
    # NULL 이다 — confirmed_amount_g 에 200.00 이 멀쩡히 있는데도 "모름" 이 된다.
    assert item.confirmed_amount_g == Decimal("200.00")
    assert _stored_amount(item) == (None, None, None)


def test_backfill_restores_the_amount_from_raw_ai_result(db):
    """백필 뒤에는 읽기 규칙 한 줄로 양이 나온다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = _legacy_user_item(db, meal.id)

    db.execute(text(_backfill_sql()))
    db.expire_all()
    item = db.get(MealItem, item.id)

    assert item.confirmed_amount == Decimal("200.00")
    assert item.confirmed_unit == "g"
    assert _stored_amount(item) == (Decimal("200.00"), Decimal("200.00"), "g")


def test_backfill_leaves_ai_recognised_items_alone(db):
    """`source=MODEL` 은 건드리지 않는다 — 사용자 확인 전이라 NULL 이 맞다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = MealItem(
        meal_id=meal.id,
        original_food_name="김밥",
        display_name="김밥",
        estimated_amount=Decimal("250.00"),
        estimated_unit="g",
        estimated_amount_g=Decimal("250.00"),
        source=MealItemSource.MODEL,
        # AI 원본에도 amount·unit 키가 있다 — source 로 거르지 않으면 여기까지 딸려온다.
        raw_ai_result={"foodName": "김밥", "amount": 250, "unit": "g"},
    )
    db.add(item)
    db.flush()

    db.execute(text(_backfill_sql()))
    db.expire_all()
    item = db.get(MealItem, item.id)

    assert item.confirmed_amount is None
    assert item.confirmed_unit is None


def test_backfill_skips_user_rows_written_after_the_columns_existed(db):
    """이미 `confirmed_amount` 가 있는 행은 덮어쓰지 않는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = MealItem(
        meal_id=meal.id,
        original_food_name="계란",
        display_name="계란",
        confirmed_amount=Decimal("2.00"),
        confirmed_unit="개",
        source=MealItemSource.USER,
        raw_ai_result={"amount": "999", "unit": "g"},
    )
    db.add(item)
    db.flush()

    db.execute(text(_backfill_sql()))
    db.expire_all()
    item = db.get(MealItem, item.id)

    assert (item.confirmed_amount, item.confirmed_unit) == (Decimal("2.00"), "개")
