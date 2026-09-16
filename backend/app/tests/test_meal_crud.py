"""soft delete 의 DB 왕복 테스트.

`test_meal_service.py` 의 delete 테스트는 `soft_delete_meal` 을 monkeypatch 하므로
"service 가 crud 결과를 스키마로 옮긴다" 까지만 검증한다. 이 파일이 검증하는 것은
그 아래 — **행이 실제로 남는지, 소유권 필터가 실제로 도는지, 삭제된 식사가 읽기
경로에서 실제로 빠지는지**다. crud 의 WHERE 조건을 하나라도 지우면 여기가 빨개진다.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.crud import meal as meal_crud
from app.models.meal import Meal
from app.services import meal as meal_service
from app.tests.factories import EATEN_AT, make_meal, make_user


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
