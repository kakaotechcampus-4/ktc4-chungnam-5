"""GET /meals/{mealId}/feedback · POST /meals/{mealId}/satiety-checkins 의 HTTP 계약.

FE 가 실제로 보는 모양 — 응답 래퍼 · camelCase · 명세 필드 · 404 단일화 · 마스킹.

**피드백 행은 직접 넣는다.** 채우는 워커(`worker/jobs/feedback_meal.py`)가 아직
스텁이라 API 로는 만들 수 없다. 워커가 붙으면 이 픽스처가 그 계약이 된다.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud import meal as meal_crud
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


def test_editing_after_evaluation_hides_the_stale_feedback(
    client: TestClient, db: Session
) -> None:
    """음식을 고치면 옛 문장을 내보내지 않는다.

    문장은 고치기 전 끼니를 보고 쓴 것이라 이미 사실이 아니다 — "단백질이 부족했어요"
    가 단백질을 더한 뒤에도 그대로 나간다. 확정 응답이 이때 `PENDING` 이므로 여기서
    `READY` 를 내면 같은 끼니에 두 답이 된다.

    실제 수정 경로가 부르는 `mark_recalculating` 을 그대로 쓴다 — 상태 상수를 손으로
    넣으면 그 함수가 바뀌었을 때 이 테스트가 따라오지 못한다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal)

    meal_crud.mark_recalculating(db, meal)
    db.flush()

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "PENDING"
    assert data["summary"] is None
    assert data["reasoning"] is None
    assert data["suggestions"] == []


@pytest.mark.parametrize(
    "status", [MealStatus.ANALYZING, MealStatus.REVIEW_REQUIRED, MealStatus.FAILED]
)
def test_only_evaluated_meals_serve_feedback(
    client: TestClient, db: Session, status: MealStatus
) -> None:
    """확정 상태가 아닌 나머지 셋도 모두 막는다.

    `ANALYZING` 만 막으면 남은 둘로 샌다. `EVALUATED` 를 통과 조건으로 두면 상태가
    늘어도 기본값이 "안 내보낸다" 쪽이다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal)
    meal.status = status
    db.flush()

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "PENDING"
    assert data["summary"] is None


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
