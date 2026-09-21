"""PATCH /meals/{mealId}/items 의 HTTP 계약.

확인 화면에서 사용자가 AI 인식 결과를 고치는 경로다. POST(음식 추가)의 계약은
`test_meal_items_api.py` 에 따로 있다.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import MealItemSource, MealStatus
from app.models.meal import UserCorrection
from app.models.task import Task
from app.tests.factories import (
    make_food_ref,
    make_meal,
    make_meal_item,
    make_user,
)


def test_update_item_returns_recalculating_status_with_steps(client, db):
    """명세서(contracts/API.md PATCH /meals/{mealId}/items)의 응답 모양 그대로."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, display_name="참치김밥")
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
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

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    assert data["status"] == "ANALYZING"
    assert data["isRecalculation"] is True
    assert data["steps"] == [
        {"key": "FOOD_RECOGNITION", "state": "DONE"},
        {"key": "DB_MATCHING", "state": "RUNNING"},
        {"key": "STAGE_RULE_APPLY", "state": "PENDING"},
    ]


def test_changing_only_the_amount_keeps_the_public_db_link(client, db):
    """양만 고쳤는데 영양정보가 사라지면 안 된다.

    이름 매칭(`crud.food.find_unique_by_name`)은 일부러 보수적이라 흔한 음식에
    `None` 을 준다. AI 가 `candidateFoodRefId` 로 정확히 연결해 둔 항목을 다시 찾으면
    링크가 끊긴다 — "250g → 220g" 수정이 영양정보를 통째로 날리는 셈이다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    # 같은 이름이 공공 DB 에 여러 건 — 이름으로는 하나로 좁혀지지 않는다.
    make_food_ref(db, food_ref_id="KFD_JJIGAE_1", name="김치찌개")
    make_food_ref(db, food_ref_id="KFD_JJIGAE_2", name="김치찌개")
    item = make_meal_item(
        db, meal_id=meal.id, display_name="김치찌개", food_ref_id="KFD_JJIGAE_1"
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(item.id),
                    "displayName": "김치찌개",
                    "amount": 220,
                    "unit": "g",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    db.refresh(item)
    assert item.food_ref_id == "KFD_JJIGAE_1"
    assert item.confirmed_amount_g == Decimal("220.00")


def test_changing_the_name_rematches_the_public_db(client, db):
    """이름이 바뀌면 다른 음식이다 — 링크도 새 이름 기준으로 다시 잡는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_GIMBAP", name="참치김밥")
    make_food_ref(db, food_ref_id="KFD_MIYEOK", name="미역국")
    item = make_meal_item(
        db, meal_id=meal.id, display_name="참치김밥", food_ref_id="KFD_GIMBAP"
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(item.id),
                    "displayName": "미역국",
                    "amount": 200,
                    "unit": "g",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    db.refresh(item)
    assert item.display_name == "미역국"
    assert item.food_ref_id == "KFD_MIYEOK"
    # AI 최초 추정값은 남는다 — 인식 성능 평가의 기준이다.
    assert item.original_food_name == "참치김밥"


def test_renaming_to_an_unmatchable_food_unlinks_the_public_db_row(client, db):
    """새 이름을 못 찾으면 링크를 끊는다 — 남겨 두면 다른 음식의 성분이 붙는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_GIMBAP", name="참치김밥")
    item = make_meal_item(
        db, meal_id=meal.id, display_name="참치김밥", food_ref_id="KFD_GIMBAP"
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(item.id),
                    "displayName": "할머니표 잡채",
                    "amount": 150,
                    "unit": "g",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    db.refresh(item)
    assert item.display_name == "할머니표 잡채"
    assert item.food_ref_id is None


def test_updates_several_items_in_one_request(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    gimbap = make_meal_item(db, meal_id=meal.id, display_name="참치김밥")
    egg = make_meal_item(db, meal_id=meal.id, display_name="삶은 계란")
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(gimbap.id),
                    "displayName": "참치김밥",
                    "amount": 220,
                    "unit": "g",
                },
                {
                    "itemId": str(egg.id),
                    "displayName": "삶은 메추리알",
                    "amount": 60,
                    "unit": "g",
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    db.refresh(gimbap)
    db.refresh(egg)
    assert gimbap.confirmed_amount_g == Decimal("220.00")
    assert egg.display_name == "삶은 메추리알"
    assert egg.confirmed_amount_g == Decimal("60.00")


def test_unknown_item_id_is_404_and_leaves_every_item_untouched(client, db):
    """확인 화면은 여러 항목을 한 번에 보낸다 — 절반만 반영되면 화면과 서버가 어긋난다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    gimbap = make_meal_item(db, meal_id=meal.id, display_name="참치김밥")
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(gimbap.id),
                    "displayName": "참치김밥",
                    "amount": 220,
                    "unit": "g",
                },
                {
                    "itemId": str(uuid.uuid4()),
                    "displayName": "없는 항목",
                    "amount": 100,
                    "unit": "g",
                },
            ]
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"

    db.refresh(gimbap)
    assert gimbap.confirmed_amount_g is None
    db.refresh(meal)
    assert meal.status is MealStatus.REVIEW_REQUIRED
    assert meal.is_recalculation is False


def test_item_belonging_to_another_meal_is_404(client, db):
    """itemId 만 맞으면 남의 식사 항목까지 고쳐지는 일이 없어야 한다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    other_meal = make_meal(db, user_id=user.id)
    outsider = make_meal_item(db, meal_id=other_meal.id, display_name="현미밥")
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(outsider.id),
                    "displayName": "백미밥",
                    "amount": 210,
                    "unit": "g",
                }
            ]
        },
    )

    assert response.status_code == 404
    db.refresh(outsider)
    assert outsider.display_name == "현미밥"


def test_update_on_unknown_meal_is_404(client, db):
    user = make_user(db)

    response = client.patch(
        f"/api/v1/meals/{uuid.uuid4()}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(uuid.uuid4()),
                    "displayName": "미역국",
                    "amount": 200,
                    "unit": "g",
                }
            ]
        },
    )

    assert response.status_code == 404


def test_update_on_another_users_meal_is_indistinguishable_from_unknown(client, db):
    """소유권 누출 방지: 남의 식사도 '없는 식사'와 같은 응답이어야 한다."""
    owner = make_user(db, "종호")
    stranger = make_user(db, "남의사람")
    meal = make_meal(db, user_id=owner.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    payload = {
        "items": [
            {
                "itemId": str(item.id),
                "displayName": "참치김밥",
                "amount": 220,
                "unit": "g",
            }
        ]
    }
    headers = {"X-User-Id": str(stranger.id)}

    real = client.patch(f"/api/v1/meals/{meal.id}/items", headers=headers, json=payload)
    unknown = client.patch(
        f"/api/v1/meals/{uuid.uuid4()}/items", headers=headers, json=payload
    )

    assert real.status_code == unknown.status_code == 404
    assert real.json()["error"]["code"] == unknown.json()["error"]["code"] == "NOT_FOUND"


def test_update_on_deleted_meal_is_404(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()
    headers = {"X-User-Id": str(user.id)}

    assert client.delete(f"/api/v1/meals/{meal.id}", headers=headers).status_code == 200

    response = client.patch(
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

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_update_without_user_header_is_401(client, db):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
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

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("status", [MealStatus.ANALYZING, MealStatus.FAILED])
def test_update_is_conflict_while_meal_is_not_editable(client, db, status):
    """최초 분석 중에는 Worker 가 `meal_items` 를 갈아엎고 있고, 실패한 식사는 고칠 대상이 없다.

    여기 `ANALYZING` 은 `is_recalculation=False` — 최초 분석이다. 사용자 수정으로
    인한 `ANALYZING` 은 반대로 허용된다(`test_user_can_keep_editing_...` 참고).
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=status)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
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

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_user_can_keep_editing_while_recalculation_is_pending(client, db):
    """확인 화면에서는 고치고 또 고친다 — 한 번만 되면 기능이 아니다.

    첫 수정이 식사를 `ANALYZING` 으로 바꾸므로, `ANALYZING` 을 통째로 막으면 두 번째
    수정이 409 로 막힌다. 사용자가 방금 스스로 만든 상태에 갇히는 셈이다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()
    headers = {"X-User-Id": str(user.id)}

    edits = [
        client.patch(
            f"/api/v1/meals/{meal.id}/items",
            headers=headers,
            json={
                "items": [
                    {
                        "itemId": str(item.id),
                        "displayName": "참치김밥",
                        "amount": amount,
                        "unit": "g",
                    }
                ]
            },
        )
        for amount in (240, 230, 220)
    ]

    assert [r.status_code for r in edits] == [200, 200, 200]
    db.refresh(item)
    assert item.confirmed_amount_g == Decimal("220.00")


def test_update_is_allowed_after_evaluation(client, db):
    """평가가 끝난 식사도 고칠 수 있다 — 명세의 상태 전이가 ANALYZING 으로 되돌린다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
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

    assert response.status_code == 200, response.text
    db.refresh(meal)
    assert meal.status is MealStatus.ANALYZING
    assert meal.is_recalculation is True


def _payload(item_id, **overrides):
    base = {
        "itemId": str(item_id),
        "displayName": "참치김밥",
        "amount": 220,
        "unit": "g",
    }
    return {"items": [base | overrides]}


@pytest.mark.parametrize(
    "items, reason",
    [
        ([], "빈 배열 — 고칠 게 없는 요청"),
        (
            [
                {
                    "itemId": "00000000-0000-0000-0000-000000000001",
                    "displayName": "참치김밥",
                    "amount": 0,
                    "unit": "g",
                }
            ],
            "amount 는 양수여야 한다",
        ),
        (
            [
                {
                    "itemId": "00000000-0000-0000-0000-000000000001",
                    "displayName": "참치김밥",
                    "amount": 1000000,
                    "unit": "g",
                }
            ],
            "confirmed_amount_g 는 Numeric(8,2) — 넘으면 DB 가 터진다",
        ),
        (
            [
                {
                    "itemId": "00000000-0000-0000-0000-000000000001",
                    "displayName": "   ",
                    "amount": 220,
                    "unit": "g",
                }
            ],
            "공백만 있는 이름",
        ),
        (
            [
                {
                    "itemId": "00000000-0000-0000-0000-000000000001",
                    "amount": 220,
                    "unit": "g",
                }
            ],
            "displayName 은 필수 — 항목을 통째로 교체한다",
        ),
    ],
)
def test_update_rejects_invalid_payload(client, db, items, reason):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={"items": items},
    )

    assert response.status_code == 422, reason


def test_update_rejects_the_same_item_twice(client, db):
    """같은 항목에 두 값이 오면 어느 쪽이 맞는지 서버가 정할 수 없다.

    조용히 뒤엣것을 쓰면 사용자는 자기가 보낸 값이 사라진 걸 모른다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(item.id),
                    "displayName": "참치김밥",
                    "amount": 220,
                    "unit": "g",
                },
                {
                    "itemId": str(item.id),
                    "displayName": "참치김밥",
                    "amount": 180,
                    "unit": "g",
                },
            ]
        },
    )

    assert response.status_code == 422
    db.refresh(item)
    assert item.confirmed_amount_g is None


def _corrections(db, item_id):
    return (
        db.execute(
            select(UserCorrection).where(UserCorrection.meal_item_id == item_id)
        )
        .scalars()
        .all()
    )


def test_correcting_an_ai_item_records_the_change(client, db):
    """`user_corrections` 는 AI 인식 성능 평가용이다 — PATCH 가 그 유일한 기록 지점이다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="김밥",
        estimated_amount_g=Decimal("250.00"),
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="참치김밥", amount=220, unit="g"),
    )
    assert response.status_code == 200, response.text

    (correction,) = _corrections(db, item.id)
    assert correction.original_value["displayName"] == "김밥"
    assert correction.original_value["amountG"] == "250.00"
    assert correction.corrected_value["displayName"] == "참치김밥"
    assert correction.corrected_value["amountG"] == "220.00"


def test_correcting_a_user_added_item_records_nothing(client, db):
    """사용자가 직접 넣은 음식에는 고칠 'AI 인식값' 이 없다 — 통계에 섞이면 안 된다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="미역국",
        source=MealItemSource.USER,
        confidence=None,
        estimated_amount_g=None,
        confirmed_amount_g=Decimal("200.00"),
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="미역국", amount=150, unit="g"),
    )
    assert response.status_code == 200, response.text

    assert _corrections(db, item.id) == []
    db.refresh(item)
    assert item.confirmed_amount_g == Decimal("150.00")


def test_resending_the_same_values_records_nothing(client, db):
    """확인 화면이 고치지 않은 항목까지 보내도 '수정했다' 는 기록이 쌓이면 안 된다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="참치김밥",
        estimated_amount_g=Decimal("250.00"),
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="참치김밥", amount=250, unit="g"),
    )
    assert response.status_code == 200, response.text

    assert _corrections(db, item.id) == []


def test_correction_keeps_the_users_raw_unit_when_grams_are_unknown(client, db):
    """"2개" 처럼 환산 근거가 없는 단위는 `confirmed_amount_g` 에 담기지 않는다.

    AI 인식 항목의 `raw_ai_result` 는 AI 원본이라 덮어쓸 수 없으므로, 사용자가 실제로
    무엇을 입력했는지는 여기에만 남는다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="삶은 계란",
        estimated_amount_g=Decimal("100.00"),
        raw_ai_result={"foodName": "삶은 계란", "confidence": 0.96},
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="삶은 계란", amount=2, unit="개"),
    )
    assert response.status_code == 200, response.text

    db.refresh(item)
    assert item.confirmed_amount_g is None
    assert (item.confirmed_amount, item.confirmed_unit) == (Decimal("2"), "개")
    # AI 원본은 사용자 입력과 섞이지 않는다.
    assert item.raw_ai_result == {"foodName": "삶은 계란", "confidence": 0.96}

    (correction,) = _corrections(db, item.id)
    assert correction.corrected_value["amount"] == "2"
    assert correction.corrected_value["unit"] == "개"
    assert correction.corrected_value["amountG"] is None


def test_update_never_touches_the_task_queue(client, db):
    """음식 수정은 비동기 작업을 만들지 않는다 — 설계 결정을 코드로 고정한다.

    응답의 `steps` 가 `DB_MATCHING: RUNNING` 이라 "워커가 돈다" 로 읽히기 쉽다.
    실제로는 사용자가 이름과 양을 직접 알려줬으므로 AI 에게 물을 것이 없고, 다시
    계산할 Q/Q/S 는 순수 함수다(README 절대 규칙 2).

    큐가 테이블이라 보내는 함수를 패치할 필요가 없다 — 행이 생겼는지 직접 본다.
    `enqueue` 를 패치하면 호출부가 `from app.infra.queue import enqueue` 로 이름을
    당겨 왔을 때 잡지 못하지만, 결과를 보면 어떤 경로로 넣었든 걸린다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id)
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, amount=220),
    )

    assert response.status_code == 200, response.text
    queued = db.execute(select(Task)).scalars().all()
    assert queued == [], (
        "음식 수정 경로에서 큐에 작업을 넣었다 "
        "— `update_meal_items` 독스트링의 'steps 는 고정값이다' 참고"
    )


def test_amount_edit_in_a_countable_unit_is_not_silently_dropped(client, db):
    """AI 가 "삶은 계란 2개" 로 인식한 항목의 양을 3개로 고친다.

    g 환산이 안 되므로 `confirmed_amount_g` 에는 담을 수 없다. 그렇다고 요청을
    받아 놓고 200 을 준 뒤 아무 데도 남기지 않으면 사용자 입력이 사라진다 —
    화면에는 3개, DB 에는 아무것도 없는 상태가 된다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="삶은 계란",
        estimated_amount_g=None,
        raw_ai_result={"foodName": "삶은 계란", "amount": 2, "unit": "개"},
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="삶은 계란", amount=3, unit="개"),
    )
    assert response.status_code == 200, response.text

    db.refresh(item)
    # 사용자 입력은 전용 컬럼으로, AI 원본은 손대지 않는다.
    assert (item.confirmed_amount, item.confirmed_unit) == (Decimal("3"), "개")
    assert item.raw_ai_result == {"foodName": "삶은 계란", "amount": 2, "unit": "개"}

    (correction,) = _corrections(db, item.id)
    assert correction.corrected_value["amount"] == "3"
    assert correction.corrected_value["unit"] == "개"


def test_user_added_item_keeps_its_raw_input_in_sync(client, db):
    """사용자가 직접 넣은 항목도 AI 인식 항목과 같은 자리를 쓴다.

    출처에 따라 양이 다른 곳에 있으면 읽는 쪽(`GET /meals/{mealId}`)이 분기해야 한다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="미역국",
        source=MealItemSource.USER,
        confidence=None,
        estimated_amount_g=None,
        confirmed_amount=Decimal("200"),
        confirmed_unit="g",
        confirmed_amount_g=Decimal("200.00"),
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="미역국", amount=2, unit="개"),
    )
    assert response.status_code == 200, response.text

    db.refresh(item)
    assert (item.confirmed_amount, item.confirmed_unit) == (Decimal("2"), "개")
    assert item.confirmed_amount_g is None


def test_renaming_clears_the_ai_confidence(client, db):
    """사용자가 이름을 바꾸면 그 신뢰도는 더는 이 항목의 것이 아니다.

    명세서상 FE 는 `confidence < 0.8` 을 강조한다 — 사용자가 직접 써 넣은 이름이
    "AI 가 자신 없어함" 으로 표시되면 거짓말이다. `crud.meal.add_item` 이 사용자
    입력 항목의 신뢰도를 NULL 로 두는 것과 같은 이유다.

    값 자체는 `user_corrections` 에 남아 AI 인식 성능 평가에 쓸 수 있다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db, meal_id=meal.id, display_name="김밥", confidence=Decimal("0.620")
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="참치김밥", amount=220, unit="g"),
    )
    assert response.status_code == 200, response.text

    db.refresh(item)
    assert item.confidence is None

    (correction,) = _corrections(db, item.id)
    assert correction.original_value["confidence"] == "0.620"


def test_amount_only_edit_keeps_the_ai_confidence(client, db):
    """이름이 그대로면 AI 인식은 여전히 유효하다 — 신뢰도를 지울 이유가 없다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db, meal_id=meal.id, display_name="참치김밥", confidence=Decimal("0.620")
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, amount=220),
    )
    assert response.status_code == 200, response.text

    db.refresh(item)
    assert item.confidence == Decimal("0.620")


def test_trailing_whitespace_in_the_name_is_not_a_rename(client, db):
    """FE 가 화면의 이름을 그대로 돌려보낼 때 공백 하나로 링크가 끊기면 안 된다.

    요청의 이름은 pydantic 이 strip 하지만 저장된 이름은 워커가 쓴 그대로다. 둘을
    날것으로 비교하면 `"참치김밥 "` → `"참치김밥"` 이 rename 으로 보이고, 이름
    매칭이 못 좁히는 순간 `food_ref_id` 가 사라진다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    make_food_ref(db, food_ref_id="KFD_GIMBAP_1", name="참치김밥")
    make_food_ref(db, food_ref_id="KFD_GIMBAP_2", name="참치김밥")
    item = make_meal_item(
        db, meal_id=meal.id, display_name="참치김밥 ", food_ref_id="KFD_GIMBAP_1"
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="참치김밥", amount=250),
    )
    assert response.status_code == 200, response.text

    db.refresh(item)
    assert item.food_ref_id == "KFD_GIMBAP_1"
    # 공백만 다른 건 사용자가 고친 게 아니다 — 이력에 쌓이면 인식 오차가 오염된다.
    assert _corrections(db, item.id) == []


def test_editing_the_same_item_twice_chains_the_corrections(client, db):
    """두 번째 행의 `original_value` 는 AI 인식값이 아니라 첫 수정의 결과다.

    README 의 `user_corrections` 절이 약속하는 내용이다 — 이걸 모르고 두 번째 행을
    AI 인식값으로 읽으면 인식 성능 통계가 조용히 틀어진다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db, meal_id=meal.id, display_name="김밥", estimated_amount_g=Decimal("250.00")
    )
    db.commit()
    headers = {"X-User-Id": str(user.id)}

    first = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers=headers,
        json=_payload(item.id, displayName="김밥", amount=230),
    )
    second = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers=headers,
        json=_payload(item.id, displayName="김밥", amount=220),
    )
    assert [first.status_code, second.status_code] == [200, 200]

    corrections = sorted(
        _corrections(db, item.id), key=lambda row: row.corrected_value["amountG"]
    )
    assert [row.original_value["amountG"] for row in corrections] == ["230.00", "250.00"]
    assert [row.corrected_value["amountG"] for row in corrections] == ["220.00", "230.00"]

    # AI 최초 추정값은 어떤 수정에도 덮이지 않는다 — 인식 성능의 기준점이다.
    db.refresh(item)
    assert item.estimated_amount_g == Decimal("250.00")
    assert item.original_food_name == "김밥"


def test_rejected_request_writes_no_correction_rows(client, db):
    """404 로 거절된 요청은 이력도 남기지 않는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, display_name="김밥")
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(item.id),
                    "displayName": "참치김밥",
                    "amount": 220,
                    "unit": "g",
                },
                {
                    "itemId": str(uuid.uuid4()),
                    "displayName": "없는 항목",
                    "amount": 100,
                    "unit": "g",
                },
            ]
        },
    )

    assert response.status_code == 404
    assert _corrections(db, item.id) == []


def test_update_rejects_an_absurdly_long_items_array(client, db):
    """확인 화면 한 끼에 담길 수 있는 양을 넘어서면 거절한다.

    상한이 없으면 아무 UUID 로나 5만 건을 보내 공공 DB 조회를 5만 번 시킬 수 있다 —
    소유권 확인(404)보다 이름 매칭이 먼저 돌기 때문이다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json={
            "items": [
                {
                    "itemId": str(uuid.uuid4()),
                    "displayName": f"음식{index}",
                    "amount": 100,
                    "unit": "g",
                }
                for index in range(51)
            ]
        },
    )

    assert response.status_code == 422


def test_user_input_amount_and_unit_land_in_their_own_columns(client, db):
    """사용자가 말한 양은 환산 여부와 무관하게 항상 같은 자리에 있어야 한다.

    `confirmed_amount_g` 는 g 으로 환산된 값만 담으므로 "3개" 는 NULL 이 된다.
    읽는 쪽(`GET /meals/{mealId}` 의 `amount`·`unit`)이 출처와 수정 이력에 따라
    다른 곳을 뒤지게 만들지 않는다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(
        db,
        meal_id=meal.id,
        display_name="삶은 계란",
        estimated_amount_g=None,
        raw_ai_result={"foodName": "삶은 계란", "confidence": 0.96},
    )
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, displayName="삶은 계란", amount=3, unit="개"),
    )
    assert response.status_code == 200, response.text

    db.refresh(item)
    assert item.confirmed_amount == Decimal("3")
    assert item.confirmed_unit == "개"
    assert item.confirmed_amount_g is None
    # AI 원본은 이제 사용자 입력과 섞이지 않는다.
    assert item.raw_ai_result == {"foodName": "삶은 계란", "confidence": 0.96}


def test_convertible_unit_fills_both_the_raw_pair_and_the_grams(client, db):
    """g 으로 환산되는 단위여도 사용자가 말한 값은 그대로 남는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, display_name="참치김밥")
    db.commit()

    response = client.patch(
        f"/api/v1/meals/{meal.id}/items",
        headers={"X-User-Id": str(user.id)},
        json=_payload(item.id, amount=220, unit="g"),
    )
    assert response.status_code == 200, response.text

    db.refresh(item)
    assert item.confirmed_amount == Decimal("220")
    assert item.confirmed_unit == "g"
    assert item.confirmed_amount_g == Decimal("220.00")
