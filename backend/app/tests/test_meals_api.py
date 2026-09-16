"""/meals 엔드포인트의 HTTP 계약.

응답 래퍼 · camelCase · 인증 이음새 · 404 단일화까지, FE 가 실제로 보는 모양을 검증한다.
"""

import uuid

from app.tests.factories import make_meal, make_user


def test_delete_returns_wrapped_camel_case_body(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    response = client.delete(
        f"/api/v1/meals/{meal.id}", headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    assert data["mealId"] == str(meal.id)
    assert data["deletedAt"] is not None
    assert data["affectedInsights"] == []


def test_delete_unknown_meal_is_404_not_found(client, db):
    user = make_user(db)

    response = client.delete(
        f"/api/v1/meals/{uuid.uuid4()}", headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"


def test_delete_another_users_meal_is_indistinguishable_from_unknown(client, db):
    """소유권 누출 방지: 남의 식사도 '없는 식사'와 같은 응답이어야 한다."""
    owner = make_user(db, "종호")
    stranger = make_user(db, "남의사람")
    meal = make_meal(db, user_id=owner.id)

    real = client.delete(
        f"/api/v1/meals/{meal.id}", headers={"X-User-Id": str(stranger.id)}
    )
    unknown = client.delete(
        f"/api/v1/meals/{uuid.uuid4()}", headers={"X-User-Id": str(stranger.id)}
    )

    assert real.status_code == unknown.status_code == 404
    assert real.json()["error"]["code"] == unknown.json()["error"]["code"]


def test_delete_without_user_header_is_401(client, db):
    """인증 이음새를 타므로 헤더가 없으면 지워지지 않는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    response = client.delete(f"/api/v1/meals/{meal.id}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_deleted_meal_is_gone_from_list_endpoint(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    headers = {"X-User-Id": str(user.id)}

    before = client.get("/api/v1/meals", headers=headers)
    assert [item["mealId"] for item in before.json()["data"]["items"]] == [str(meal.id)]

    client.delete(f"/api/v1/meals/{meal.id}", headers=headers)

    after = client.get("/api/v1/meals", headers=headers)
    assert after.json()["data"]["items"] == []


def test_list_without_user_header_is_401(client, db):
    """식사 기록은 민감 건강정보다. 조회도 인증 이음새를 타야 한다."""
    user = make_user(db)
    make_meal(db, user_id=user.id)

    response = client.get("/api/v1/meals")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_list_only_returns_the_callers_meals(client, db):
    """헤더의 사용자 것만 나온다 — 남의 기록이 섞이지 않는다."""
    owner = make_user(db, "종호")
    stranger = make_user(db, "남의사람")
    mine = make_meal(db, user_id=owner.id)
    make_meal(db, user_id=stranger.id)

    response = client.get("/api/v1/meals", headers={"X-User-Id": str(owner.id)})

    items = response.json()["data"]["items"]
    assert [item["mealId"] for item in items] == [str(mine.id)]
