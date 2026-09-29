"""PUT /meals/{mealId}/items/{itemId}/nutrition 의 HTTP 계약.

이름 매칭이 실패해 `matched: false` 로 나간 항목에 사용자가 영양정보를 붙이는
폴백의 마지막 단계다. 첫 단계(`GET /nutrition/candidates`)의 계약은
`test_nutrition_candidates_api.py` 에 있다.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.enums import MealStatus
from app.tests.factories import make_food_ref, make_meal, make_meal_item, make_user


def _put(client, *, user_id, meal_id, item_id, json):
    return client.put(
        f"/api/v1/meals/{meal_id}/items/{item_id}/nutrition",
        headers={"X-User-Id": str(user_id)},
        json=json,
    )


def test_selecting_a_candidate_scales_the_public_db_values_to_the_eaten_amount(
    client, db
):
    """명세서(contracts/API.md)의 후보 선택 요청 그대로.

    후보의 영양성분은 `servingSizeG` 기준량 값이라(`schemas.nutrition.FoodCandidate`)
    먹은 양만큼 환산해서 내보내야 한다 — 100g 기준 50kcal 짜리를 250g 먹었으면 125다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    # 기본값이 "참치김밥 250g" 이고 공공 DB 링크가 없다 — 폴백으로 오는 모양이다.
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    assert data["itemId"] == str(item.id)
    assert data["matched"] is True
    assert data["nutritionSource"] == "PUBLIC_DB"
    assert data["nutrition"] == {
        "kcal": 125.0,
        "proteinG": 7.5,
        "fatG": 3.75,
        "carbG": 10.0,
        "fiberG": 1.25,
        "sodiumMg": 1500.0,
    }
    # 명세서가 이 응답에도 식사 전체의 상태를 함께 싣는다 — 형제 엔드포인트와 같다.
    assert data["status"] == "ANALYZING"
    assert data["isRecalculation"] is True

    db.refresh(item)
    assert item.food_ref_id == "KFD_01023"


_MANUAL = {
    "kcal": 150,
    "proteinG": 13,
    "fatG": 11,
    "carbG": 1,
    "fiberG": 0,
    "sodiumMg": 120,
}


def test_direct_input_is_returned_as_given_without_scaling(client, db):
    """`manual` 은 **섭취량 기준 총량**이다 — 기준량이 아니라서 환산하지 않는다.

    후보 선택과 다른 점이 이것이다. 사용자는 자기가 먹은 만큼의 값을 적는다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"manual": _MANUAL},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["matched"] is True
    assert data["nutritionSource"] == "USER_INPUT"
    assert data["nutrition"] == {
        "kcal": 150.0,
        "proteinG": 13.0,
        "fatG": 11.0,
        "carbG": 1.0,
        "fiberG": 0.0,
        "sodiumMg": 120.0,
    }

    db.refresh(item)
    assert item.manual_kcal == Decimal("150.000")
    assert item.manual_sodium_mg == Decimal("120.000")


def test_direct_input_wins_over_the_public_db_link(client, db):
    """직접 입력이 있으면 공공 DB 환산보다 그쪽이 먼저다.

    링크는 끊지 않는다 — 사용자가 나중에 직접 입력을 지우면 되돌아갈 자리이고,
    "어느 음식으로 봤는가" 는 그 자체로 남길 값이다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    # 250g × (50kcal / 100g) = 125kcal — 직접 입력이 없으면 이 값이 나간다.
    item = make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_01023")
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"manual": _MANUAL},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["nutritionSource"] == "USER_INPUT"
    assert data["nutrition"]["kcal"] == 150.0

    db.refresh(item)
    assert item.food_ref_id == "KFD_01023"


def test_direct_input_may_omit_nutrients_the_user_does_not_know(client, db):
    """사용자가 모르는 성분은 비워 둘 수 있다 — 지어내지 않고 null 로 나간다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"manual": {"kcal": 150}},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["matched"] is True
    assert data["nutritionSource"] == "USER_INPUT"
    assert data["nutrition"] == {
        "kcal": 150.0,
        "proteinG": None,
        "fatG": None,
        "carbG": None,
        "fiberG": None,
        "sodiumMg": None,
    }


def test_picking_a_candidate_clears_an_earlier_direct_input(client, db):
    """직접 입력했다가 마음을 바꿔 후보를 고르는 경로.

    지우지 않으면 직접 입력이 계속 이겨(`test_direct_input_wins_over_the_public_db_link`)
    **후보 선택이 아무 일도 하지 않은 것처럼 보인다** — 200 을 받고도 값이 그대로다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"manual": _MANUAL},
    )
    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["nutritionSource"] == "PUBLIC_DB"
    assert data["nutrition"]["kcal"] == 125.0

    db.refresh(item)
    assert item.manual_kcal is None


def test_picking_a_candidate_for_an_unconvertible_amount_says_why_it_is_empty(
    client, db
):
    """"계란 2개" 는 후보를 골라도 환산할 근거가 없다 — 폴백의 대표 사례다.

    기준량(`servingSizeG`) 대비 비례 계산이 불가능하므로 영양정보를 만들 수 없다.
    지어내지 않고(`services.nutrition._nutrition_from_food_ref`) 비워 보내되,
    **왜 비었는지**를 명세서의 코드로 알린다 — FE 는 `error.code` 로 분기한다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="삶은 계란",
        food_ref_id=None,
        estimated_amount=Decimal("2.00"),
        estimated_unit="개",
    )
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # 요청 자체는 성공했다 — 결과가 비어 있을 뿐이다(`core.response.ok_with_code`).
    assert body["success"] is True
    assert body["error"]["code"] == "NUTRITION_NOT_MATCHED"

    data = body["data"]
    assert data["matched"] is False
    assert data["nutrition"] is None
    assert data["nutritionSource"] is None

    # 링크는 남긴다 — 사용자가 나중에 양을 g 으로 고치면 곧바로 환산된다.
    db.refresh(item)
    assert item.food_ref_id == "KFD_01023"


def test_unknown_food_ref_id_is_not_found(client, db):
    """공공 DB 에 없는 `foodRefId`. 환산 실패(200)와 갈라야 한다 — 이건 클라이언트 오류다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_DOES_NOT_EXIST"},
    )

    assert response.status_code == 404, response.text
    db.refresh(item)
    assert item.food_ref_id is None


def test_sending_both_branches_is_rejected(client, db):
    """후보 선택과 직접 입력 중 어느 쪽을 쓸지 서버가 정할 근거가 없다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023", "manual": _MANUAL},
    )

    assert response.status_code == 422, response.text


def test_sending_neither_branch_is_rejected(client, db):
    """빈 요청은 폴백을 해소하지 못한 채 200 을 받는다 — 거절한다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client, user_id=user.id, meal_id=meal.id, item_id=item.id, json={}
    )

    assert response.status_code == 422, response.text


def test_direct_input_with_every_nutrient_empty_is_rejected(client, db):
    """모양은 갖췄지만 값이 하나도 없는 직접 입력. 위와 같은 이유로 거절한다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"manual": {"kcal": None, "proteinG": None}},
    )

    assert response.status_code == 422, response.text


def test_negative_nutrient_is_rejected(client, db):
    """음수 영양성분은 입력 실수다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"manual": {"kcal": -1}},
    )

    assert response.status_code == 422, response.text


def test_another_users_meal_is_not_found(client, db):
    """남의 식사는 존재조차 알리지 않는다 — 형제 엔드포인트와 같다."""
    owner = make_user(db)
    stranger = make_user(db, nickname="남의사람")
    meal = make_meal(db, user_id=owner.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=stranger.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 404, response.text


def test_item_from_another_meal_is_not_found(client, db):
    """이 식사의 항목이 아니면 404 다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    other_meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    other_item = make_meal_item(db, meal_id=other_meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=other_item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 404, response.text


def test_missing_meal_is_not_found(client, db):
    user = make_user(db)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=uuid.uuid4(),
        item_id=uuid.uuid4(),
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 404, response.text


def test_meal_under_initial_analysis_cannot_be_touched(client, db):
    """최초 분석 중(`is_recalculation=false`)에는 워커가 항목을 갈아엎는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.ANALYZING)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 409, response.text


def test_evaluated_meal_goes_back_to_recalculating(client, db):
    """이미 채점된 식사의 영양정보를 고치면 점수가 더는 유효하지 않다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "ANALYZING"
    assert data["isRecalculation"] is True


def _patch_amount(client, *, user_id, meal_id, item_id, display_name, amount, unit):
    return client.patch(
        f"/api/v1/meals/{meal_id}/items",
        headers={"X-User-Id": str(user_id)},
        json={
            "items": [
                {
                    "itemId": str(item_id),
                    "displayName": display_name,
                    "amount": amount,
                    "unit": unit,
                }
            ]
        },
    )


def _item_with_direct_input(client, db, *, user, meal, display_name="참치김밥"):
    """직접 입력까지 끝난 항목을 만든다 — 실제 경로(PUT)를 그대로 태운다."""
    item = make_meal_item(
        db, meal_id=meal.id, display_name=display_name, food_ref_id=None
    )
    db.commit()
    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"manual": _MANUAL},
    )
    assert response.status_code == 200, response.text
    return item


def test_changing_the_amount_clears_a_direct_input(client, db):
    """직접 입력은 **섭취량 기준 총량**이라 양이 바뀌면 거짓값이 된다.

    250g 을 먹었다고 적은 150kcal 이 300g 으로 바뀐 뒤에도 그대로 남으면, 그 값이
    Q/Q/S 채점까지 조용히 흘러간다. 비어 있는 편이 낫다는 기존 원칙 그대로
    (`api/v1/endpoints/meal_items.py` 의 `add_meal_item` 독스트링) NULL 로 되돌리고
    `matched: false` 폴백으로 다시 보낸다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = _item_with_direct_input(client, db, user=user, meal=meal)

    response = _patch_amount(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        display_name="참치김밥",
        amount=300,
        unit="g",
    )

    assert response.status_code == 200, response.text
    db.refresh(item)
    assert item.manual_kcal is None
    assert item.manual_sodium_mg is None


def test_renaming_clears_a_direct_input(client, db):
    """이름이 바뀌면 다른 음식이다 — 앞 음식에 적은 성분을 물려줄 근거가 없다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = _item_with_direct_input(client, db, user=user, meal=meal)

    response = _patch_amount(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        display_name="제육볶음",
        amount=250,
        unit="g",
    )

    assert response.status_code == 200, response.text
    db.refresh(item)
    assert item.manual_kcal is None


def test_resending_unchanged_values_keeps_a_direct_input(client, db):
    """확인 화면은 고치지 않은 항목까지 보낸다 — 그때 값이 사라지면 안 된다.

    `update_meal_items` 독스트링이 적어 둔 정상 경로다. 여기서 지우면 사용자는
    아무것도 고치지 않았는데 직접 입력한 영양정보를 잃는다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = _item_with_direct_input(client, db, user=user, meal=meal)

    response = _patch_amount(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        display_name="참치김밥",
        amount=250,
        unit="g",
    )

    assert response.status_code == 200, response.text
    db.refresh(item)
    assert item.manual_kcal == Decimal("150.000")


def test_the_database_rejects_a_negative_manual_nutrient(db):
    """스키마가 먼저 막지만(`ManualNutrition`) DB 에도 같은 규칙을 건다.

    API 를 거치지 않고 `meal_items` 를 쓰는 경로(워커·배치·수기 SQL)가 생겨도 음수가
    새지 않아야 한다 — 이 값은 Q/Q/S 채점기로 바로 흘러간다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.flush()

    item.manual_kcal = Decimal("-1.000")
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_candidate_that_cannot_be_scaled_does_not_wipe_a_direct_input(client, db):
    """실패한 후보 선택이 사용자가 갖고 있던 유일한 영양정보를 지우면 안 된다.

    "계란 2개" 처럼 환산이 안 되는 항목에 직접 입력을 해 둔 뒤 후보를 고르면, 그
    후보로는 아무 값도 만들 수 없다. 여기서 직접 입력까지 지우면 사용자는 요청
    한 번으로 폴백 시작점(`matched: false`)으로 되돌아간다 — 얻은 것 없이 잃기만
    한다. 지우는 건 **새 값이 실제로 그 자리를 채울 때**뿐이다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="삶은 계란",
        food_ref_id=None,
        estimated_amount=Decimal("2.00"),
        estimated_unit="개",
    )
    db.commit()
    assert (
        _put(
            client,
            user_id=user.id,
            meal_id=meal.id,
            item_id=item.id,
            json={"manual": _MANUAL},
        ).status_code
        == 200
    )

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # 직접 입력이 살아 있으므로 여전히 영양정보가 나간다 — 빈 응답이 아니다.
    assert body["error"] is None
    data = body["data"]
    assert data["matched"] is True
    assert data["nutritionSource"] == "USER_INPUT"
    assert data["nutrition"]["kcal"] == 150.0

    db.refresh(item)
    assert item.manual_kcal == Decimal("150.000")
    # 링크는 고른 후보로 바뀐다 — 양을 g 으로 고치면 그때 환산된다.
    assert item.food_ref_id == "KFD_01023"


def test_deleted_meal_is_not_found(client, db):
    """soft delete 된 식사는 없는 식사와 같게 응답한다 — 형제 엔드포인트와 동일."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    headers = {"X-User-Id": str(user.id)}
    assert client.delete(f"/api/v1/meals/{meal.id}", headers=headers).status_code == 200

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"foodRefId": "KFD_01023"},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_absurdly_large_nutrient_is_rejected(client, db):
    """상한(`_MAX_NUTRIENT`)을 넘는 값은 오타다 — 채점까지 흘러가기 전에 막는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, food_ref_id=None)
    db.commit()

    response = _put(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        json={"manual": {"kcal": 1000001}},
    )

    assert response.status_code == 422, response.text


def test_changing_the_amount_falls_back_to_the_linked_public_db_values(client, db):
    """직접 입력이 지워지면 그 아래 깔려 있던 공공 DB 링크가 다시 드러난다.

    직접 입력은 링크를 끊지 않으므로(`services.meal.set_item_nutrition`) 양을 고쳐
    직접 입력이 NULL 이 되면 **예전에 걸려 있던 링크의 환산값이 되살아난다.**
    "계란 2개 → 100g" 처럼 이제 환산이 되는 경우엔 바라던 동작이지만, 사용자가 그
    링크의 값이 틀려서 직접 입력했던 경우라면 거부했던 값이 되돌아온다.

    지금은 이 동작을 그대로 둔다 — 링크를 끊을 수단이 요청 모양에 없고(명세서에
    그런 갈래가 없다), 링크를 지우면 "양만 고쳤는데 영양정보가 사라졌다" 가 된다.
    의도를 여기 못 박아 둔다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_01023", name="달걀_삶은것")
    item = make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_01023")
    db.commit()

    assert (
        _put(
            client,
            user_id=user.id,
            meal_id=meal.id,
            item_id=item.id,
            json={"manual": _MANUAL},
        ).status_code
        == 200
    )

    response = _patch_amount(
        client,
        user_id=user.id,
        meal_id=meal.id,
        item_id=item.id,
        display_name="참치김밥",
        amount=200,
        unit="g",
    )

    assert response.status_code == 200, response.text
    db.refresh(item)
    assert item.manual_kcal is None
    # 200g × (50kcal / 100g) = 100kcal — 링크가 살아 있어 다시 계산된다.
    assert item.food_ref_id == "KFD_01023"
