"""POST /meals/{mealId}/confirm · GET /meals/{mealId}/evaluation 의 HTTP 계약.

FE 가 실제로 보는 모양 — 응답 래퍼 · camelCase · 명세 필드 · 404 단일화 · 409 코드.
"""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.enums import MealStatus, MedicationStage
from app.tests.factories import make_food_ref, make_meal, make_meal_item, make_user

_SPEC_FIELDS = {
    "mealId", "status", "stage", "scores", "stageEmphasis",
    "nutrients", "evidence", "feedbackStatus",
}


def _h(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def _ready_meal(db: Session, *, stage=MedicationStage.MAINTENANCE):
    """확정 직전 상태(REVIEW_REQUIRED)의 식사 + 성분이 붙은 음식 1건."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, stage=stage, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(db)
    # `food_ref_id` 를 명시한다 — 팩토리 기본값이 None(성분 못 구한 항목)이라
    # 빼면 모든 응답에 NUTRITION_NOT_MATCHED 가 붙는다.
    make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_TEST_01")
    return user, meal


# ── 명세 응답 형식 ─────────────────────────────────────────────


def test_confirm_returns_exactly_the_spec_fields(client: TestClient, db: Session) -> None:
    """명세 **JSON 예시**의 8필드. 초과도 누락도 없다.

    ⚠️ 명세가 자기 안에서 어긋난다 — 엔드포인트 표(`CLAUDE.md` 「평가 · 피드백」)는
    이 응답을 "Q·Q·S + `quantityBasis` + 영양소" 라고 적었는데 JSON 예시에는
    `quantityBasis` 가 없다. Quantity 채점 기준선도 미정이라 낼 값이 없어서
    JSON 예시를 따랐다. 명세를 어느 쪽으로든 고쳐야 한다.
    """
    user, meal = _ready_meal(db)

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm",
        headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True and body["error"] is None
    assert set(body["data"]) == _SPEC_FIELDS


def test_evaluation_is_confirm_minus_feedback_status(
    client: TestClient, db: Session
) -> None:
    """명세: "confirm 응답과 동일 (`feedbackStatus` 제외)"."""
    user, meal = _ready_meal(db)
    confirmed = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]

    fetched = client.get(
        f"/api/v1/meals/{meal.id}/evaluation", headers=_h(user.id)
    ).json()["data"]

    assert set(fetched) == _SPEC_FIELDS - {"feedbackStatus"}
    assert {k: v for k, v in confirmed.items() if k != "feedbackStatus"} == fetched


def test_confirm_marks_the_meal_evaluated(client: TestClient, db: Session) -> None:
    user, meal = _ready_meal(db)

    data = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]

    assert data["status"] == "EVALUATED"
    assert data["feedbackStatus"] == "PENDING"  # 점수는 났지만 문장은 아직


def test_satiety_score_is_the_reported_value(client: TestClient, db: Session) -> None:
    """명세 예시: 요청 satietyAfterPct 68 → 응답 scores.satiety 68."""
    user, meal = _ready_meal(db)

    data = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]

    assert data["scores"]["satiety"] == 68


def test_nutrients_match_the_spec_shape(client: TestClient, db: Session) -> None:
    """명세의 세 가지 + 목표치는 미확정이라 null."""
    user, meal = _ready_meal(db)

    rows = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]["nutrients"]

    assert [r["code"] for r in rows] == ["PROTEIN", "FIBER", "SODIUM"]
    assert [r["unit"] for r in rows] == ["g", "g", "mg"]
    for row in rows:
        assert set(row) == {"code", "label", "current", "target", "unit", "state"}
        assert row["target"] is None and row["state"] is None
        # 숫자로 나가야 한다 — Decimal 이 문자열로 새면 FE 비교가 깨진다
        assert isinstance(row["current"], (int, float))


def test_stage_emphasis_follows_the_stage(client: TestClient, db: Session) -> None:
    """강조축은 **그 단계에서 무엇이 중요한가**다. 명세 예시가 MAINTENANCE 다.

    오늘 그 축을 매길 수 있는지와는 별개다 — Quantity·Quality 기준선이 미정이라
    점수가 null 이어도 단계의 의미는 변하지 않는다. 점수가 null 인 축을 어떻게
    그릴지는 FE 가 정한다.
    """
    user, meal = _ready_meal(db, stage=MedicationStage.MAINTENANCE)

    data = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]

    assert data["stage"] == "MAINTENANCE"
    assert data["stageEmphasis"] == ["SATIETY", "QUALITY"]   # 명세 예시 그대로
    assert data["scores"]["quality"] is None                 # 점수는 아직 없어도


def test_stage_emphasis_is_never_empty(client: TestClient, db: Session) -> None:
    """어느 단계든 강조할 축이 하나는 있다 — FE 가 기준을 잃으면 안 된다."""
    for stage in MedicationStage:
        user = make_user(db)
        meal = make_meal(
            db, user_id=user.id, stage=stage, status=MealStatus.REVIEW_REQUIRED
        )
        data = client.post(
            f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
            json={"satietyAfterPct": 68},
        ).json()["data"]
        assert data["stageEmphasis"], stage


# ── 에러 ───────────────────────────────────────────────────────


def test_evaluation_before_confirm_is_409_not_confirmed(
    client: TestClient, db: Session
) -> None:
    """명세: 확정 전 호출 시 409 · NOT_CONFIRMED."""
    user, meal = _ready_meal(db)

    res = client.get(f"/api/v1/meals/{meal.id}/evaluation", headers=_h(user.id))

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NOT_CONFIRMED"


def test_analyzing_meal_cannot_be_confirmed(client: TestClient, db: Session) -> None:
    """**최초** 분석 중에는 확정할 수 없다 — 워커가 항목을 갈아엎는 중이다.

    `is_recalculation` 은 기본값 False 다. 아래 재계산 테스트와 짝이다 — 같은
    `ANALYZING` 인데 이 플래그 하나로 갈린다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.ANALYZING)

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )

    assert res.status_code == 409


def test_recalculating_meal_can_be_confirmed(client: TestClient, db: Session) -> None:
    """음식을 고친 뒤(`ANALYZING` + `isRecalculation`)에는 확정할 수 있어야 한다.

    `PATCH`·`POST`·`DELETE /meals/{mealId}/items` 가 식사를 이 상태로 되돌린다.
    여기서 막으면 **음식을 고친 사용자는 영원히 확정하지 못한다** — 재계산을 푸는
    경로가 확정뿐이라 스피너에서 빠져나올 방법이 없다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.ANALYZING)
    meal.is_recalculation = True
    make_food_ref(db)
    make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_TEST_01")
    db.flush()

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )

    assert res.status_code == 200, res.text
    db.expire_all()
    assert meal.status is MealStatus.EVALUATED


def test_other_users_meal_is_404(client: TestClient, db: Session) -> None:
    """남의 식사는 404 다 — "그 mealId 는 존재한다" 가 새면 안 된다.

    없는 식사·삭제된 식사도 같은 404 이고, 각각 `test_error_codes_are_explicit`
    와 `test_deleted_meal_is_404_like_the_others` 에서 본다.
    """
    _, meal = _ready_meal(db)
    stranger = make_user(db, nickname="남")

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(stranger.id),
        json={"satietyAfterPct": 68},
    )

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_meal_is_404(client: TestClient, db: Session) -> None:
    user = make_user(db)

    res = client.get(f"/api/v1/meals/{uuid.uuid4()}/evaluation", headers=_h(user.id))

    assert res.status_code == 404


def test_without_header_is_401(client: TestClient, db: Session) -> None:
    _, meal = _ready_meal(db)

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", json={"satietyAfterPct": 68}
    )

    assert res.status_code == 401


def test_out_of_range_satiety_is_422(client: TestClient, db: Session) -> None:
    user, meal = _ready_meal(db)

    for bad in (-1, 101):
        res = client.post(
            f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
            json={"satietyAfterPct": bad},
        )
        assert res.status_code == 422, bad


def test_typo_field_is_rejected(client: TestClient, db: Session) -> None:
    """extra="forbid" — 오타를 조용히 무시하면 포만감 없이 확정된다."""
    user, meal = _ready_meal(db)

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPctt": 68},
    )

    assert res.status_code == 422


# ── 기준선 미정: 점수는 null 로 나간다 ─────────────────────────


def test_unscored_axes_are_null_not_zero(client: TestClient, db: Session) -> None:
    """명세가 Q/Q 계산식을 주지 않았다. 0 이 아니라 null 이어야 한다.

    0 을 쓰면 "못 쟀다" 와 "바닥이다" 가 같은 값이 되어 FE 가 게이지를 0 으로
    그린다. `qqs_evaluations.*_score` 가 NULL 허용인 것도 같은 이유다.
    """
    user, meal = _ready_meal(db)

    scores = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]["scores"]

    assert scores == {"quantity": None, "quality": None, "satiety": 68}


def test_nutrient_totals_are_still_reported(client: TestClient, db: Session) -> None:
    """점수는 안 매겨도 성분 합계는 나간다 — 계산이 아니라 집계다.

    기준량 100g 짜리를 200g 먹었으면 성분은 두 배다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(
        db,
        serving_size=Decimal("100"),
        protein_g=Decimal("9"),
        fiber_g=Decimal("3"),
        sodium_mg=Decimal("810"),
    )
    make_meal_item(
        db,
        meal_id=meal.id,
        food_ref_id="KFD_TEST_01",
        confirmed_amount=Decimal("200"),
        confirmed_unit="g",
    )

    rows = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 50},
    ).json()["data"]["nutrients"]

    assert [r["current"] for r in rows] == [18.0, 6.0, 1620.0]


# ── 재계산 대기로 돌아가면 옛 점수를 내보내지 않는다 ───────────


def test_evaluation_is_gated_on_status_not_row(client: TestClient, db: Session) -> None:
    """확정 뒤 식사가 재계산 대기로 돌아가면 409 다.

    `qqs_evaluations` 행은 이력으로 남겨 두되, 무효가 된 점수를 조회로 내보내지는
    않는다. 지금은 `POST /items` 가 없어 상태를 직접 되돌려 재현한다.
    """
    user, meal = _ready_meal(db)
    client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )
    assert client.get(
        f"/api/v1/meals/{meal.id}/evaluation", headers=_h(user.id)
    ).status_code == 200

    # 사용자가 음식을 고친 상황 — POST /meals/{mealId}/items 가 하는 일이다
    meal.status = MealStatus.ANALYZING
    db.flush()

    res = client.get(f"/api/v1/meals/{meal.id}/evaluation", headers=_h(user.id))

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NOT_CONFIRMED"


# ── 성분 합계가 불완전하면 알린다 ──────────────────────────────


def test_partial_nutrition_is_flagged_not_silent(
    client: TestClient, db: Session
) -> None:
    """합산에서 빠진 음식이 있으면 200 안에 NUTRITION_NOT_MATCHED 를 싣는다.

    숫자만 내보내면 사용자는 적게 나온 단백질을 완전한 값으로 믿고 다음 끼니를
    조절한다. 실패가 아니라 단서라 success 는 참이고 data 도 있다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(db, food_ref_id="KFD_A", name="밥", protein_g="9")
    make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_A")
    make_meal_item(db, meal_id=meal.id, food_ref_id=None, display_name="김치찌개")

    body = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()

    assert body["success"] is True
    assert body["data"] is not None
    assert body["error"]["code"] == "NUTRITION_NOT_MATCHED"


def test_complete_nutrition_has_no_flag(client: TestClient, db: Session) -> None:
    """전부 합산됐으면 단서가 붙지 않는다."""
    user, meal = _ready_meal(db)

    body = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()

    assert body["error"] is None


def test_nutrition_sources_reflect_what_was_summed(
    client: TestClient, db: Session
) -> None:
    """evidence 는 실제로 합산된 것만 말한다.

    항목의 존재가 아니라 합산 여부로 판단한다 — `food_ref_id` 가 없는 항목은
    "사용자가 성분을 입력했다"(USER_INPUT)가 아니라 "성분을 모른다" 다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_meal_item(db, meal_id=meal.id, food_ref_id=None, display_name="김치찌개")

    data = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]

    assert data["evidence"]["nutritionSources"] == []
    assert [r["current"] for r in data["nutrients"]] == [None, None, None]


def test_matched_item_without_amount_is_not_counted(
    client: TestClient, db: Session
) -> None:
    """음식은 찾았지만 먹은 양을 모르면 합산할 수 없다 — 출처도 비어야 한다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(db, food_ref_id="KFD_A", name="밥")
    make_meal_item(
        db,
        meal_id=meal.id,
        food_ref_id="KFD_A",
        estimated_amount=None,
        estimated_unit=None,
    )

    data = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]

    assert data["evidence"]["nutritionSources"] == []


def test_confirmed_amount_never_falls_back_to_the_ai_guess(
    client: TestClient, db: Session
) -> None:
    """사용자가 "2개" 로 고쳤으면 AI 가 말한 250g 으로 계산하지 않는다.

    `confirmed_amount_g` 는 g 으로 환산된 값만 담아서 "2개" 는 NULL 이다.
    그 NULL 을 "확인 전" 으로 읽고 `estimated_amount_g` 로 폴백하면 **사용자가
    방금 부정한 값**이 합계에 들어간다 — 그리고 `counted` 가 1 이라 경고도 안 붙어서,
    사용자는 자기가 고친 값이 반영된 줄 알고 그 숫자를 믿는다.

    빠뜨리는 것보다 나쁘다. 합산에서 빼고 단서를 붙이는 게 맞다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(db, food_ref_id="KFD_A", name="밥")
    make_meal_item(
        db,
        meal_id=meal.id,
        food_ref_id="KFD_A",
        estimated_amount=Decimal("250.00"),  # AI 추정은 g 으로 환산된다
        estimated_unit="g",
        confirmed_amount=Decimal("2"),  # 사용자가 고친 값은 환산되지 않는다
        confirmed_unit="개",
    )

    body = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()

    assert body["data"]["evidence"]["nutritionSources"] == []
    for nutrient in body["data"]["nutrients"]:
        assert nutrient["current"] is None, nutrient
    assert body["error"]["code"] == "NUTRITION_NOT_MATCHED"
    assert "양을 모" not in body["error"]["message"]


def test_empty_meal_confirms_without_nutrition(
    client: TestClient, db: Session
) -> None:
    """항목이 하나도 없는 식사도 확정된다 — 뺄 것도 없으니 단서도 없다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)

    body = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()

    assert body["error"] is None
    assert body["data"]["evidence"]["nutritionSources"] == []


# ── 재확정 ─────────────────────────────────────────────────────


def test_reconfirm_overwrites_instead_of_stacking(
    client: TestClient, db: Session
) -> None:
    """재확정은 행을 쌓지 않고 덮는다. `(meal_id)` UNIQUE 라 upsert 다."""
    from app.crud import evaluation as evaluation_crud

    user, meal = _ready_meal(db)
    client.post(f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
                json={"satietyAfterPct": 68})

    data = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 20},
    ).json()["data"]

    assert data["scores"]["satiety"] == 20
    rows = db.execute(
        text("SELECT count(*) FROM qqs_evaluations WHERE meal_id = :m"),
        {"m": meal.id},
    ).scalar()
    assert rows == 1
    assert evaluation_crud.get_by_meal(db, meal.id) is not None


def test_reconfirm_keeps_satiety_before(client: TestClient, db: Session) -> None:
    """식전 포만감은 식사 등록 때 받은 값이라 확정이 덮으면 안 된다."""
    user, meal = _ready_meal(db)
    db.execute(
        text(
            "INSERT INTO satiety_logs (id, meal_id, satiety_before, logged_at) "
            "VALUES (gen_random_uuid(), :m, 20, now())"
        ),
        {"m": meal.id},
    )

    client.post(f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
                json={"satietyAfterPct": 68})

    row = db.execute(
        text("SELECT satiety_before, satiety_after FROM satiety_logs WHERE meal_id = :m"),
        {"m": meal.id},
    ).one()
    assert row.satiety_before == 20
    assert row.satiety_after == 68


# ── 에러는 status 가 아니라 code 로 분기한다 ───────────────────


def test_error_codes_are_explicit(client: TestClient, db: Session) -> None:
    """명세: "분기 기준 error.code (HTTP status 아님)"."""
    user = make_user(db)
    analyzing = make_meal(db, user_id=user.id, status=MealStatus.ANALYZING)

    res = client.post(
        f"/api/v1/meals/{analyzing.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )
    assert (res.status_code, res.json()["error"]["code"]) == (409, "CONFLICT")

    res = client.get(f"/api/v1/meals/{uuid.uuid4()}/evaluation", headers=_h(user.id))
    assert (res.status_code, res.json()["error"]["code"]) == (404, "NOT_FOUND")


def test_deleted_meal_is_404_like_the_others(client: TestClient, db: Session) -> None:
    """삭제된 식사도 남의 식사·없는 식사와 같은 404 다."""
    user, meal = _ready_meal(db)
    db.execute(text("UPDATE meals SET deleted_at = now() WHERE id = :m"), {"m": meal.id})

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_every_stage_has_an_emphasis_entry() -> None:
    """단계를 추가하면 여기서 먼저 깨진다 — 런타임 KeyError 로 500 나기 전에."""
    from app.models.enums import MedicationStage
    from app.services.evaluation.stage_profile import STAGE_EMPHASIS

    assert set(STAGE_EMPHASIS) == set(MedicationStage)


def test_missing_amount_says_so_instead_of_blaming_nutrition(
    client: TestClient, db: Session
) -> None:
    """양을 모르는 건 "영양정보를 못 찾았다" 가 아니다.

    명세상 `NUTRITION_NOT_MATCHED` 의 FE 처리는 "해당 항목 직접 입력" 이라
    영양정보 입력 화면으로 보낸다. 성분은 이미 찾았고 양이 없는 경우라면 거기서
    아무리 입력해도 안 풀린다 — 막다른 길이다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(db, food_ref_id="KFD_A", name="밥", protein_g="9")
    make_meal_item(
        db,
        meal_id=meal.id,
        food_ref_id="KFD_A",
        estimated_amount=None,
        estimated_unit=None,
    )

    body = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()

    assert "양을 g" in body["error"]["message"]
    assert "영양정보를 찾지 못한" not in body["error"]["message"]
    # "양을 모른다" 고 쓰지 않는다 — 사용자가 "2개" 라고 말한 경우도 여기로 온다.
    assert "양을 모" not in body["error"]["message"]


def test_unmatched_food_still_says_nutrition_not_found(
    client: TestClient, db: Session
) -> None:
    """반대로 이름 매칭 실패는 영양정보 문제가 맞다 — 안내가 섞이면 안 된다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_meal_item(db, meal_id=meal.id, food_ref_id=None, display_name="김치찌개")

    body = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()

    assert "영양정보를 찾지 못한" in body["error"]["message"]


def test_reconfirm_keeps_the_original_logged_at(
    client: TestClient, db: Session
) -> None:
    """식후 포만감을 덮어써도 기록 시각은 그대로다.

    한 행에 식전·식후 두 값이 들어 있어 타임스탬프 하나가 둘 다를 뜻할 수 없다.
    """
    user, meal = _ready_meal(db)
    client.post(f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
                json={"satietyAfterPct": 68})
    first = db.execute(
        text("SELECT logged_at FROM satiety_logs WHERE meal_id = :m"), {"m": meal.id}
    ).scalar()

    client.post(f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
                json={"satietyAfterPct": 20})

    row = db.execute(
        text("SELECT logged_at, satiety_after FROM satiety_logs WHERE meal_id = :m"),
        {"m": meal.id},
    ).one()
    assert row.logged_at == first      # 시각은 그대로
    assert row.satiety_after == 20     # 값은 갱신


def test_nullable_response_fields_are_required_in_the_schema() -> None:
    """값이 없어도 키는 항상 나간다 — 생성 클라이언트가 optional 로 받으면 안 된다."""
    from app.main import app

    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["QqsScores"]["required"]) == {"quantity", "quality", "satiety"}
    assert "current" in schemas["NutrientRow"]["required"]
    assert "target" in schemas["NutrientRow"]["required"]
