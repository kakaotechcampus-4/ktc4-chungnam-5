"""POST /meals/{mealId}/confirm · GET /meals/{mealId}/evaluation 의 HTTP 계약.

FE 가 실제로 보는 모양 — 응답 래퍼 · camelCase · 명세 필드 · 404 단일화 · 409 코드.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import evaluation as evaluation_crud
from app.crud import meal as meal_crud
from app.models.enums import MealStatus, MedicationStage
from app.tests.factories import (
    make_food_ref,
    make_meal,
    make_meal_item,
    make_user,
    make_weight,
)

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
    """명세의 세 줄. 목표는 끼니 기준(하루 ÷ 3)이다. 체중이 없으면 단백질 목표만 null."""
    user, meal = _ready_meal(db)

    rows = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]["nutrients"]

    assert [r["code"] for r in rows] == ["PROTEIN", "FIBER", "SODIUM"]
    assert [r["unit"] for r in rows] == ["g", "g", "mg"]
    for row in rows:
        assert set(row) == {"code", "label", "current", "target", "unit", "state"}
        # 숫자로 나가야 한다 — Decimal 이 문자열로 새면 FE 비교가 깨진다
        assert isinstance(row["current"], (int, float))
    protein, fiber, sodium = rows
    assert protein["target"] is None and protein["state"] is None   # 체중 없음
    assert (fiber["target"], fiber["state"]) == (8.3, "SHORT")      # 25 ÷ 3
    assert (sodium["target"], sodium["state"]) == (766.7, "OVER")   # 2300 ÷ 3


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


# ── 채점 (docs/be-qqs-scoring-rule.md) ──────────────────────────


def _meal_with(
    db: Session,
    *,
    stage=MedicationStage.MAINTENANCE,
    weight=True,
    food_ref_id="KFD_TEST_01",
    **food,
):
    """음식 1건(250g)짜리 식사. `food` 는 100g 당 성분이다."""
    user = make_user(db)
    if weight:
        make_weight(db, user_id=user.id)                       # 70kg
    meal = make_meal(db, user_id=user.id, stage=stage, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(db, food_ref_id=food_ref_id, **food)
    make_meal_item(db, meal_id=meal.id, food_ref_id=food_ref_id)
    return user, meal


def _confirm(client: TestClient, user, meal) -> dict:
    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_same_meal_scores_differently_by_stage(client: TestClient, db: Session) -> None:
    """700kcal = 평소의 100%. 유지기는 15%p 초과 / T30 → 50, 감량기는 10%p 초과 / T15 → 33."""
    user, meal = _meal_with(db, calories=Decimal("280"))
    assert _confirm(client, user, meal)["scores"]["quantity"] == 50

    user, meal = _meal_with(
        db, stage=MedicationStage.REDUCED, food_ref_id="KFD_TEST_02", calories=Decimal("280")
    )
    assert _confirm(client, user, meal)["scores"]["quantity"] == 33


# 100g 당 단백질 5.6 · 식이섬유 1.6 · 나트륨 200 → 250g 에 14g · 4g · 500mg.
# 70kg 유지기 목표 28g · 8.33g · 766.7mg → 0.5 · 0.48 · 1 → 평균 0.66.
_BALANCED = dict(protein_g=Decimal("5.6"), fiber_g=Decimal("1.6"), sodium_mg=Decimal("200"))


def test_quality_is_scored_and_targets_filled(client: TestClient, db: Session) -> None:
    user, meal = _meal_with(db, **_BALANCED)

    data = _confirm(client, user, meal)

    assert data["scores"]["quality"] == 66
    rows = {r["code"]: r for r in data["nutrients"]}
    assert (rows["PROTEIN"]["target"], rows["PROTEIN"]["state"]) == (28.0, "SHORT")
    assert (rows["FIBER"]["target"], rows["FIBER"]["state"]) == (8.3, "SHORT")
    assert (rows["SODIUM"]["target"], rows["SODIUM"]["state"]) == (766.7, "OK")
    assert data["evidence"]["stageRuleVersion"] == "v2"


def test_evaluation_view_equals_confirm_with_targets(client: TestClient, db: Session) -> None:
    """목표치는 조회 때 다시 계산한다. 그래도 confirm 과 같아야 한다 (Review Focus 5)."""
    user, meal = _meal_with(db, **_BALANCED)
    confirmed = _confirm(client, user, meal)

    fetched = client.get(
        f"/api/v1/meals/{meal.id}/evaluation", headers=_h(user.id)
    ).json()["data"]

    assert {k: v for k, v in confirmed.items() if k != "feedbackStatus"} == fetched


def test_missing_fiber_is_dropped_not_zeroed(client: TestClient, db: Session) -> None:
    """공공 DB 에 식이섬유가 비어 있으면 그 항목만 뺀다 — (0.5 + 1) / 2 = 75 (Review Focus 3)."""
    user, meal = _meal_with(db, **{**_BALANCED, "fiber_g": None})

    data = _confirm(client, user, meal)

    assert data["scores"]["quality"] == 75
    fiber = next(r for r in data["nutrients"] if r["code"] == "FIBER")
    assert fiber["current"] is None and fiber["state"] is None


def test_excluded_item_is_scored_on_the_rest_with_warning(
    client: TestClient, db: Session
) -> None:
    """성분을 못 구한 음식은 빼고 매기되, 경고가 **같이** 나가야 한다 (Review Focus 1).

    결정 A (spec 「7. 값이 없을 때의 처리」): 점수가 늘 나오는 대신 빠진 만큼 낮게 나올 수 있다. 경고가
    사용자에게 그 사실을 알리는 유일한 장치라 여기서 함께 못 박는다.
    """
    user, meal = _meal_with(db, calories=Decimal("280"), **_BALANCED)
    make_meal_item(db, meal_id=meal.id, display_name="이름모를음식")   # food_ref_id=None

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["error"]["code"] == "NUTRITION_NOT_MATCHED"
    # 매칭된 음식만: 700kcal(유지기 100% → 50), 품질은 _BALANCED 그대로 66
    assert body["data"]["scores"] == {"quantity": 50, "quality": 66, "satiety": 68}


def test_weight_recorded_after_meal_falls_back_to_latest(
    client: TestClient, db: Session
) -> None:
    """먹은 시각 이전 체중이 없으면 가장 최근 체중을 쓴다 (Review Focus 2)."""
    user, meal = _meal_with(db, weight=False, **_BALANCED)
    make_weight(db, user_id=user.id, recorded_at=meal.eaten_at + timedelta(days=1))

    assert _confirm(client, user, meal)["scores"]["quality"] == 66


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


# ── 근거가 없으면 null ─────────────────────────────────────


def test_quality_is_null_without_weight(client: TestClient, db: Session) -> None:
    """단백질 목표가 체중에서 나온다. 체중을 모르면 0 이 아니라 null 이다."""
    user, meal = _ready_meal(db)

    scores = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]["scores"]

    assert scores["quality"] is None
    assert scores["satiety"] == 68
    # 125kcal / 평소 700kcal = 17.9% — 유지기 하한 60 에서 30%p 넘게 부족하다
    assert scores["quantity"] == 0


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

    행은 `mark_recalculating` 이 이미 지웠으므로 `row is None` 으로도 409 다. 상태
    검사는 그 뒤를 받친다 — 행이 어떤 이유로 남아도 여기서는 안 나간다.
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


def test_missing_nutrient_column_is_not_a_partial_sum(
    client: TestClient, db: Session
) -> None:
    """성분 값이 비어 있는 음식이 섞이면 그 성분은 합계를 내보내지 않는다.

    `food_refs` 의 성분 컬럼은 전부 nullable 이고, 공공 DB 에 식이섬유·나트륨이
    비어 있는 행이 흔하다(시드 로더가 파싱 실패를 NULL 로 넣는다).

    SQL 의 `SUM` 은 NULL 입력 행을 조용히 건너뛴다. 항목 단위로는 둘 다 "계산됨"
    (`counted=2`)이라 경고가 안 붙는데, 단백질만 한 항목 몫이 빠진다 — **같은 응답
    안에서 단백질은 부분합이고 식이섬유는 완전한** 상태가 되고 겉으로는 구분이 안 된다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(
        db,
        food_ref_id="KFD_RICE",
        name="밥",
        serving_size=Decimal("100"),
        protein_g=Decimal("9"),
        fiber_g=Decimal("3"),
    )
    make_food_ref(
        db,
        food_ref_id="KFD_GIM",
        name="김",
        serving_size=Decimal("100"),
        protein_g=None,  # 공공 DB 결측
        fiber_g=Decimal("1"),
    )
    for food_ref_id in ("KFD_RICE", "KFD_GIM"):
        make_meal_item(
            db,
            meal_id=meal.id,
            food_ref_id=food_ref_id,
            confirmed_amount=Decimal("100"),
            confirmed_unit="g",
        )

    data = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["data"]
    current = {row["code"]: row["current"] for row in data["nutrients"]}

    assert current["PROTEIN"] is None, "결측이 있으면 부분합(9)을 내보내면 안 된다"
    assert current["FIBER"] == 4.0, "둘 다 값이 있는 성분은 그대로 합산한다"


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


def test_editing_food_removes_the_stale_evaluation(
    client: TestClient, db: Session
) -> None:
    """확정한 뒤 음식을 고치면 옛 점수가 남지 않는다.

    상태만 되돌리고 행을 남기면 상태를 안 보는 쿼리가 무효 점수를 그대로 내보낸다 —
    `list_meals` · `get_calendar_summary` 와 `crud/dashboard.py` 의 집계 넷, 여섯
    곳이다. 같은 식사가 목록에는 옛 점수를, 이 엔드포인트에는 409 를 내게 된다.
    """
    user, meal = _ready_meal(db)
    client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )
    assert evaluation_crud.get_by_meal(db, meal.id) is not None

    meal_crud.mark_recalculating(db, meal)
    db.flush()

    assert evaluation_crud.get_by_meal(db, meal.id) is None


def test_editing_before_confirm_is_harmless(client: TestClient, db: Session) -> None:
    """확정 전 수정에는 지울 행이 없다 — 0 행 DELETE 라 그냥 넘어간다.

    호출부가 "확정된 적 있나" 를 따로 따지지 않아도 되는 근거다.
    """
    _, meal = _ready_meal(db)

    meal_crud.mark_recalculating(db, meal)
    db.flush()

    assert meal.status is MealStatus.ANALYZING
    assert meal.is_recalculation is True


def test_list_and_calendar_stop_showing_the_stale_score(
    client: TestClient, db: Session
) -> None:
    """리뷰에서 지적된 증상 자체를 본다 — 목록·달력이 옛 점수를 더 이상 안 보인다.

    `get_by_meal` 만 확인하면 "행이 지워졌다" 는 사실은 알아도 **그게 화면에 닿는지**
    는 모른다. 이 둘은 상태를 안 보고 `qqs_evaluations` 를 join 하는 쪽이라, 여기서
    사라져야 고쳐진 것이다.

    `crud/dashboard.py` 의 집계 넷도 같은 모양이지만 그 파일은 이 브랜치에 아직
    없다 — 같은 행을 join 하므로 행이 없으면 같이 비어야 맞다.
    """
    user, meal = _ready_meal(db)
    client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )
    before = meal_crud.list_meals(db, user_id=user.id, limit=10)[0]
    # `satiety_score` 로 본다 — 이 픽스처는 영양 매칭이 없어 `quantity_score` 가
    # 확정 직후에도 NULL 이라, 그걸 기준으로 삼으면 지워졌는지 구분되지 않는다.
    assert before.satiety_score is not None

    meal_crud.mark_recalculating(db, meal)
    db.flush()

    after = meal_crud.list_meals(db, user_id=user.id, limit=10)[0]
    assert (after.quantity_score, after.quality_score, after.satiety_score) == (
        None, None, None,
    )

    month = meal.eaten_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    summary = meal_crud.get_calendar_summary(
        db,
        user_id=user.id,
        month_start=month,
        month_end=month + timedelta(days=40),
    )
    # 식사는 그대로 세고(총 1끼) 평균에서만 빠진다 — 끼니를 먹은 사실은 유효하다.
    assert summary.total_meals == 1
    assert summary.avg_satiety is None


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


def test_both_exclusion_reasons_are_reported_together(
    client: TestClient, db: Session
) -> None:
    """제외 사유가 둘이면 한 메시지에 둘 다 담는다.

    먼저 걸리는 하나만 내보내면, 사용자가 영양정보를 다 채우고 다시 확정해야
    비로소 양 안내를 본다 — 고칠 게 둘인 걸 모른 채 왕복하게 된다.
    """
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)
    make_food_ref(db, food_ref_id="KFD_A", name="밥")
    # 성분을 못 구한 항목
    make_meal_item(db, meal_id=meal.id, food_ref_id=None, display_name="김치찌개")
    # 성분은 구했는데 양이 g 으로 안 잡히는 항목
    make_meal_item(
        db,
        meal_id=meal.id,
        food_ref_id="KFD_A",
        estimated_amount=None,
        estimated_unit=None,
    )

    error = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    ).json()["error"]

    assert "영양정보를 찾지 못한" in error["message"]
    assert "양을 g 으로 환산하지 못한" in error["message"]


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


def test_state_agrees_with_the_displayed_target(client: TestClient, db: Session) -> None:
    """화면에 보이는 숫자끼리 모순되면 안 된다 — `8.3 / 8.3` 인데 SHORT 로 나가면 안 된다.

    식이섬유 목표는 25 ÷ 3 = 8.333… 이고 8.3 으로 내보낸다. 섭취 8.3g 은 그 표시값을
    채웠으므로 OK 다.
    """
    user, meal = _meal_with(db, fiber_g=Decimal("3.32"))          # 250g → 8.3g

    fiber = next(r for r in _confirm(client, user, meal)["nutrients"] if r["code"] == "FIBER")

    assert (fiber["current"], fiber["target"], fiber["state"]) == (8.3, 8.3, "OK")
