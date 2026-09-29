"""GET /meals/{mealId}/feedback · POST /meals/{mealId}/satiety-checkins 의 HTTP 계약.

FE 가 실제로 보는 모양 — 응답 래퍼 · camelCase · 명세 필드 · 404 단일화 · 마스킹.

**피드백 행은 직접 넣는다.** 채우는 워커(`worker/jobs/feedback_meal.py`)가 아직
스텁이라 API 로는 만들 수 없다. 워커가 붙으면 이 픽스처가 그 계약이 된다.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud import evaluation as evaluation_crud
from app.crud import feedback as feedback_crud
from app.crud import meal as meal_crud
from app.models.enums import MealStatus, MedicationStage, SafetyStatus
from app.models.feedback import MealFeedback
from app.tests.factories import make_food_ref, make_meal, make_meal_item, make_user

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


def test_reconfirming_does_not_bring_the_stale_feedback_back(
    client: TestClient, db: Session
) -> None:
    """**재확정까지 가 본다** — 여기가 사용자가 실제로 보는 지점이다.

    상태 가드만으로는 `ANALYZING` 구간밖에 못 막는다. 재확정하면 상태가
    `EVALUATED` 로 돌아가 가드가 풀리고, 아무도 안 건드린 옛 문장이 다시 `READY` 로
    나간다 — 닭가슴살을 더했는데 "단백질 비중이 낮았어요" 가 그대로 나가는 상황이다.

    점수는 `evaluation_crud.upsert` 가 덮으므로 문제가 없다. 문장만 남는다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal)
    make_food_ref(db)
    make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_TEST_01")

    meal_crud.mark_recalculating(db, meal)
    db.flush()
    # 재확정 — 여기서 상태가 EVALUATED 로 돌아온다.
    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm",
        headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["status"] == "EVALUATED"

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "PENDING"
    assert data["summary"] is None
    assert data["reasoning"] is None
    assert data["suggestions"] == []


@pytest.mark.parametrize("body", ["", "   ", "\n"], ids=["빈 문자열", "공백", "줄바꿈"])
def test_blank_body_is_pending_not_an_empty_ready(
    client: TestClient, db: Session, body: str
) -> None:
    """본문이 비면 `PENDING` 이다 — NULL 이든 빈 문자열이든 내용이 없는 건 같다.

    AI 계약(`ai-stub/schemas.py::ShortFeedbackResponse.body`)에 `min_length` 가 없어
    가드레일이 본문을 지우거나 부분 응답이 오면 실제로 빈 문자열이 온다. NULL 만
    보면 `READY` + `summary: ""` 가 나가 FE 가 빈 카드를 그리고 **폴링까지 멈춘다.**
    """
    user, meal = _meal(db)
    row = _add_feedback(db, meal)
    row.body = body
    db.flush()

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "PENDING"
    assert data["summary"] is None


def test_confirming_twice_without_editing_keeps_the_feedback(
    client: TestClient, db: Session
) -> None:
    """**아무것도 안 고친 재확정은 문장을 지우지 않는다.**

    `_CONFIRMABLE` 에 `EVALUATED` 가 있어 확정 버튼 더블탭 · FE 타임아웃 재시도 ·
    같은 값 재전송이 전부 여기로 온다. 무조건 비우면 멀쩡한 문장이 날아가고,
    되살릴 길이 없다 — `feedback.meal` 을 큐에 넣는 코드가 아직 없고 워커도 스텁이라
    `PENDING` 에 고착된다.
    """
    user, meal = _meal(db)
    make_food_ref(db)
    make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_TEST_01")
    body = {"satietyAfterPct": 68}
    client.post(f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id), json=body)
    _add_feedback(db, meal)  # 워커가 문장을 채운 상태를 흉내 낸다

    # 같은 값으로 한 번 더 — 바뀐 게 없다.
    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id), json=body
    )
    assert res.status_code == 200, res.text

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "READY"
    assert data["summary"] == "유지기 기준 포만감이 부족한 식사예요."


def test_reconfirming_with_a_different_satiety_invalidates(
    client: TestClient, db: Session
) -> None:
    """포만감만 고쳐 재확정해도 문장은 낡는다 — 상태만 봐서는 못 잡는다.

    음식을 안 고쳤으니 상태는 `EVALUATED` 그대로 들어온다. Satiety 는 지금 유일하게
    값이 있는 점수이고 요청값을 그대로 쓰므로, 점수 비교가 이 경로의 유일한 단서다.
    """
    user, meal = _meal(db)
    make_food_ref(db)
    make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_TEST_01")
    client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 68},
    )
    _add_feedback(db, meal)

    res = client.post(
        f"/api/v1/meals/{meal.id}/confirm", headers=_h(user.id),
        json={"satietyAfterPct": 20},
    )
    assert res.status_code == 200, res.text

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "PENDING"
    assert data["summary"] is None


def test_invalidated_row_is_pending_not_an_empty_ready(
    client: TestClient, db: Session
) -> None:
    """내용만 비운 행은 `PENDING` 이다 — 행 존재만 보면 빈 `READY` 가 나간다.

    무효화는 행을 지우지 않는다(`daily_feedback_sources` 가 CASCADE 라 일일 피드백의
    출처 링크가 사라진다). 그래서 "행이 있다" 와 "읽을 내용이 있다" 가 갈린다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal)

    feedback_crud.invalidate_by_meal(db, meal.id)
    db.flush()

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["feedbackStatus"] == "PENDING"
    assert data["summary"] is None

    # **DB 를 직접 본다.** 응답만 보면 `body` 만 비워도 통과한다 — `get_feedback` 이
    # `body` 가 비었으면 곧장 `pending()` 으로 빠지고, 그 팩토리가 나머지를
    # 하드코딩하기 때문이다. 그러면 낡은 근거 문장과 제안이 행에 남아, 같은 행을
    # 읽기로 되어 있는 `GET /meals/{mealId}` 가 그걸 그대로 싣는다.
    row = feedback_crud.get_by_meal(db, meal.id)
    # 행은 남아 있어야 한다 — 일일 피드백이 이 행을 출처로 물고 있을 수 있다.
    assert row is not None
    assert (row.body, row.reasoning, row.suggestions) == (None, None, None)
    # 판정도 되돌아간다 — 내용에 붙은 판정이라 내용이 무효면 같이 무효다.
    assert row.safety_status is SafetyStatus.REVIEW_REQUIRED
    # 어느 모델이 썼었나는 지운 뒤에도 사실이다.
    assert row.model_version == "stub-1"


@pytest.mark.parametrize(
    ("stored", "label"),
    [
        ({"items": []}, "배열이 아닌 객체"),
        ("not-a-list", "JSON 문자열"),
        (["두부 반 모"], "문자열 원소"),
        ([{"foodName": None, "advice": None, "candidateFoodRefId": None}], "명시적 null"),
        ([{"foodName": "두부", "advice": "a", "candidateFoodRefId": 123}], "정수 refId"),
        ([{"foodName": "두부", "advice": "a", "candidateFoodRefId": ["x"]}], "리스트 refId"),
    ],
)
def test_malformed_suggestions_do_not_break_the_response(
    client: TestClient, db: Session, stored: object, label: str
) -> None:
    """저장 모양이 어긋나도 200 이다 — 제안만 버리고 요약·근거는 살린다.

    컬럼이 JSONB 라 DB 는 아무 JSON 이나 받고, 쓰는 쪽은 AI 응답을 담을 워커다.
    계약이 바뀌거나 부분 응답이 오면 이 모양들이 실제로 들어온다. 검증이 없던
    동안에는 여섯 가지가 전부 500 이었고, **제안 한 건 때문에 그 끼니 피드백
    전체를 못 읽었다.**
    """
    user, meal = _meal(db)
    row = _add_feedback(db, meal)
    row.suggestions = stored
    db.flush()

    res = client.get(f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id))

    assert res.status_code == 200, f"{label}: {res.text}"
    data = res.json()["data"]
    # 제안은 버려도 본문은 남는다 — 이게 degrade 의 핵심이다.
    assert data["summary"] == "유지기 기준 포만감이 부족한 식사예요."
    assert isinstance(data["suggestions"], list)


def test_partial_suggestion_keeps_the_text_and_empties_nutrients(
    client: TestClient, db: Session
) -> None:
    """`candidateFoodRefId` 가 없어도 문구는 살린다 — 성분만 비운다.

    반대로 문구가 둘 다 비면 그 항목은 버린다. FE 에 그릴 게 없는 카드다.
    """
    user, meal = _meal(db)
    row = _add_feedback(db, meal)
    row.suggestions = [
        {"foodName": "나물 한 접시", "advice": "식이섬유로 포만감을 늘려요"},
        {"foodName": "", "advice": "", "candidateFoodRefId": "KFD_TEST_01"},
    ]
    db.flush()

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["suggestions"] == [
        {
            "foodName": "나물 한 접시",
            "nutrients": [],
            "advice": "식이섬유로 포만감을 늘려요",
        }
    ]


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
    assert data["expectedSatietyPct"] is None
    assert data["safetyStatus"] == "BLOCKED"
    # 생성은 끝났고 **영영 못 보여 준다** — `REVIEW_REQUIRED` 와 달리 `READY` 다.
    # FE 는 폴링을 멈추고 상담 안내로 바꾼다(`MEDICAL_QUESTION_DETECTED` 동반).
    assert data["feedbackStatus"] == "READY"


def test_blocked_carries_the_spec_error_code(client: TestClient, db: Session) -> None:
    """명세 에러표: `MEDICAL_QUESTION_DETECTED` | 200 | "상담 안내 후 복귀".

    `safetyStatus` 로도 분기는 되지만, 이 레포는 FE 가 `error.code` 로 분기하는 것을
    전제로 200 에 도메인 코드를 싣는다(`evaluations.py` 의 `NUTRITION_NOT_MATCHED`).
    여기만 빼면 같은 성격의 분기가 두 방식이 된다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal, safety=SafetyStatus.BLOCKED)

    body = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()

    assert body["success"] is True
    assert body["error"]["code"] == "MEDICAL_QUESTION_DETECTED"


def test_safe_feedback_carries_no_error_code(client: TestClient, db: Session) -> None:
    """정상 응답에는 코드가 없다 — 없어야 FE 가 분기를 신뢰할 수 있다."""
    user, meal = _meal(db)
    _add_feedback(db, meal)

    assert client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["error"] is None


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
    # **`READY` 가 아니라 `PENDING` 이다.** 검수를 통과하면 보일 수도 있으니 "아직"
    # 이고, `READY` + `summary: null` 은 FE 에 빈 카드를 그리게 한다.
    assert data["feedbackStatus"] == "PENDING"


def test_fractional_score_is_rounded_not_truncated(
    client: TestClient, db: Session
) -> None:
    """점수가 소수면 반올림한다 — 자르면 68.7 이 68 이 된다.

    컬럼이 `Numeric(5, 2)` 라 소수가 들어올 수 있다. 반올림하는 다른 화면과 같은
    끼니가 68 과 69 로 갈리면 안 된다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal)
    evaluation_crud.upsert(
        db, meal_id=meal.id, stage=MedicationStage.MAINTENANCE,
        quantity_score=None, quality_score=None, satiety_score=Decimal("68.7"),
    )
    db.flush()

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["expectedSatietyPct"] == {"current": 69, "after": 69}


@pytest.mark.parametrize(
    ("score", "expected"),
    [("68.5", 68), ("69.5", 70), ("68.4", 68), ("68.6", 69)],
)
def test_half_cases_use_bankers_rounding(
    client: TestClient, db: Session, score: str, expected: int
) -> None:
    """`.5` 는 **짝수 쪽**으로 간다 — 파이썬 `round` 가 half-even 이다.

    68.5 → 68, 69.5 → 70. 올림을 기대하면 틀린다. 지금은 `score_satiety` 가 정수만
    만들어 도달 불가지만, 이 코드는 소수가 들어올 미래를 위한 것이라 어느 방식인지
    못박아 둔다. 다른 화면이 half-up 으로 반올림하면 68.5 에서 68/69 로 갈린다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal)
    evaluation_crud.upsert(
        db, meal_id=meal.id, stage=MedicationStage.MAINTENANCE,
        quantity_score=None, quality_score=None, satiety_score=Decimal(score),
    )
    db.flush()

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["expectedSatietyPct"]["current"] == expected


@pytest.mark.parametrize(
    "suggestion",
    [
        {"foodName": "두부", "advice": None},
        {"foodName": "두부"},
        {"advice": "단백질을 채워요"},
        {"foodName": "", "advice": "단백질을 채워요"},
    ],
)
def test_half_written_suggestion_is_dropped(
    client: TestClient, db: Session, suggestion: dict
) -> None:
    """이름과 조언은 짝이다 — 한쪽만 있으면 FE 가 반쪽 카드를 그린다."""
    user, meal = _meal(db)
    _add_feedback(db, meal, suggestions=[suggestion])

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["suggestions"] == []
    assert data["summary"] == "유지기 기준 포만감이 부족한 식사예요."


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


def test_nutrients_are_scaled_to_100g(client: TestClient, db: Session) -> None:
    """기준량이 100g 이 아니면 환산해서 낸다.

    `food_refs` 의 성분은 `serving_size` 당 값이고 그 값이 행마다 다르다. 음료
    200ml 행을 그대로 내보내면 표시 숫자가 두 배로 틀린다 — 응답에 `serving_size` 가
    없어 FE 는 기준을 알 방법이 없다.

    기준량 200 · 단백질 3g → 100g 당 1.5g.
    """
    user, meal = _meal(db)
    make_food_ref(
        db,
        food_ref_id="KFD_DRINK",
        name="두유",
        serving_size=Decimal("200.000"),
        protein_g=Decimal("3.000"),
        fiber_g=None,
    )
    _add_feedback(
        db,
        meal,
        suggestions=[
            {"foodName": "두유 한 컵", "advice": "단백질을 채워요",
             "candidateFoodRefId": "KFD_DRINK"}
        ],
    )

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["suggestions"][0]["nutrients"] == [{"code": "PROTEIN", "amountG": 1.5}]


def test_nan_nutrient_is_omitted_not_null(client: TestClient, db: Session) -> None:
    """NaN 성분은 키째 뺀다 — NULL 과 같은 "모른다" 다.

    `'NaN'::numeric` 은 성분 컬럼에 그냥 저장되고 `is not None` 을 통과한다. 그대로
    두면 `amountG: null` 이 나가는데, 스키마상 **required non-nullable** 이라 응답이
    자기 OpenAPI 계약을 어긴다.
    """
    user, meal = _meal(db)
    make_food_ref(
        db, food_ref_id="KFD_NAN", name="정체불명",
        protein_g=Decimal("NaN"), fiber_g=Decimal("2.000"),
    )
    _add_feedback(
        db,
        meal,
        suggestions=[
            {"foodName": "정체불명", "advice": "식이섬유를 채워요",
             "candidateFoodRefId": "KFD_NAN"}
        ],
    )

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    # 멀쩡한 FIBER 만 남는다. PROTEIN 은 null 로도 0 으로도 나가지 않는다.
    assert data["suggestions"][0]["nutrients"] == [{"code": "FIBER", "amountG": 2.0}]


def test_tiny_serving_size_does_not_overflow(client: TestClient, db: Session) -> None:
    """기준량이 아주 작아도 500 이 나면 안 된다.

    성분과 `serving_size` 가 둘 다 `Numeric(10, 3)` 이라 환산 결과가 약 1e12 까지
    간다. 캐스팅 폭이 좁으면 Postgres 가 numeric field overflow 로 죽고, 그 한 행
    때문에 끼니 피드백 전체를 못 읽는다.
    """
    user, meal = _meal(db)
    make_food_ref(
        db, food_ref_id="KFD_TINY", name="농축액",
        serving_size=Decimal("0.001"), protein_g=Decimal("9999999.999"), fiber_g=None,
    )
    _add_feedback(
        db, meal,
        suggestions=[{"foodName": "농축액", "advice": "단백질을 채워요",
                      "candidateFoodRefId": "KFD_TINY"}],
    )

    res = client.get(f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id))

    assert res.status_code == 200, res.text
    assert res.json()["data"]["suggestions"][0]["nutrients"][0]["code"] == "PROTEIN"


@pytest.mark.parametrize(
    "serving_size", [None, Decimal("0"), Decimal("NaN")], ids=["NULL", "0", "NaN"]
)
def test_unusable_serving_size_empties_nutrients(
    client: TestClient, db: Session, serving_size: Decimal | None
) -> None:
    """기준량을 못 쓰면 성분을 비우고 제안 문구만 낸다.

    나눗셈의 분모다 — 0 이면 터지고 NaN 이면 결과가 전부 NaN 이 된다. `NaN` 을 따로
    거르는 건 Postgres 에서 `'NaN'::numeric > 0` 이 **TRUE** 라서, `> 0` 조건만으로는
    안 걸리기 때문이다.
    """
    user, meal = _meal(db)
    make_food_ref(db, food_ref_id="KFD_BAD", name="정체불명", serving_size=serving_size)
    _add_feedback(
        db,
        meal,
        suggestions=[
            {"foodName": "정체불명", "advice": "단백질을 채워요",
             "candidateFoodRefId": "KFD_BAD"}
        ],
    )

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["suggestions"] == [
        {"foodName": "정체불명", "nutrients": [], "advice": "단백질을 채워요"}
    ]


def test_expected_satiety_uses_the_stored_score(client: TestClient, db: Session) -> None:
    """`current` 는 `qqs_evaluations.satiety_score` 다.

    ⚠️ `after` 도 같은 값이다 — 팀 결정. 제안을 반영한 예측을 만들 소스가 없어서
    "변화 없음" 으로 내보낸다(`schemas/feedback.py::ExpectedSatiety`). 예측이 생기면
    **이 단언이 깨져서** 여기도 같이 고쳐야 한다는 걸 알려 준다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal)
    evaluation_crud.upsert(
        db,
        meal_id=meal.id,
        stage=MedicationStage.MAINTENANCE,
        quantity_score=None,
        quality_score=None,
        satiety_score=68,
    )
    db.flush()

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["expectedSatietyPct"] == {"current": 68, "after": 68}


def test_expected_satiety_is_null_without_a_score(
    client: TestClient, db: Session
) -> None:
    """점수 행이 없으면 `null` 이다 — 0 으로 채우면 "포만감 0%" 라는 거짓말이 된다."""
    user, meal = _meal(db)
    _add_feedback(db, meal)

    data = client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]

    assert data["expectedSatietyPct"] is None


def test_feedback_is_404_for_everyone_but_the_owner(
    client: TestClient, db: Session
) -> None:
    """**같은 mealId 로 주인은 200, 남은 404** — 짝으로 봐야 의미가 있다.

    404 만 단언하면 라우트를 통째로 지워도 통과한다(없는 경로라 Starlette 가 404 를
    낸다). 그러면 "소유권이 막혔다" 와 "엔드포인트가 없다" 를 구분하지 못한다.
    """
    user, meal = _meal(db)
    other = make_user(db, nickname="남")

    assert client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).status_code == 200

    res = client.get(f"/api/v1/meals/{meal.id}/feedback", headers=_h(other.id))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_feedback_of_a_deleted_meal_is_404(client: TestClient, db: Session) -> None:
    """삭제된 식사도 404 다 — 없는 식사·남의 식사와 같게 응답한다.

    `meals` 는 soft delete 라 행이 살아 있고, 자식 행(`meal_feedbacks`)도 남는다.
    거르는 건 `crud/meal.py::get_owned_meal` 의 `deleted_at IS NULL` 하나뿐이라,
    누가 그 조회를 직접 쿼리로 바꾸면 삭제한 식사의 피드백이 그대로 나간다.
    `crud/__init__.py` 가 "필터를 각자 기억해야 한다" 고 경고하는 바로 그 회귀다.
    """
    user, meal = _meal(db)
    _add_feedback(db, meal)
    meal.deleted_at = datetime.now(UTC)
    db.flush()

    res = client.get(f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_feedback_without_header_is_401(client: TestClient, db: Session) -> None:
    _, meal = _meal(db)

    assert client.get(f"/api/v1/meals/{meal.id}/feedback").status_code == 401
