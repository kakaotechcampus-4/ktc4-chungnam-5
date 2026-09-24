"""DELETE /meals/{mealId}/items/{itemId} 의 HTTP 계약.

확인 화면에서 사용자가 AI 가 잘못 인식한 음식을 빼는 경로다. 추가(POST)는
`test_meal_items_api.py`, 수정(PATCH)은 `test_meal_items_update_api.py` 에 있다.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import MealItemSource, MealStatus
from app.models.meal import MealItem, UserCorrection
from app.models.task import Task
from app.tests.factories import make_meal, make_meal_item, make_user


def _items(db, meal_id: uuid.UUID) -> list[MealItem]:
    return list(
        db.execute(select(MealItem).where(MealItem.meal_id == meal_id)).scalars().all()
    )


def _corrections(db, item_id: uuid.UUID) -> list[UserCorrection]:
    return list(
        db.execute(
            select(UserCorrection).where(UserCorrection.meal_item_id == item_id)
        )
        .scalars()
        .all()
    )


def _assert_not_found(response) -> None:
    """엔드포인트가 낸 404 인지 확인한다 — 라우트가 없어서 난 404 가 아니라.

    둘은 status 도 `error.code` 도 똑같다(`core.response` 의 HTTPException 핸들러가
    404 를 그대로 `NOT_FOUND` 로 옮긴다). 그래서 이 파일의 404 테스트는 라우트를
    지워도 전부 통과한다 — 소유권 누출을 막는지 아무것도 증명하지 못한다.

    가르는 건 메시지다. 라우트 미스매치는 Starlette 의 기본 detail 인 `"Not Found"`
    가 그대로 나오고, `services.meal` 이 던진 것은 한국어 도메인 메시지다.
    """
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] != "Not Found", (
        "라우트가 없어서 난 404 다 — 엔드포인트가 낸 404 가 아니다"
    )


def test_delete_item_returns_recalculating_status(client, db):
    """명세서(contracts/API.md DELETE /meals/{mealId}/items/{itemId})의 응답 모양 그대로."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, display_name="참치김밥")
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    assert data["status"] == "ANALYZING"
    assert data["isRecalculation"] is True


def test_delete_response_has_no_steps(client, db):
    """PATCH 와 달리 명세서가 DELETE 응답에 `steps` 를 주지 않는다.

    형제 응답 스키마(`MealItemsUpdateResponse`)를 그대로 재사용하면 명세에 없는
    필드가 조용히 따라 나간다 — FE 가 그걸 보고 폴링을 시작할 근거가 된다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    assert "steps" not in response.json()["data"]


def test_delete_removes_the_row(client, db):
    """soft delete 가 아니라 행을 지운다 — `meal_items` 에는 `deleted_at` 이 없다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    assert _items(db, meal.id) == []


def test_delete_leaves_the_other_items_untouched(client, db):
    """한 항목을 지운다고 나머지가 흔들리면 안 된다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    gimbap = make_meal_item(db, meal_id=meal.id, display_name="참치김밥")
    rice = make_meal_item(db, meal_id=meal.id, display_name="현미밥")
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{gimbap.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    remaining = _items(db, meal.id)
    assert [item.id for item in remaining] == [rice.id]
    assert remaining[0].display_name == "현미밥"


def test_deleting_the_last_item_is_allowed(client, db):
    """마지막 한 개도 지울 수 있다.

    막으면 "AI 가 잘못 인식한 유일한 항목을 지우고 올바른 걸 넣기" 가 POST 를 먼저
    해야 하는 순서 제약이 된다. 식사를 통째로 지우는 경로는
    `DELETE /meals/{mealId}` 로 따로 있다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    assert _items(db, meal.id) == []


def test_meal_with_no_items_left_still_lists_with_an_empty_name(client, db):
    """마지막 항목을 지운 식사가 `GET /meals` 에서 어떻게 보이는지 고정한다.

    `crud.get_display_names` 는 항목이 없는 식사를 dict 에 넣지 않으므로
    `displayName` 이 `""` 로 나간다(`services.meal.list_meals` 의 `.get(id, "")`).
    이건 이 엔드포인트가 만든 상태가 아니다 — `FAILED` 식사도 항목이 0 개라 목록은
    전부터 이 경로를 탄다. 그래도 여기서 못 박는 건, **마지막 항목 삭제를 허용하기로
    한 결정이 이 표시를 정상 동작으로 받아들인다**는 뜻이기 때문이다. 제목 없는
    카드가 FE 에서 문제가 되면 고칠 자리는 `list_meals` 이지 이 엔드포인트가 아니다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, display_name="참치김밥")
    db.commit()
    headers = {"X-User-Id": str(user.id)}

    deleted = client.delete(f"/api/v1/meals/{meal.id}/items/{item.id}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    listed = client.get("/api/v1/meals", headers=headers)

    assert listed.status_code == 200, listed.text
    (row,) = listed.json()["data"]["items"]
    assert row["mealId"] == str(meal.id)
    assert row["displayName"] == ""


def test_delete_marks_an_evaluated_meal_for_recalculation(client, db):
    """평가가 끝난 식사에서 음식을 빼면 점수가 더는 유효하지 않다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    db.refresh(meal)
    assert meal.status is MealStatus.ANALYZING
    assert meal.is_recalculation is True


def test_user_can_keep_deleting_while_recalculation_is_pending(client, db):
    """첫 삭제가 만든 `ANALYZING` 이 두 번째 삭제를 막으면 안 된다.

    `ANALYZING` 을 통째로 막으면 사용자가 방금 스스로 만든 상태에 갇힌다
    (`services.meal._is_editable` 참고).
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    first = make_meal_item(db, meal_id=meal.id, display_name="참치김밥")
    second = make_meal_item(db, meal_id=meal.id, display_name="현미밥")
    db.commit()
    headers = {"X-User-Id": str(user.id)}

    deletions = [
        client.delete(f"/api/v1/meals/{meal.id}/items/{item.id}", headers=headers)
        for item in (first, second)
    ]

    assert [r.status_code for r in deletions] == [200, 200]
    assert _items(db, meal.id) == []


def test_user_added_item_can_be_deleted(client, db):
    """사용자가 직접 넣은 음식도 뺄 수 있다 — 출처로 가르지 않는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="미역국",
        source=MealItemSource.USER,
        confidence=None,
        estimated_amount=None,
        estimated_unit=None,
        confirmed_amount=Decimal("200.00"),
        confirmed_unit="g",
    )
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    assert _items(db, meal.id) == []


def test_deleting_an_item_takes_its_corrections_with_it(client, db):
    """hard delete 의 대가를 코드로 고정한다.

    `user_corrections.meal_item_id` 가 `ondelete=CASCADE` 라 그 항목의 수정 이력이
    함께 사라진다 — AI 인식 성능 평가에서 이 항목은 통째로 빠진다. soft delete 로
    바꾸는 선택을 한다면 가장 먼저 깨질 테스트가 여기다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, display_name="김밥")
    db.commit()
    headers = {"X-User-Id": str(user.id)}

    edit = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers=headers,
        json={
            "items": [
                {
                    "itemId": str(item.id),
                    "displayName": "참치김밥",
                    "amount": 220,
                    "unit": "g",
                }
            ]
        },
    )
    assert edit.status_code == 200, edit.text
    assert len(_corrections(db, item.id)) == 1

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}", headers=headers
    )

    assert response.status_code == 200, response.text
    assert _corrections(db, item.id) == []


def test_delete_never_touches_the_task_queue(client, db):
    """음식 삭제는 비동기 작업을 만들지 않는다 — 형제(POST · PATCH)와 같은 결정이다.

    사용자가 뺀 음식이라 AI 에게 물을 것이 없고, 다시 계산할 Q/Q/S 는 순수
    함수다(README 절대 규칙 2). 큐가 테이블이라 행이 생겼는지 직접 본다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    queued = db.execute(select(Task)).scalars().all()
    assert queued == [], (
        "음식 삭제 경로에서 큐에 작업을 넣었다 "
        "— `delete_meal_item` 독스트링의 '여기서 비동기 작업을 만들지 않는다' 참고"
    )


def test_delete_of_unknown_item_leaves_the_meal_untouched(client, db):
    """404 는 부작용 없이 끝나야 한다 — 상태를 먼저 뒤집고 나중에 404 를 내면 안 된다.

    지금은 `get_item` → 404 가 `mark_recalculating` 보다 앞이라 안전하다. 그 **순서**
    를 잡아 두는 것이 이 테스트다. 누가 "어차피 재계산 표시는 항상 하니까" 하며
    순서를 바꾸면, 없는 itemId 를 보낸 잘못된 요청 하나가 `EVALUATED` 식사를 조용히
    `ANALYZING` 으로 되돌려 사용자가 점수를 잃는다 — 404 를 받았는데도.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{uuid.uuid4()}",
        headers={"X-User-Id": str(user.id)},
    )

    _assert_not_found(response)
    db.refresh(meal)
    assert meal.status is MealStatus.EVALUATED
    assert meal.is_recalculation is False
    assert [row.id for row in _items(db, meal.id)] == [item.id]


def test_item_belonging_to_another_meal_is_404(client, db):
    """itemId 만 맞으면 다른 식사 항목까지 지워지는 일이 없어야 한다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    other_meal = make_meal(db, user_id=user.id)
    outsider = make_meal_item(db, meal_id=other_meal.id, display_name="현미밥")
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{outsider.id}",
        headers={"X-User-Id": str(user.id)},
    )

    _assert_not_found(response)
    assert [item.id for item in _items(db, other_meal.id)] == [outsider.id]


def test_deleting_the_same_item_twice_is_404(client, db):
    """이미 지운 항목은 이 식사의 항목이 아니다 — 처음 보는 itemId 와 같게 응답한다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()
    headers = {"X-User-Id": str(user.id)}
    path = f"/api/v1/meals/{meal.id}/items/{item.id}"

    assert client.delete(path, headers=headers).status_code == 200

    response = client.delete(path, headers=headers)

    _assert_not_found(response)


def test_delete_on_unknown_meal_is_404(client, db):
    user = make_user(db)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{uuid.uuid4()}/items/{uuid.uuid4()}",
        headers={"X-User-Id": str(user.id)},
    )

    _assert_not_found(response)


def test_delete_on_another_users_meal_is_indistinguishable_from_unknown(client, db):
    """남의 mealId 는 존재한다는 사실조차 흘리지 않는다."""
    owner = make_user(db, nickname="주인")
    stranger = make_user(db, nickname="남")
    meal = make_meal(db, user_id=owner.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(stranger.id)},
    )

    _assert_not_found(response)
    assert [row.id for row in _items(db, meal.id)] == [item.id]


def test_delete_on_deleted_meal_is_404(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()
    headers = {"X-User-Id": str(user.id)}

    assert client.delete(f"/api/v1/meals/{meal.id}", headers=headers).status_code == 200

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}", headers=headers
    )

    _assert_not_found(response)


def test_delete_without_user_header_is_401(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(f"/api/v1/meals/{meal.id}/items/{item.id}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("status", [MealStatus.ANALYZING, MealStatus.FAILED])
def test_delete_is_conflict_while_meal_is_not_editable(client, db, status):
    """최초 분석 중에는 Worker 가 `meal_items` 를 갈아엎고 있고, 실패한 식사는 뺄 것이 없다.

    여기 `ANALYZING` 은 `is_recalculation=False` — 최초 분석이다. 사용자 수정으로
    인한 `ANALYZING` 은 반대로 허용된다
    (`test_user_can_keep_deleting_while_recalculation_is_pending` 참고).
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=status)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/{item.id}",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert [row.id for row in _items(db, meal.id)] == [item.id]
    # 409 도 부작용 없이 끝나야 한다 — 거절해 놓고 상태만 바꾸면 최초 분석 중인
    # 식사의 `is_recalculation` 이 뒤집혀 워커와 사용자의 해소 주체가 뒤바뀐다.
    db.refresh(meal)
    assert meal.status is status
    assert meal.is_recalculation is False


def test_delete_rejects_a_malformed_item_id(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    db.commit()

    response = client.delete(
        f"/api/v1/meals/{meal.id}/items/not-a-uuid",
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
