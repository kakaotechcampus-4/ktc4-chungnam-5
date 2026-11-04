"""POST /meals 의 "식사 안 함"(`skipped: true`)과 그 끼니의 조회.

FE 명세(식사 안 함 입력):
  - `{ mealType, eatenAt, skipped: true }` 만으로 등록한다 — 사진 · items 없음
  - 음식 분석을 만들지 않고 곧바로 `EVALUATED`, 끼니 피드백 작업만 넣는다 (201)
  - 점수는 매기지 않는다 — 세 축 모두 null (0 이 아니다). 평균에서 빠진다
  - 조회 응답(상세 · 목록 · 홈 · 캘린더)에 `skipped` 가 실린다
  - 고칠 수도 확정할 수도 없다 — 바꾸려면 지우고 다시 등록한다

틀리기 쉬운 것:
  - 점수 행을 만들어 평균에 0 점이 섞이는 것
  - 상태를 `EVALUATED` 가 아닌 것으로 두어 하루 피드백 비교에서 빠지는 것
  - 안 먹은 끼니에 음식 · 식전 포만감이 함께 와도 조용히 받는 것
"""

from datetime import datetime

from sqlalchemy import func, select

from app.core.time import KST
from app.models.enums import MealType
from app.models.evaluation import QQSEvaluation
from app.models.meal import Meal, MealItem, SatietyLog
from app.models.task import Task
from app.tests.factories import make_food_ref, make_meal, make_qqs_evaluation, make_user

_EATEN_AT = "2026-10-30T12:00:00+09:00"


def _headers(user) -> dict:
    return {"X-User-Id": str(user.id)}


def _post_skipped(client, user, **extra):
    body = {"mealType": "LUNCH", "eatenAt": _EATEN_AT, "skipped": True, **extra}
    return client.post("/api/v1/meals", headers=_headers(user), json=body)


def _count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def _created_id(client, user, **extra) -> str:
    response = _post_skipped(client, user, **extra)
    assert response.status_code == 201, response.text
    return response.json()["data"]["mealId"]


# ─────────────────────────── 등록 ───────────────────────────


def test_skipped_meal_is_created_evaluated_without_analysis(client, db):
    """201 + EVALUATED, steps 는 비고 폴링 안내는 null 이다 — FE 명세의 등록 응답 예시."""
    user = make_user(db)
    db.commit()

    response = _post_skipped(client, user)

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["status"] == "EVALUATED"
    assert data["steps"] == []
    assert data["pollIntervalMs"] is None
    assert data["timeoutMs"] is None

    meal = db.get(Meal, data["mealId"])
    assert meal.skipped is True
    assert meal.image_key is None
    assert meal.raw_text is None
    assert _count(db, MealItem) == 0


def test_skipped_meal_enqueues_only_the_meal_feedback(client, db):
    """음식 분석(`meal.analyze`)은 없고 끼니 피드백(`feedback.meal`)만 들어간다."""
    user = make_user(db)
    db.commit()

    meal_id = _created_id(client, user)

    tasks = db.execute(select(Task.type, Task.payload)).all()
    assert [(t.type, t.payload) for t in tasks] == [("feedback.meal", {"mealId": meal_id})]


def test_skipped_meal_has_no_score_row(client, db):
    """점수 행이 없어야 평균 · 장기 피드백 집계(JOIN)에서 저절로 빠진다."""
    user = make_user(db)
    db.commit()

    _created_id(client, user)

    assert _count(db, QQSEvaluation) == 0


def test_skipped_meal_rejects_items_and_satiety_before(client, db):
    """안 먹은 끼니에 음식 · 식전 포만감이 오면 어느 쪽이 맞는지 모른다 — 422."""
    user = make_user(db)
    make_food_ref(db, food_ref_id="D000123", name="계란말이")
    db.commit()

    with_items = _post_skipped(
        client, user, items=[{"foodRefId": "D000123", "amount": 120, "unit": "g"}]
    )
    with_satiety = _post_skipped(client, user, satietyBeforePct=30)

    assert with_items.status_code == 422, with_items.text
    assert with_satiety.status_code == 422, with_satiety.text
    assert _count(db, Meal) == 0


def test_skipped_must_be_a_boolean(client, db):
    user = make_user(db)
    db.commit()

    response = _post_skipped(client, user, skipped="yes")

    assert response.status_code == 422, response.text


def test_skipped_meal_still_rejects_raw_text(client, db):
    user = make_user(db)
    db.commit()

    response = _post_skipped(client, user, rawText="안 먹음")

    assert response.status_code == 422, response.text


def test_skipped_false_falls_back_to_selected_foods(client, db):
    """`skipped: false` 는 지금과 같다 — items 가 필요하다."""
    user = make_user(db)
    db.commit()

    response = _post_skipped(client, user, skipped=False)

    assert response.status_code == 422, response.text
    assert "items" in response.text


# ─────────────────────────── 조회 ───────────────────────────


def test_detail_shows_skipped_with_null_scores_and_no_items(client, db):
    """FE 명세의 상세 예시 — 점수 세 축 모두 null, 음식 없음."""
    user = make_user(db)
    db.commit()
    meal_id = _created_id(client, user)

    response = client.get(f"/api/v1/meals/{meal_id}", headers=_headers(user))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["skipped"] is True
    assert data["status"] == "EVALUATED"
    assert data["mealType"] == "LUNCH"
    assert data["items"] == []
    assert data["steps"] == []
    assert data["nutrients"] == []
    assert data["scores"] == {"quantity": None, "quality": None, "satiety": None}
    # 문장은 워커가 아직 안 만들었다.
    assert data["feedback"] is None


def test_normal_meal_detail_is_not_skipped(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    db.commit()

    response = client.get(f"/api/v1/meals/{meal.id}", headers=_headers(user))

    assert response.json()["data"]["skipped"] is False


def test_feedback_is_pending_until_the_worker_writes_it(client, db):
    """FE 는 등록 직후 `GET /meals/{id}/feedback` 을 폴링한다."""
    user = make_user(db)
    db.commit()
    meal_id = _created_id(client, user)

    response = client.get(f"/api/v1/meals/{meal_id}/feedback", headers=_headers(user))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["feedbackStatus"] == "PENDING"


def test_list_marks_skipped_meals_with_null_scores(client, db):
    user = make_user(db)
    eaten = make_meal(db, user_id=user.id, eaten_at=datetime(2026, 10, 30, 8, 0, tzinfo=KST))
    make_qqs_evaluation(db, meal_id=eaten.id, quantity_score=80, quality_score=70, satiety_score=60)
    db.commit()
    skipped_id = _created_id(client, user)

    response = client.get("/api/v1/meals", headers=_headers(user))

    items = {item["mealId"]: item for item in response.json()["data"]["items"]}
    assert items[skipped_id]["skipped"] is True
    assert items[skipped_id]["scores"] is None
    assert items[str(eaten.id)]["skipped"] is False


def test_calendar_lists_skipped_meal_types_and_excludes_them_from_averages(client, db):
    """식사 안 함은 기록한 끼니라 `recordedMealTypes` 에도 있고, 평균에는 섞이지 않는다."""
    user = make_user(db)
    eaten = make_meal(
        db,
        user_id=user.id,
        eaten_at=datetime(2026, 10, 30, 8, 0, tzinfo=KST),
        meal_type=MealType.BREAKFAST,
    )
    make_qqs_evaluation(db, meal_id=eaten.id, quantity_score=80, quality_score=70, satiety_score=60)
    db.commit()
    _created_id(client, user)

    response = client.get(
        "/api/v1/meals/calendar", params={"month": "2026-10"}, headers=_headers(user)
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    (day,) = data["days"]
    assert set(day["recordedMealTypes"]) == {"BREAKFAST", "LUNCH"}
    assert day["count"] == 2
    assert day["skippedMealTypes"] == ["LUNCH"]
    assert data["summary"]["avgScores"] == {"quantity": 80, "quality": 70, "satiety": 60}


def test_calendar_day_without_skipped_meals_has_an_empty_list(client, db):
    user = make_user(db)
    make_meal(db, user_id=user.id, eaten_at=datetime(2026, 10, 30, 8, 0, tzinfo=KST))
    db.commit()

    response = client.get(
        "/api/v1/meals/calendar", params={"month": "2026-10"}, headers=_headers(user)
    )

    assert response.json()["data"]["days"][0]["skippedMealTypes"] == []


# ─────────────────────────── 수정 · 확정 · 삭제 ───────────────────────────


def test_skipped_meal_cannot_be_confirmed(client, db):
    """이미 EVALUATED 지만 확정하면 빈 음식으로 점수 행이 생긴다 — 409."""
    user = make_user(db)
    db.commit()
    meal_id = _created_id(client, user)

    response = client.post(
        f"/api/v1/meals/{meal_id}/confirm",
        headers=_headers(user),
        json={"satietyAfterPct": 50},
    )

    assert response.status_code == 409, response.text
    assert _count(db, QQSEvaluation) == 0


def test_skipped_meal_cannot_get_items(client, db):
    """수정 API 는 없다 — 바꾸려면 지우고 다시 등록한다."""
    user = make_user(db)
    db.commit()
    meal_id = _created_id(client, user)

    response = client.post(
        f"/api/v1/meals/{meal_id}/items",
        headers=_headers(user),
        json={"displayName": "김밥", "amount": 1, "unit": "줄"},
    )

    assert response.status_code == 409, response.text
    db.expire_all()
    meal = db.get(Meal, meal_id)
    assert meal.status.value == "EVALUATED"
    assert _count(db, MealItem) == 0


def test_skipped_meal_can_be_deleted_and_registered_again(client, db):
    """식사 안 함을 취소하면 삭제 후 다시 등록한다."""
    user = make_user(db)
    db.commit()
    meal_id = _created_id(client, user)

    deleted = client.delete(f"/api/v1/meals/{meal_id}", headers=_headers(user))
    again = _post_skipped(client, user)

    assert deleted.status_code == 200, deleted.text
    assert again.status_code == 201, again.text
    listed = client.get("/api/v1/meals", headers=_headers(user)).json()["data"]["items"]
    assert [item["mealId"] for item in listed] == [again.json()["data"]["mealId"]]


def test_skipped_meal_has_no_satiety_log(client, db):
    user = make_user(db)
    db.commit()

    _created_id(client, user)

    assert _count(db, SatietyLog) == 0
