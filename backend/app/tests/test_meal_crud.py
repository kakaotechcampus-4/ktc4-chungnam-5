"""soft delete 의 DB 왕복 테스트.

`test_meal_service.py` 의 delete 테스트는 `soft_delete_meal` 을 monkeypatch 하므로
"service 가 crud 결과를 스키마로 옮긴다" 까지만 검증한다. 이 파일이 검증하는 것은
그 아래 — **행이 실제로 남는지, 소유권 필터가 실제로 도는지, 삭제된 식사가 읽기
경로에서 실제로 빠지는지**다. crud 의 WHERE 조건을 하나라도 지우면 여기가 빨개진다.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.crud import meal as meal_crud
from app.models.enums import MealItemSource, MealStatus
from app.models.meal import Meal
from app.services import meal as meal_service
from app.tests.factories import EATEN_AT, make_food_ref, make_meal, make_user


def _row_count(db, meal_id: uuid.UUID) -> int:
    return len(db.execute(select(Meal.id).where(Meal.id == meal_id)).all())


def test_soft_delete_keeps_row_and_stamps_deleted_at(db):
    """hard delete 가 아니다 — 행은 남고 deleted_at 만 채워져야 한다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    deleted = meal_crud.soft_delete_meal(db, user_id=user.id, meal_id=meal.id)
    db.flush()

    assert deleted is not None
    assert deleted.deleted_at is not None
    assert _row_count(db, meal.id) == 1

    # ORM 캐시가 아니라 DB 가 실제로 그렇게 들고 있는지 확인한다.
    db.expire_all()
    stored = db.execute(select(Meal).where(Meal.id == meal.id)).scalar_one()
    assert stored.deleted_at is not None


def test_soft_delete_returns_none_for_another_users_meal(db):
    """남의 식사는 존재하더라도 지울 수 없고, 행도 그대로여야 한다."""
    owner = make_user(db, "종호")
    stranger = make_user(db, "남의사람")
    meal = make_meal(db, user_id=owner.id)

    assert meal_crud.soft_delete_meal(db, user_id=stranger.id, meal_id=meal.id) is None

    db.expire_all()
    stored = db.execute(select(Meal).where(Meal.id == meal.id)).scalar_one()
    assert stored.deleted_at is None


def test_soft_delete_returns_none_for_unknown_id(db):
    user = make_user(db)
    assert meal_crud.soft_delete_meal(db, user_id=user.id, meal_id=uuid.uuid4()) is None


def test_soft_delete_twice_returns_none_the_second_time(db):
    """이미 삭제된 식사는 '지울 게 없다' — 없는 식사와 동일하게 None."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    assert meal_crud.soft_delete_meal(db, user_id=user.id, meal_id=meal.id) is not None
    db.flush()

    assert meal_crud.soft_delete_meal(db, user_id=user.id, meal_id=meal.id) is None


def test_deleted_meal_disappears_from_list_meals(db):
    """읽기 경로에서 빠지는 것까지가 soft delete 다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    before = meal_service.list_meals(db, user_id=user.id, cursor=None, limit=20)
    assert [item.meal_id for item in before.items] == [meal.id]

    meal_crud.soft_delete_meal(db, user_id=user.id, meal_id=meal.id)
    db.flush()

    after = meal_service.list_meals(db, user_id=user.id, cursor=None, limit=20)
    assert after.items == []
    assert after.has_more is False


def test_list_meals_keeps_other_meals_after_one_is_deleted(db):
    """필터가 너무 넓어서 멀쩡한 식사까지 사라지는 반대 방향의 실수도 잡는다."""
    user = make_user(db)
    kept = make_meal(db, user_id=user.id, eaten_at=EATEN_AT)
    removed = make_meal(
        db, user_id=user.id, eaten_at=datetime(2026, 8, 22, 19, 0, tzinfo=UTC)
    )

    meal_crud.soft_delete_meal(db, user_id=user.id, meal_id=removed.id)
    db.flush()

    listed = meal_service.list_meals(db, user_id=user.id, cursor=None, limit=20)
    assert [item.meal_id for item in listed.items] == [kept.id]


def test_mark_recalculating_moves_meal_back_to_analyzing(db):
    """음식을 고치면 확인 화면에서 분석 중으로 되돌아간다 — 명세의 상태 전이."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)

    meal_crud.mark_recalculating(db, meal)

    assert meal.status is MealStatus.ANALYZING
    assert meal.is_recalculation is True


def test_get_owned_meal_returns_the_meal_for_its_owner(db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    found = meal_crud.get_owned_meal(db, user_id=user.id, meal_id=meal.id)

    assert found is not None
    assert found.id == meal.id


def test_get_owned_meal_returns_none_for_another_users_meal(db):
    owner = make_user(db, "종호")
    stranger = make_user(db, "남의사람")
    meal = make_meal(db, user_id=owner.id)

    assert meal_crud.get_owned_meal(db, user_id=stranger.id, meal_id=meal.id) is None


def test_get_owned_meal_returns_none_for_deleted_meal(db):
    """soft delete 된 식사는 행이 남아 있다 — WHERE 에서 빠뜨리면 지운 식사를 고칠 수 있다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    meal_crud.soft_delete_meal(db, user_id=user.id, meal_id=meal.id)
    db.flush()

    assert meal_crud.get_owned_meal(db, user_id=user.id, meal_id=meal.id) is None


def test_add_item_stores_a_user_sourced_row(db):
    """사용자가 직접 넣은 음식은 AI 인식 결과와 구분돼야 한다 — worker 가 재분석할 때
    `source=MODEL` 항목만 지우기 때문이다(`jobs/analyze_meal.py` 6단계)."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    item = meal_crud.add_item(
        db,
        meal_id=meal.id,
        display_name="미역국",
        amount=Decimal("200"),
        unit="g",
        amount_g=Decimal("200.00"),
        food_ref_id=None,
    )

    assert item.meal_id == meal.id
    assert item.display_name == "미역국"
    assert item.original_food_name == "미역국"
    assert item.source is MealItemSource.USER
    assert item.confirmed_amount_g == Decimal("200.00")
    assert (item.confirmed_amount, item.confirmed_unit) == (Decimal("200"), "g")
    assert item.estimated_amount_g is None
    assert item.confidence is None
    # AI 가 인식한 적이 없는 항목이다 — AI 원본 자리는 비어 있어야 한다.
    assert item.raw_ai_result is None


def test_add_item_links_the_matched_food_ref(db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    food_ref = make_food_ref(db, food_ref_id="KFD_MIYEOK", name="미역국")

    item = meal_crud.add_item(
        db,
        meal_id=meal.id,
        display_name="미역국",
        amount=Decimal("200"),
        unit="g",
        amount_g=Decimal("200.00"),
        food_ref_id=food_ref.id,
    )

    assert item.food_ref_id == "KFD_MIYEOK"


def test_new_meal_starts_analyzing_and_not_recalculating(db):
    """DB 기본값 회귀 방지.

    대부분의 테스트가 `make_meal(status=...)` 로 상태를 명시해 INSERT 하므로,
    server_default 를 실제로 태우는 경로는 여기 하나뿐이다. 기본값이 바뀌면
    `POST /meals` 로 만든 식사가 조용히 엉뚱한 상태에서 출발한다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=None)
    db.refresh(meal)

    assert meal.status is MealStatus.ANALYZING
    assert meal.is_recalculation is False
