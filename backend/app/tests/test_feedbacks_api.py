"""GET /meals/{mealId}/feedback · POST /meals/{mealId}/satiety-checkins 의 HTTP 계약.

FE 가 실제로 보는 모양 — 응답 래퍼 · camelCase · 명세 필드 · 404 단일화 · 마스킹.

**피드백 행은 직접 넣는다.** 채우는 워커(`worker/jobs/feedback_meal.py`)가 아직
스텁이라 API 로는 만들 수 없다. 워커가 붙으면 이 픽스처가 그 계약이 된다.
"""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import MealStatus, SafetyStatus
from app.models.feedback import MealFeedback
from app.tests.factories import make_food_ref, make_meal, make_user

_SPEC_FIELDS = {
    "feedbackStatus", "summary", "reasoning",
    "suggestions", "expectedSatietyPct", "safetyStatus",
}


def _h(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def _meal(db: Session):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    return user, meal


def _add_feedback(
    db: Session,
    meal,
    *,
    safety: SafetyStatus = SafetyStatus.SAFE,
    suggestions: list[dict] | None = None,
) -> MealFeedback:
    """워커가 만들 행을 흉내 낸다. `suggestions` 는 AI 계약 모양 그대로다."""
    row = MealFeedback(
        user_id=meal.user_id,
        meal_id=meal.id,
        body="유지기 기준 포만감이 부족한 식사예요.",
        reasoning="단백질 비중이 낮았어요.",
        suggestions=suggestions,
        model_version="stub-1",
        safety_status=safety,
    )
    db.add(row)
    db.flush()
    return row


# ── GET /meals/{mealId}/feedback ────────────────────────────────


def test_feedback_returns_exactly_the_spec_fields(client: TestClient, db: Session) -> None:
    """명세 6필드. 초과도 누락도 없다."""
    user, meal = _meal(db)
    _add_feedback(db, meal)

    res = client.get(f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id))

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True and body["error"] is None
    assert set(body["data"]) == _SPEC_FIELDS


def test_missing_feedback_is_pending_not_404(client: TestClient, db: Session) -> None:
    """아직 없는 건 에러가 아니다 — 404 로 내리면 "없는 식사" 와 구분되지 않는다."""
    user, meal = _meal(db)

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "PENDING"
    assert data["summary"] is None
    assert data["suggestions"] == []
    assert data["safetyStatus"] is None


def test_ready_feedback_carries_the_text(client: TestClient, db: Session) -> None:
    user, meal = _meal(db)
    _add_feedback(db, meal)

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "READY"
    assert data["summary"] == "유지기 기준 포만감이 부족한 식사예요."
    assert data["reasoning"] == "단백질 비중이 낮았어요."
    assert data["safetyStatus"] == "SAFE"


def test_blocked_feedback_hides_the_text(client: TestClient, db: Session) -> None:
    """명세: `safetyStatus: BLOCKED` → summary · suggestions 미표시.

    AI 가 의료 판단에 닿는 문장을 썼을 때 그대로 내보내면 안 된다 (절대 규칙 1).
    `safetyStatus` 는 실제 값을 실어 FE 가 상담 안내로 바꿀 수 있게 한다.
    """
    user, meal = _meal(db)
    _add_feedback(
        db, meal, safety=SafetyStatus.BLOCKED,
        suggestions=[{"foodName": "두부", "advice": "더 드세요", "candidateFoodRefId": None}],
    )

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["summary"] is None
    assert data["reasoning"] is None
    assert data["suggestions"] == []
    assert data["safetyStatus"] == "BLOCKED"
    assert data["feedbackStatus"] == "READY"  # 생성은 끝났다


def test_unreviewed_feedback_is_hidden_too(client: TestClient, db: Session) -> None:
    """`REVIEW_REQUIRED` 도 막는다 — 그게 컬럼의 기본값이다.

    `safety_status` 의 server_default 가 `SAFE` 가 아니라 `REVIEW_REQUIRED` 다
    ("가드레일을 통과해야만 SAFE 가 된다"). 예외적인 BLOCKED 만 막으면 **검수 전
    행 전부**가 새어 나간다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal, safety=SafetyStatus.REVIEW_REQUIRED)

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["summary"] is None
    assert data["safetyStatus"] == "REVIEW_REQUIRED"


def test_suggestion_nutrients_come_from_food_refs(client: TestClient, db: Session) -> None:
    """`nutrients` 는 저장돼 있지 않다 — `candidateFoodRefId` 로 조회해 채운다."""
    user, meal = _meal(db)
    make_food_ref(
        db, food_ref_id="KFD_TOFU", name="두부",
        protein_g=Decimal("10"), fiber_g=Decimal("2"),
    )
    _add_feedback(
        db, meal,
        suggestions=[
            {"foodName": "두부 반 모", "advice": "단백질을 채워요",
             "candidateFoodRefId": "KFD_TOFU"}
        ],
    )

    (suggestion,) = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]["suggestions"]

    assert set(suggestion) == {"foodName", "nutrients", "advice"}
    assert suggestion["foodName"] == "두부 반 모"
    assert {n["code"]: n["amountG"] for n in suggestion["nutrients"]} == {
        "PROTEIN": 10.0, "FIBER": 2.0,
    }


def test_dead_food_ref_keeps_the_advice(client: TestClient, db: Session) -> None:
    """참조를 못 찾아도 제안을 버리지 않는다 — 문구는 그대로 쓸모가 있다."""
    user, meal = _meal(db)
    _add_feedback(
        db, meal,
        suggestions=[
            {"foodName": "미역국", "advice": "국물을 곁들여요",
             "candidateFoodRefId": "KFD_GONE"}
        ],
    )

    (suggestion,) = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]["suggestions"]

    assert suggestion["advice"] == "국물을 곁들여요"
    assert suggestion["nutrients"] == []


def test_expected_satiety_is_null(client: TestClient, db: Session) -> None:
    """담을 컬럼도 AI 응답 필드도 없다. 명세 필드라 키는 두되 값이 없다."""
    user, meal = _meal(db)
    _add_feedback(db, meal)

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["expectedSatietyPct"] is None


def test_feedback_of_other_users_meal_is_404(client: TestClient, db: Session) -> None:
    _, meal = _meal(db)
    other = make_user(db, nickname="남")

    res = client.get(f"/api/v1/meals/{meal.id}/feedback", headers=_h(other.id))

    assert res.status_code == 404


def test_feedback_without_header_is_401(client: TestClient, db: Session) -> None:
    _, meal = _meal(db)

    assert client.get(f"/api/v1/meals/{meal.id}/feedback").status_code == 401


# ── POST /meals/{mealId}/satiety-checkins ───────────────────────


def _checkin(client: TestClient, user_id: uuid.UUID, meal_id: uuid.UUID, **body):
    payload = {"checkinOffsetHours": 3, "satietyPct": 40} | body
    return client.post(
        f"/api/v1/meals/{meal_id}/satiety-checkins",
        headers=_h(user_id),
        json=payload,
    )


def test_checkin_returns_exactly_the_spec_fields(client: TestClient, db: Session) -> None:
    """명세 5필드. `comment` 는 응답에 없다 — 방금 보낸 값이라 돌려줄 이유가 없다."""
    user, meal = _meal(db)

    res = _checkin(client, user.id, meal.id, hungerReturnMinutes=60, comment="배고팠어요")

    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert set(data) == {
        "checkinId", "mealId", "checkinOffsetHours", "satietyPct", "hungerReturnMinutes",
    }
    assert data["checkinOffsetHours"] == 3
    assert data["satietyPct"] == 40
    assert data["hungerReturnMinutes"] == 60


def test_same_offset_overwrites_instead_of_stacking(
    client: TestClient, db: Session
) -> None:
    """같은 시점을 다시 보내면 덮는다 — 그래프에 점 두 개가 생기면 안 된다.

    `id` 도 유지된다. `ON CONFLICT DO UPDATE` 가 기존 행을 고치기 때문이다.
    """
    user, meal = _meal(db)

    first = _checkin(client, user.id, meal.id, satietyPct=40).json()["data"]
    second = _checkin(client, user.id, meal.id, satietyPct=25).json()["data"]

    assert second["checkinId"] == first["checkinId"]
    assert second["satietyPct"] == 25
    rows = db.execute(
        __import__("sqlalchemy").text(
            "SELECT count(*) FROM satiety_checkins WHERE meal_id = :m"
        ),
        {"m": meal.id},
    ).scalar_one()
    assert rows == 1


def test_different_offsets_are_separate_rows(client: TestClient, db: Session) -> None:
    """시점이 다르면 각각 남는다 — 포만감이 시간에 따라 떨어지는 걸 보는 게 목적이다."""
    user, meal = _meal(db)

    a = _checkin(client, user.id, meal.id, checkinOffsetHours=1, satietyPct=80)
    b = _checkin(client, user.id, meal.id, checkinOffsetHours=4, satietyPct=30)

    assert a.json()["data"]["checkinId"] != b.json()["data"]["checkinId"]


def test_checkin_does_not_touch_confirm_columns(client: TestClient, db: Session) -> None:
    """`satiety_before` · `satiety_after` 는 식사 등록·확정이 쓰는 칸이다.

    체크인이 덮으면 "먹기 전" 과 "먹은 직후" 기록이 사라진다.
    """
    from app.crud import satiety as satiety_crud

    user, meal = _meal(db)
    satiety_crud.set_satiety_after(db, meal_id=meal.id, pct=68)
    db.flush()

    _checkin(client, user.id, meal.id, hungerReturnMinutes=90)

    db.expire_all()
    log = satiety_crud.get_by_meal(db, meal.id)
    assert log.satiety_after == 68
    assert log.hunger_return_minutes == 90


def test_omitted_hunger_return_does_not_erase_it(client: TestClient, db: Session) -> None:
    """안 보낸 것과 지워 달라는 건 다르다."""
    user, meal = _meal(db)
    _checkin(client, user.id, meal.id, checkinOffsetHours=1, hungerReturnMinutes=60)

    data = _checkin(client, user.id, meal.id, checkinOffsetHours=4).json()["data"]

    assert data["hungerReturnMinutes"] == 60


def test_checkin_on_other_users_meal_is_404(client: TestClient, db: Session) -> None:
    _, meal = _meal(db)
    other = make_user(db, nickname="남")

    assert _checkin(client, other.id, meal.id).status_code == 404


def test_out_of_range_checkin_is_422(client: TestClient, db: Session) -> None:
    user, meal = _meal(db)

    assert _checkin(client, user.id, meal.id, satietyPct=101).status_code == 422
    assert _checkin(client, user.id, meal.id, checkinOffsetHours=100).status_code == 422


def test_typo_field_is_rejected(client: TestClient, db: Session) -> None:
    """extra="forbid" — 오타난 필드를 조용히 무시하면 그 값이 통째로 사라진다."""
    user, meal = _meal(db)

    res = client.post(
        f"/api/v1/meals/{meal.id}/satiety-checkins",
        headers=_h(user.id),
        json={"checkinOffsetHours": 3, "satietyPct": 40, "hungerReturnMinute": 60},
    )

    assert res.status_code == 422
