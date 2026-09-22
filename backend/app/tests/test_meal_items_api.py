"""POST /meals/{mealId}/items 의 HTTP 계약.

FE 가 실제로 보는 모양 — 응답 래퍼 · camelCase · 숫자 타입 · 404 단일화 · 상태 가드.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import MealItemSource, MealStatus
from app.models.meal import MealItem
from app.models.task import Task
from app.tests.factories import make_food_ref, make_meal, make_user


def test_add_item_returns_created_item_with_scaled_nutrition(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_MIYEOK", name="미역국")

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "미역국", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    assert data["itemId"]
    assert data["matched"] is True
    assert data["status"] == "ANALYZING"
    assert data["isRecalculation"] is True
    # 명세의 nutrition 은 숫자다. Decimal 이 문자열로 새어 나가면 FE 의 비교가 깨진다.
    assert data["nutrition"]["kcal"] == 100
    assert data["nutrition"]["proteinG"] == 6
    assert data["nutrition"]["sodiumMg"] == 1200


def test_add_item_to_unknown_meal_is_404(client, db):
    """없는 mealId 는 404 다. 소유권 누출을 막으려 남의 것·삭제된 것과 같게 응답한다."""
    user = make_user(db)

    response = client.post(
        f"/api/v1/meals/{uuid.uuid4()}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "미역국", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 404


@pytest.mark.parametrize("status", [MealStatus.ANALYZING, MealStatus.FAILED])
def test_add_item_is_conflict_while_meal_is_not_editable(client, db, status):
    """최초 분석 중에는 Worker 가 `meal_items` 를 갈아엎고 있고, 실패한 식사는 고칠 대상이 없다.

    여기 `ANALYZING` 은 `is_recalculation=False` — 최초 분석이다. 사용자 수정으로
    인한 `ANALYZING` 은 반대로 허용된다(`test_user_can_keep_editing_...` 참고).
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=status)

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "미역국", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_user_can_keep_editing_while_recalculation_is_pending(client, db):
    """확인 화면에서는 음식을 여러 개 고친 뒤 "확인" 을 누른다 — 한 번만 되면 기능이 아니다.

    첫 추가가 식사를 `ANALYZING` 으로 바꾸므로, `ANALYZING` 을 통째로 막으면 두 번째
    추가가 409 로 막힌다. 사용자가 방금 스스로 만든 상태에 갇히는 셈이다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    db.commit()
    headers = {"X-User-Id": str(user.id)}

    added = [
        client.post(
            f"/api/v1/meals/{meal.id}/items",
            headers=headers,
            json={"displayName": name, "amount": 100, "unit": "g"},
        )
        for name in ("미역국", "김치", "현미밥")
    ]

    assert [r.status_code for r in added] == [201, 201, 201]
    # 모든 응답이 명세대로 재분석 대기를 알린다.
    for response in added:
        data = response.json()["data"]
        assert data["status"] == MealStatus.ANALYZING.value
        assert data["isRecalculation"] is True


def test_add_item_is_allowed_after_evaluation(client, db):
    """평가가 끝난 식사도 고칠 수 있다 — 명세의 상태 전이가 ANALYZING 으로 되돌린다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "미역국", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["status"] == "ANALYZING"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"displayName": "", "amount": 200, "unit": "g"}, "빈 음식명"),
        ({"displayName": "   ", "amount": 200, "unit": "g"}, "공백뿐인 음식명"),
        ({"displayName": "미역국", "amount": 0, "unit": "g"}, "0 g 을 먹을 수는 없다"),
        ({"displayName": "미역국", "amount": -1, "unit": "g"}, "음수 섭취량"),
        ({"displayName": "미역국", "amount": 200, "unit": ""}, "빈 단위"),
        ({"displayName": "미역국", "amount": 200}, "단위 누락"),
        # 아래 둘은 컬럼(Numeric(8, 2))을 넘긴다. 스키마가 막지 않으면 각각
        # Decimal.quantize 의 InvalidOperation 과 커밋 시 DataError 로 500 이 된다 —
        # 클라이언트 입력 오류가 5xx 로 나가면 core/response.py 의 규칙을 어긴다.
        ({"displayName": "미역국", "amount": 1000000, "unit": "g"}, "컬럼 상한 초과"),
        ({"displayName": "미역국", "amount": 1e30, "unit": "g"}, "quantize 가 터지는 값"),
    ],
)
def test_add_item_rejects_invalid_payload(client, db, payload, reason):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=payload,
    )

    assert response.status_code == 422, reason
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_add_item_without_public_db_match_returns_no_nutrition(client, db):
    """공공 DB 에 없는 음식이어도 항목은 추가된다 — 영양정보만 비어 있다.

    FE 는 `matched: false` 를 보고 `PUT /meals/{mealId}/items/{itemId}/nutrition`
    (직접 입력) 으로 유도한다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "할머니표 미역국", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["matched"] is False
    assert data["nutrition"] is None


def test_add_item_in_countable_unit_is_not_matched(client, db):
    """"계란 2개" 는 g 으로 옮길 근거가 없어 영양성분을 만들 수 없다.

    공공 DB 에서 음식 자체는 찾았지만 `matched` 는 false 다 — 계약서가 `matched` 를
    "영양정보 유무"로 정의하기 때문이다(API.md 필드표: `false → 영양정보 없음`).
    같은 상황("삶은 계란 2개")을 계약서도 `matched: false` 로 예시한다.
    FE 는 이 플래그를 보고 직접 입력으로 유도하므로, true 로 내보내면 영양성분
    없는 항목이 조용히 Q/Q/S 채점에 들어간다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_EGG", name="삶은 계란")

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "삶은 계란", "amount": 2, "unit": "개"},
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["matched"] is False
    assert data["nutrition"] is None


def test_add_item_is_not_matched_when_serving_size_is_unknown(client, db):
    """기준량이 없어 환산이 불가능한 음식도 마찬가지로 matched=false 다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_NO_BASIS", name="기준량없는음식", serving_size=None)

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "기준량없는음식", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["data"]["matched"] is False


def test_unmatched_item_still_links_the_food_ref_row(client, db):
    """응답의 matched 는 영양정보 유무지만, DB 링크는 끊지 않는다.

    나중에 사용자가 g 단위로 양을 고치면 이미 붙어 있는 food_ref 로 곧바로 환산된다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_EGG", name="삶은 계란")

    client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "삶은 계란", "amount": 2, "unit": "개"},
    )

    item = db.execute(select(MealItem).where(MealItem.meal_id == meal.id)).scalar_one()
    assert item.food_ref_id == "KFD_EGG"


def test_add_item_persists_row_and_meal_state(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "미역국", "amount": 200, "unit": "g"},
    )
    assert response.status_code == 201, response.text

    items = db.execute(select(MealItem).where(MealItem.meal_id == meal.id)).scalars().all()
    assert [item.display_name for item in items] == ["미역국"]
    assert items[0].source is MealItemSource.USER
    assert items[0].confirmed_amount_g == Decimal("200.00")
    # 사용자가 말한 값 자체는 환산 여부와 무관하게 이 두 컬럼에 남는다.
    assert (items[0].confirmed_amount, items[0].confirmed_unit) == (Decimal("200"), "g")

    db.refresh(meal)
    assert meal.status is MealStatus.ANALYZING
    assert meal.is_recalculation is True


def test_add_item_to_another_users_meal_is_indistinguishable_from_unknown(client, db):
    """소유권 누출 방지: 남의 식사도 '없는 식사'와 같은 응답이어야 한다."""
    owner = make_user(db, "종호")
    stranger = make_user(db, "남의사람")
    meal = make_meal(db, user_id=owner.id)

    payload = {"displayName": "미역국", "amount": 200, "unit": "g"}
    headers = {"X-User-Id": str(stranger.id)}

    real = client.post(f"/api/v1/meals/{meal.id}/items", headers=headers, json=payload)
    unknown = client.post(
        f"/api/v1/meals/{uuid.uuid4()}/items", headers=headers, json=payload
    )

    assert real.status_code == unknown.status_code == 404
    assert real.json()["error"]["code"] == unknown.json()["error"]["code"] == "NOT_FOUND"


def test_add_item_to_deleted_meal_is_404(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    headers = {"X-User-Id": str(user.id)}

    assert client.delete(f"/api/v1/meals/{meal.id}", headers=headers).status_code == 200

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers=headers,
        json={"displayName": "미역국", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_add_item_without_user_header_is_401(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        json={"displayName": "미역국", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_broken_public_db_value_never_reaches_the_response_as_invalid_json(client, db):
    """Postgres NUMERIC 은 NaN 을 담을 수 있다 — 시드에 한 건만 있어도 응답이 깨진다.

    `float(Decimal("NaN"))` 은 `nan` 이고, json 직렬화가 표준 JSON 에 없는 `NaN`
    리터럴을 내보낸다. Dart·JS 의 기본 파서는 여기서 던진다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_NAN", name="깨진데이터", calories=Decimal("NaN"))

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "깨진데이터", "amount": 100, "unit": "g"},
    )

    assert response.status_code == 201, response.text
    assert "NaN" not in response.text
    assert response.json()["data"]["nutrition"]["kcal"] is None


def test_add_item_never_touches_the_task_queue(client, db):
    """음식 추가는 비동기 작업을 만들지 않는다 — 설계 결정을 코드로 고정한다.

    사용자가 음식명과 양을 직접 알려줬으므로 AI 에게 물을 것이 없고, 다시 계산할
    Q/Q/S 는 순수 함수다(README 절대 규칙 2). `worker/dispatch.py` 도 "여기 있는 건
    AI 를 부르는 작업뿐이다" 라고 못박는다.

    이 판단은 독스트링 세 곳에 길게 적혀 있지만 **글로는 회귀를 막지 못한다.**

    큐가 테이블이라 보내는 함수를 패치할 필요가 없다 — 행이 생겼는지 직접 본다.
    `enqueue` 를 패치하면 호출부가 `from app.infra.queue import enqueue` 로 이름을
    당겨 왔을 때 잡지 못하지만, 결과를 보면 어떤 경로로 넣었든 걸린다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)

    response = client.post(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"displayName": "미역국", "amount": 200, "unit": "g"},
    )

    assert response.status_code == 201, response.text
    queued = db.execute(select(Task)).scalars().all()
    assert queued == [], (
        "음식 추가 경로에서 큐에 작업을 넣었다 "
        "— `add_meal_item` 독스트링의 'ANALYZING 은 워커가 도는 중이 아니다' 참고"
    )
