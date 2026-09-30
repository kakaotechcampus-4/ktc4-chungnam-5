"""`feedback.meal` 워커 — 확정된 끼니 하나의 Q/Q/S 를 문장으로 옮겨 meal_feedbacks 1행에 담는다.

틀리기 쉬운 것:
  - 두 번 돌았을 때 행이 쌓이거나 UNIQUE 에 걸려 커밋이 깨지는 것 — upsert 여야 한다
  - 채점 못 한 축(NULL)을 0 으로 채워 "못 쟀다" 를 "바닥이다" 로 바꾸는 것
  - AI 가 준 safetyStatus 를 SAFE 로 올리는 것 (규칙 1)
  - 낡은 작업(삭제·재계산 중인 식사)에 AI 를 부르거나 옛 문장을 쓰는 것
  - 워커 안에서 커밋하는 것 — 큐가 DONE 과 함께 커밋해야 실패 시 통째로 롤백된다
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from app.crud import satiety as satiety_crud
from app.infra.queue import ClaimedTask
from app.models.enums import MealStatus, MedicationStage, SafetyStatus
from app.models.evaluation import QQSEvaluation
from app.models.feedback import MealFeedback
from app.models.meal import Meal, SatietyLog
from app.tests.factories import (
    make_food_ref,
    make_meal,
    make_meal_feedback,
    make_meal_item,
    make_qqs_evaluation,
    make_user,
)
from app.worker.dispatch import handle
from app.worker.jobs.feedback_meal import run

_UNSET: Any = object()
"""`FakeAi(suggestions=...)` 를 안 넘겼다는 표시. `None` 은 "AI 가 null 을 줬다" 라 따로 둔다."""

_DEFAULT_SUGGESTIONS: list[dict[str, Any]] = [
    {"foodName": "두부 반 모", "advice": "단백질을 조금 더 채워요", "candidateFoodRefId": None},
]


# ─────────────────────────── Fake ───────────────────────────


class FakeAi:
    """`/short-feedback` 호출을 기록하고 정해진 응답을 돌려주는 가짜 AI.

    `ai-stub/schemas.py` ShortFeedbackResponse 모양이다. `on_call` 은 AI 를 기다리는
    사이에 사용자가 한 일(식사 삭제 등)을 흉내 낸다.
    """

    def __init__(
        self,
        *,
        body: str = "유지기 기준으로 보면 포만감이 부족한 식사예요.",
        safety_status: str = "SAFE",
        reasoning: str | None = "단백질 비중이 낮았어요.",
        suggestions: list[Any] | None = _UNSET,
        model_version: str = "stub-short-0",
        error: Exception | None = None,
        on_call=None,
    ) -> None:
        self.body = body
        self.safety_status = safety_status
        self.reasoning = reasoning
        self.suggestions = _DEFAULT_SUGGESTIONS if suggestions is _UNSET else suggestions
        self.model_version = model_version
        self.error = error
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("feedback.meal 이 analyze_meal 을 불렀다")

    def short_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return {
            "body": self.body,
            "modelVersion": self.model_version,
            "safetyStatus": self.safety_status,
            "reasoning": self.reasoning,
            "suggestions": self.suggestions,
        }

    def long_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("feedback.meal 이 long_feedback 을 불렀다")


# ─────────────────────────── helper ───────────────────────────


def _task(meal_id: uuid.UUID) -> ClaimedTask:
    return ClaimedTask(
        id=uuid.uuid4(),
        type="feedback.meal",
        payload={"mealId": str(meal_id)},
        attempts=0,
    )


def _evaluated_meal(
    db,
    user,
    *,
    scores: tuple[Any, Any, Any] = (None, None, 68),
    stage_at_evaluation: MedicationStage = MedicationStage.MAINTENANCE,
    meal_stage: MedicationStage = MedicationStage.MAINTENANCE,
    status: MealStatus = MealStatus.EVALUATED,
) -> Meal:
    """확정까지 끝난 식사 — 확인 API 가 남기는 모양(상태 EVALUATED + qqs_evaluations 1행)."""
    meal = make_meal(db, user_id=user.id, stage=meal_stage, status=status)
    quantity, quality, satiety = scores
    make_qqs_evaluation(
        db,
        meal_id=meal.id,
        quantity_score=quantity,
        quality_score=quality,
        satiety_score=satiety,
        stage_at_evaluation=stage_at_evaluation,
    )
    return meal


def _rows(db, meal_id: uuid.UUID) -> list[MealFeedback]:
    # 워커가 upsert(Core INSERT … ON CONFLICT)로 쓰므로 identity map 을 믿지 않는다.
    db.expire_all()
    return list(
        db.execute(select(MealFeedback).where(MealFeedback.meal_id == meal_id)).scalars()
    )


def _only_row(db, meal_id: uuid.UUID) -> MealFeedback:
    rows = _rows(db, meal_id)
    assert len(rows) == 1
    return rows[0]


def _soft_delete(db, meal_id: uuid.UUID) -> None:
    db.execute(update(Meal).where(Meal.id == meal_id).values(deleted_at=datetime.now(UTC)))


# ─────────────────────────── 정상 ───────────────────────────


def test_creates_meal_feedback_from_ai_result(db):
    """AI 응답의 body · reasoning · modelVersion · safetyStatus · suggestions 를 한 행에 담는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    ai = FakeAi(body="본문", reasoning="근거", model_version="stub-short-9")

    run(db, _task(meal.id), ai)

    row = _only_row(db, meal.id)
    assert row.user_id == user.id
    assert row.body == "본문"
    assert row.reasoning == "근거"
    assert row.model_version == "stub-short-9"
    assert row.safety_status == SafetyStatus.SAFE
    assert row.suggestions == _DEFAULT_SUGGESTIONS


def test_returns_ids_and_counts_only(db):
    """반환값은 task_queue.result 에 남는다 — 식별자와 개수만, 본문·음식명은 싣지 않는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)

    result = run(db, _task(meal.id), FakeAi())

    row = _only_row(db, meal.id)
    assert result == {"mealFeedbackId": str(row.id), "suggestionCount": 1}


def test_rerun_overwrites_the_same_row(db):
    """같은 작업이 두 번 돌아도 행은 하나다 — UNIQUE(meal_id) 라 INSERT 두 번이면 커밋이 깨진다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)

    run(db, _task(meal.id), FakeAi(body="A"))
    first_id = _only_row(db, meal.id).id
    run(db, _task(meal.id), FakeAi(body="B"))

    row = _only_row(db, meal.id)
    assert row.id == first_id
    assert row.body == "B"


def test_fills_a_row_invalidated_by_reconfirm(db):
    """재확정이 내용을 비운 행(body NULL · REVIEW_REQUIRED)을 새 문장으로 다시 채운다.

    행을 지우지 않고 비우는 이유는 daily_feedback_sources 의 CASCADE 다 — 워커도 같은 행을
    덮어써야 하루 피드백의 출처 링크가 살아남는다.
    """
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    stale = make_meal_feedback(
        db, user_id=user.id, meal_id=meal.id, body=None,
        safety_status=SafetyStatus.REVIEW_REQUIRED,
    )

    run(db, _task(meal.id), FakeAi(body="새 문장"))

    row = _only_row(db, meal.id)
    assert row.id == stale.id
    assert row.body == "새 문장"
    assert row.safety_status == SafetyStatus.SAFE


# ─────────────────────────── AI 요청 ───────────────────────────


def test_ai_request_is_meal_scope(db):
    """scope=MEAL · userId · mealId 를 싣는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    assert len(ai.calls) == 1
    payload = ai.calls[0]
    assert payload["scope"] == "MEAL"
    assert payload["userId"] == str(user.id)
    assert payload["mealId"] == str(meal.id)


def test_stage_is_the_one_used_for_scoring(db):
    """stage 는 채점 시점의 단계(`stage_at_evaluation`)다 — 점수와 같은 기준으로 문장을 쓴다.

    식사 스냅샷 단계·factories 기본값(MAINTENANCE)과 다르게 둬서, 다른 출처를 읽는 구현을 잡는다.
    """
    user = make_user(db)
    meal = _evaluated_meal(
        db, user,
        stage_at_evaluation=MedicationStage.TITRATION,
        meal_stage=MedicationStage.MAINTENANCE,
    )
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    assert ai.calls[0]["stage"] == "TITRATION"


def test_unscored_axes_are_sent_as_null(db):
    """채점 못 한 축은 null 로 보낸다 — 0 으로 채우면 "못 쟀다" 가 "바닥이다" 가 된다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(None, None, 68))
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    assert ai.calls[0]["qqs"] == {"quantity": None, "quality": None, "satiety": 68.0}


def test_scored_axes_are_sent_as_numbers(db):
    user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(Decimal("55.50"), 70, 80))
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    assert ai.calls[0]["qqs"] == {"quantity": 55.5, "quality": 70.0, "satiety": 80.0}


def test_items_carry_amount_and_public_db_nutrition(db):
    """공공 DB 에 매칭된 항목은 먹은 양만큼 환산한 성분을 싣는다 — AI 는 음식을 지목만 했다.

    기준량 100g 에 단백질 3g · 250g 먹음 → 7.5g.
    """
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    make_food_ref(db, food_ref_id="KFD_A", name="미역국")
    make_meal_item(
        db, meal_id=meal.id, display_name="미역국", food_ref_id="KFD_A",
        estimated_amount=Decimal("250.00"), estimated_unit="g",
    )
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    (item,) = ai.calls[0]["items"]
    assert item["displayName"] == "미역국"
    assert item["amount"] == 250.0
    assert item["unit"] == "g"
    assert item["nutrition"] == {
        "kcal": 125.0,
        "proteinG": 7.5,
        "fatG": 3.75,
        "carbG": 10.0,
        "fiberG": 1.25,
        "sodiumMg": 1500.0,
    }


def test_confirmed_amount_wins_over_estimate(db):
    """사용자가 확인한 양이 AI 추정보다 우선한다 — 사용자가 방금 부정한 값을 AI 에 보내지 않는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    make_food_ref(db, food_ref_id="KFD_A")
    make_meal_item(
        db, meal_id=meal.id, food_ref_id="KFD_A",
        estimated_amount=Decimal("250.00"), estimated_unit="g",
        confirmed_amount=Decimal("100.00"), confirmed_unit="g",
    )
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    (item,) = ai.calls[0]["items"]
    assert item["amount"] == 100.0
    assert item["nutrition"]["proteinG"] == 3.0


def test_manual_nutrition_wins_over_public_db(db):
    """직접 입력한 성분이 있으면 그 값이다 — 화면(`item_nutrition`)과 같은 규칙이어야 한다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    make_food_ref(db, food_ref_id="KFD_A")
    item_row = make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_A")
    item_row.manual_protein_g = Decimal("20.000")
    db.flush()
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    (item,) = ai.calls[0]["items"]
    assert item["nutrition"]["proteinG"] == 20.0
    assert item["nutrition"]["kcal"] is None


def test_unmatched_item_is_sent_without_nutrition(db):
    """공공 DB 매칭이 없으면 음식은 보내고 성분만 null — 먹은 음식을 빼면 문장이 틀린다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    make_meal_item(db, meal_id=meal.id, display_name="엄마표 반찬", food_ref_id=None)
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    (item,) = ai.calls[0]["items"]
    assert item["displayName"] == "엄마표 반찬"
    assert item["nutrition"] is None


def test_item_without_amount_is_sent_with_null_amount(db):
    """양을 모르는 항목도 빼지 않는다 — amount · unit 을 null 로 보낸다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    make_meal_item(db, meal_id=meal.id, estimated_amount=None, estimated_unit=None)
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    (item,) = ai.calls[0]["items"]
    assert item["amount"] is None
    assert item["unit"] is None


def test_satiety_context_from_logs_and_checkins(db):
    """포만감 컨텍스트: 식전·식후 · 체크인(시점 순) · 다시 배고파진 시각 · 한마디."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    satiety_crud.set_satiety_after(db, meal_id=meal.id, pct=68)
    db.execute(update(SatietyLog).where(SatietyLog.meal_id == meal.id).values(satiety_before=20))
    satiety_crud.upsert_checkin(db, meal_id=meal.id, offset_hours=3, pct=40)
    satiety_crud.upsert_checkin(db, meal_id=meal.id, offset_hours=1, pct=60)
    satiety_crud.set_hunger_return(db, meal_id=meal.id, minutes=150, comment="금방 배고팠어요")
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    assert ai.calls[0]["satiety"] == {
        "beforePct": 20,
        "afterPct": 68,
        "checkins": [
            {"checkinOffsetHours": 1, "satietyPct": 60},
            {"checkinOffsetHours": 3, "satietyPct": 40},
        ],
        "hungerReturnMinutes": 150,
        "userComment": "금방 배고팠어요",
    }


def test_checkins_alone_make_a_satiety_context(db):
    """satiety_logs 행이 없어도 체크인이 있으면 컨텍스트를 만든다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    satiety_crud.upsert_checkin(db, meal_id=meal.id, offset_hours=2, pct=50)
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    assert ai.calls[0]["satiety"] == {
        "beforePct": None,
        "afterPct": None,
        "checkins": [{"checkinOffsetHours": 2, "satietyPct": 50}],
        "hungerReturnMinutes": None,
        "userComment": None,
    }


def test_satiety_is_null_without_any_record(db):
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    assert ai.calls[0]["satiety"] is None


def test_ai_payload_is_json_serializable(db):
    """AI 요청은 JSON 으로 나간다 — Decimal · UUID 객체를 싣지 않는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(Decimal("10.00"), Decimal("20.00"), 30))
    make_food_ref(db, food_ref_id="KFD_A")
    make_meal_item(db, meal_id=meal.id, food_ref_id="KFD_A")
    satiety_crud.upsert_checkin(db, meal_id=meal.id, offset_hours=2, pct=50)
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    json.dumps(ai.calls[0])


# ─────────────────────────── 제안 · 안전 ───────────────────────────


def test_suggestion_with_unknown_food_ref_keeps_text_but_drops_the_id(db):
    """AI 가 지목한 음식이 food_refs 에 없으면 id 만 비운다 — 제안 문구는 성분 없이도 쓸모가 있다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    make_food_ref(db, food_ref_id="KFD_REAL")
    ai = FakeAi(
        suggestions=[
            {"foodName": "두부", "advice": "단백질", "candidateFoodRefId": "KFD_REAL"},
            {"foodName": "나물", "advice": "식이섬유", "candidateFoodRefId": "KFD_GHOST"},
        ]
    )

    run(db, _task(meal.id), ai)

    assert _only_row(db, meal.id).suggestions == [
        {"foodName": "두부", "advice": "단백질", "candidateFoodRefId": "KFD_REAL"},
        {"foodName": "나물", "advice": "식이섬유", "candidateFoodRefId": None},
    ]


def test_malformed_suggestions_do_not_fail_the_task(db):
    """AI 가 모양이 어긋난 제안을 줘도 작업이 죽지 않는다 — 죽으면 재시도만 반복하다 DLQ 로 간다.

    dict 가 아닌 원소는 버리고, 문자열이 아닌 id(list · int)는 비운다. list 는 해시가
    안 돼 `in` 비교에서 TypeError 가 난다.
    """
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    ai = FakeAi(
        suggestions=[
            "문자열 원소",
            {"foodName": "두부", "advice": "단백질", "candidateFoodRefId": ["KFD_A"]},
            {"foodName": "나물", "advice": "식이섬유", "candidateFoodRefId": 123},
        ]
    )

    run(db, _task(meal.id), ai)

    assert _only_row(db, meal.id).suggestions == [
        {"foodName": "두부", "advice": "단백질", "candidateFoodRefId": None},
        {"foodName": "나물", "advice": "식이섬유", "candidateFoodRefId": None},
    ]


def test_null_suggestions_are_stored_as_null(db):
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    result = run(db, _task(meal.id), FakeAi(suggestions=None))

    assert _only_row(db, meal.id).suggestions is None
    assert result["suggestionCount"] == 0


@pytest.mark.parametrize("status", ["BLOCKED", "REVIEW_REQUIRED"])
def test_unsafe_result_is_stored_as_is(db, status):
    """safetyStatus 는 AI 가 준 그대로다. SAFE 로 올리지 않는다 (규칙 1)."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)

    run(db, _task(meal.id), FakeAi(safety_status=status))

    assert _only_row(db, meal.id).safety_status == SafetyStatus(status)


# ─────────────────────────── 낡은 작업 ───────────────────────────


def test_missing_meal_is_skipped_without_ai(db):
    ai = FakeAi()

    result = run(db, _task(uuid.uuid4()), ai)

    assert ai.calls == []
    assert result == {"skipped": "MEAL_NOT_FOUND"}


def test_deleted_meal_is_skipped_without_ai(db):
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    _soft_delete(db, meal.id)
    ai = FakeAi()

    result = run(db, _task(meal.id), ai)

    assert ai.calls == []
    assert _rows(db, meal.id) == []
    assert result == {"skipped": "MEAL_NOT_FOUND"}


def test_meal_being_recalculated_is_skipped_without_ai(db):
    """음식을 고쳐 재계산 대기로 돌아간 식사 — 다음 확정이 새 작업을 넣는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user, status=MealStatus.ANALYZING)
    ai = FakeAi()

    result = run(db, _task(meal.id), ai)

    assert ai.calls == []
    assert _rows(db, meal.id) == []
    assert result == {"skipped": "MEAL_NOT_EVALUATED"}


def test_meal_deleted_during_ai_call_is_not_written(db):
    """AI 를 기다리는 사이 식사가 지워지면 쓰지 않는다 — 쓰기 직전에 잠그고 다시 본다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    ai = FakeAi(on_call=lambda: _soft_delete(db, meal.id))

    result = run(db, _task(meal.id), ai)

    assert len(ai.calls) == 1
    assert _rows(db, meal.id) == []
    assert result == {"skipped": "MEAL_NOT_FOUND"}


def test_meal_edited_during_ai_call_is_not_written(db):
    """AI 를 기다리는 사이 음식을 고쳐 재계산 대기로 가면 옛 끼니로 쓴 문장을 쓰지 않는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)

    def edit() -> None:
        db.execute(
            update(Meal).where(Meal.id == meal.id).values(status=MealStatus.ANALYZING)
        )

    result = run(db, _task(meal.id), FakeAi(on_call=edit))

    assert _rows(db, meal.id) == []
    assert result == {"skipped": "MEAL_NOT_EVALUATED"}


def test_scores_changed_during_ai_call_are_not_written(db):
    """AI 를 기다리는 사이 포만감만 고쳐 재확정하면(상태는 EVALUATED 그대로) 옛 점수로 쓴 문장을 쓰지 않는다.

    재확정이 새 작업을 넣었다. 워커가 여럿이면 그 작업이 먼저 끝날 수 있어서, 여기서
    쓰면 낡은 문장이 새 문장을 덮고 영영 남는다.
    """
    user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(None, None, 68))

    def reconfirm() -> None:
        db.execute(
            update(QQSEvaluation)
            .where(QQSEvaluation.meal_id == meal.id)
            .values(satiety_score=30)
        )

    result = run(db, _task(meal.id), FakeAi(on_call=reconfirm))

    assert _rows(db, meal.id) == []
    assert result == {"skipped": "EVALUATION_CHANGED"}


def test_evaluation_replaced_during_ai_call_is_not_written(db):
    """음식을 고치고 재확정까지 AI 호출 중에 끝나면 평가 행이 새로 생긴다 — 점수가 같아도 쓰지 않는다.

    음식을 고치면 `mark_recalculating` 이 평가 행을 지우고 재확정이 새로 넣는다. 점수는
    같게 나올 수 있어서(Quantity 는 구간 안이면 100, 체중이 없으면 Quality 는 None)
    id 로만 잡힌다.
    """
    user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(None, None, 68))

    def edit_and_reconfirm() -> None:
        db.execute(delete(QQSEvaluation).where(QQSEvaluation.meal_id == meal.id))
        make_qqs_evaluation(
            db, meal_id=meal.id, quantity_score=None, quality_score=None, satiety_score=68
        )

    result = run(db, _task(meal.id), FakeAi(on_call=edit_and_reconfirm))

    assert _rows(db, meal.id) == []
    assert result == {"skipped": "EVALUATION_CHANGED"}


def test_missing_evaluation_raises_for_retry(db):
    """EVALUATED 인데 점수 행이 없으면 채점이 아직이다 — raise 해서 재시도에 맡긴다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    ai = FakeAi()

    with pytest.raises(LookupError):
        run(db, _task(meal.id), ai)

    assert ai.calls == []


# ─────────────────────────── 트랜잭션 · 로그 ───────────────────────────


def test_worker_does_not_commit(db, monkeypatch):
    """커밋은 큐가 DONE 과 함께 한다. 여기서 커밋하면 실패 시 도메인 변경이 롤백되지 않는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    commits: list[None] = []
    monkeypatch.setattr(db, "commit", lambda: commits.append(None))

    run(db, _task(meal.id), FakeAi())

    assert len(commits) == 0


def test_ai_failure_propagates_and_leaves_no_row(db):
    """AI 가 터지면 그대로 올린다 — 큐가 롤백하고 재시도한다. 빈 행을 남기지 않는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)

    with pytest.raises(RuntimeError):
        run(db, _task(meal.id), FakeAi(error=RuntimeError("AI down")))

    assert _rows(db, meal.id) == []


def test_logs_do_not_contain_food_names_or_feedback_text(db, caplog):
    """음식명 · 본문 · 제안 문구 · 사용자 한마디를 로그에 남기지 않는다 (규칙 6)."""
    caplog.set_level(logging.DEBUG)
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    make_meal_item(db, meal_id=meal.id, display_name="비밀김밥")
    satiety_crud.set_hunger_return(db, meal_id=meal.id, minutes=None, comment="비밀한마디")
    ai = FakeAi(
        body="비밀본문",
        reasoning="비밀근거",
        suggestions=[{"foodName": "비밀두부", "advice": "비밀조언", "candidateFoodRefId": None}],
    )

    run(db, _task(meal.id), ai)

    for secret in ("비밀김밥", "비밀한마디", "비밀본문", "비밀근거", "비밀두부", "비밀조언"):
        assert secret not in caplog.text


# ─────────────────────────── dispatch ───────────────────────────


def test_handle_routes_feedback_meal_to_the_handler(db):
    user = make_user(db)
    meal = _evaluated_meal(db, user)

    result = handle(db, _task(meal.id), FakeAi())

    assert result["mealFeedbackId"] == str(_only_row(db, meal.id).id)


def test_items_of_other_meals_are_not_mixed(db):
    """다른 식사의 항목이 섞이지 않는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    other = make_meal(db, user_id=user.id)
    make_meal_item(db, meal_id=meal.id, display_name="내 음식")
    make_meal_item(db, meal_id=other.id, display_name="남의 음식")
    ai = FakeAi()

    run(db, _task(meal.id), ai)

    assert [item["displayName"] for item in ai.calls[0]["items"]] == ["내 음식"]

