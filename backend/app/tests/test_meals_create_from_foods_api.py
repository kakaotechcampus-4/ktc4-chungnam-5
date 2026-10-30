"""POST /meals 의 JSON 입력 — 검색 탭에서 고른 음식(`items[]`)을 그대로 보내기.

예전 텍스트 입력(`rawText`)을 대신한다. 사진과 달리 AI 분석을 거치지 않는다 —
큐에 아무것도 넣지 않고 곧바로 `REVIEW_REQUIRED` 로 만들어 201 을 준다.
"""

from sqlalchemy import func, select

from app.infra.storage import get_file_storage
from app.main import app
from app.models.meal import Meal, SatietyLog
from app.models.task import Task
from app.tests.factories import make_food_ref, make_user

_EATEN_AT = "2026-10-24T12:30:00+09:00"


def _post(client, *, user_id, json):
    return client.post(
        "/api/v1/meals", headers={"X-User-Id": str(user_id)}, json=json
    )


def _body(items, **extra):
    return {"mealType": "LUNCH", "eatenAt": _EATEN_AT, "items": items, **extra}


def _count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_selected_foods_become_a_review_required_meal_without_analysis(client, db):
    """명세의 요청 예시 그대로. 폴링 안내 없이 201 + REVIEW_REQUIRED 다."""
    user = make_user(db)
    make_food_ref(db, food_ref_id="D000123", name="계란말이")
    make_food_ref(db, food_ref_id="D004512", name="바나나")
    db.commit()

    response = _post(
        client,
        user_id=user.id,
        json=_body(
            [
                {"foodRefId": "D000123", "amount": 120, "unit": "g"},
                {"foodRefId": "D004512", "amount": 100, "unit": "g"},
            ],
            satietyBeforePct=20,
        ),
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["status"] == "REVIEW_REQUIRED"
    assert data["pollIntervalMs"] is None
    assert data["timeoutMs"] is None
    # 분석 작업이 하나도 생기지 않는다.
    assert _count(db, Task) == 0

    meal = db.get(Meal, data["mealId"])
    assert meal.raw_text == "계란말이 120g, 바나나 100g"
    assert meal.image_key is None
    assert db.execute(select(SatietyLog.satiety_before)).scalar_one() == 20


def test_meal_detail_shows_selected_foods_with_public_db_nutrition(client, db):
    """이후 `GET /meals/{mealId}` 는 고른 음식 그대로, 먹은 양으로 환산한 영양정보를 싣는다."""
    user = make_user(db)
    # 100g 기준 50kcal · 나트륨 600mg (factories 기본값)
    make_food_ref(db, food_ref_id="D000123", name="계란말이")
    db.commit()

    created = _post(
        client,
        user_id=user.id,
        json=_body([{"foodRefId": "D000123", "amount": 120, "unit": "g"}]),
    )
    meal_id = created.json()["data"]["mealId"]

    response = client.get(
        f"/api/v1/meals/{meal_id}", headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "REVIEW_REQUIRED"
    assert data["steps"] == created.json()["data"]["steps"]
    [item] = data["items"]
    assert item["displayName"] == "계란말이"
    assert item["amount"] == 120
    assert item["unit"] == "g"
    assert item["matched"] is True
    assert item["nutritionSource"] == "PUBLIC_DB"
    assert item["confidence"] is None
    assert float(item["nutrition"]["kcal"]) == 60
    assert float(item["nutrition"]["sodiumMg"]) == 720


def test_millilitre_is_converted_like_grams(client, db):
    """ml 은 1ml ≈ 1g 로 환산된다(`services.meal.to_grams`) — 영양정보가 빠지지 않는다."""
    user = make_user(db)
    make_food_ref(db, food_ref_id="D009999", name="우유")
    db.commit()

    created = _post(
        client,
        user_id=user.id,
        json=_body([{"foodRefId": "D009999", "amount": 200, "unit": "ml"}]),
    )
    assert created.status_code == 201, created.text

    detail = client.get(
        f"/api/v1/meals/{created.json()['data']['mealId']}",
        headers={"X-User-Id": str(user.id)},
    ).json()["data"]
    [item] = detail["items"]
    assert item["unit"] == "ml"
    assert float(item["nutrition"]["kcal"]) == 100


def test_unknown_food_ref_id_is_404_and_creates_nothing(client, db):
    """고른 음식 중 하나라도 없으면 끼니를 만들지 않는다 — 나머지만 남기면 음식이 조용히 사라진다."""
    user = make_user(db)
    make_food_ref(db, food_ref_id="D000123", name="계란말이")
    db.commit()

    response = _post(
        client,
        user_id=user.id,
        json=_body(
            [
                {"foodRefId": "D000123", "amount": 120, "unit": "g"},
                {"foodRefId": "NOPE", "amount": 100, "unit": "g"},
            ]
        ),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert _count(db, Meal) == 0


def test_raw_text_is_no_longer_accepted(client, db):
    """텍스트 입력은 `items[]` 로 바뀌었다. 옛 요청은 원인이 드러나게 거부하고, 분석도 걸지 않는다."""
    user = make_user(db)
    make_food_ref(db, food_ref_id="D000123", name="계란말이")
    db.commit()

    raw_text_only = {"mealType": "LUNCH", "eatenAt": _EATEN_AT, "rawText": "김밥 한 줄"}
    with_items = _body(
        [{"foodRefId": "D000123", "amount": 120, "unit": "g"}], rawText="계란말이 120g"
    )

    for json in (raw_text_only, with_items):
        response = _post(client, user_id=user.id, json=json)
        assert response.status_code == 422, json
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert "items" in error["message"]

    assert _count(db, Meal) == 0
    assert _count(db, Task) == 0


def test_missing_items_is_422(client, db):
    user = make_user(db)

    response = _post(
        client, user_id=user.id, json={"mealType": "LUNCH", "eatenAt": _EATEN_AT}
    )

    assert response.status_code == 422


def test_invalid_items_are_422(client, db):
    """빈 목록 · 50개 초과 · 0 이하 양 · g/ml 이외 단위 · foodRefId 누락."""
    user = make_user(db)
    item = {"foodRefId": "D000123", "amount": 120, "unit": "g"}
    invalid = [
        [],
        [item] * 51,
        [{**item, "amount": 0}],
        [{**item, "unit": "개"}],
        [{"amount": 120, "unit": "g"}],
        "계란말이",
    ]

    for items in invalid:
        response = _post(client, user_id=user.id, json=_body(items))
        assert response.status_code == 422, items
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    assert _count(db, Meal) == 0


class _MemoryStorage:
    def __init__(self):
        self.saved: dict[str, bytes] = {}

    def save(self, key: str, data: bytes) -> None:
        self.saved[key] = data


def test_photo_still_goes_to_analysis(client, db):
    """사진 입력은 그대로 202 + 분석 작업이다. 큐 payload 에 `rawText` 는 더 싣지 않는다."""
    storage = _MemoryStorage()
    app.dependency_overrides[get_file_storage] = lambda: storage
    user = make_user(db)

    response = client.post(
        "/api/v1/meals",
        headers={"X-User-Id": str(user.id)},
        data={"mealType": "LUNCH", "eatenAt": _EATEN_AT},
        files={"image": ("lunch.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg")},
    )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["status"] == "ANALYZING"
    assert data["pollIntervalMs"] == 1500
    [key] = storage.saved
    task = db.execute(select(Task)).scalar_one()
    assert task.payload["imageKey"] == key
    assert "rawText" not in task.payload
