"""`feedback.meal` 워커 — 확정된 끼니 하나의 Q/Q/S 를 문장으로 옮겨 meal_feedbacks 1행에 담는다.

틀리기 쉬운 것:
  - 두 번 돌았을 때 행이 쌓이거나 UNIQUE 에 걸려 커밋이 깨지는 것 — upsert 여야 한다
  - 채점 못 한 축(NULL)을 0 으로 채워 "못 쟀다" 를 "바닥이다" 로 바꾸는 것
  - AI 가 준 safetyStatus 를 SAFE 로 올리는 것 (규칙 1)
  - 낡은 작업(삭제·재계산 중인 식사)에 AI 를 부르거나 옛 문장을 쓰는 것
  - 워커 안에서 커밋하는 것 — 큐가 DONE 과 함께 커밋해야 실패 시 통째로 롤백된다
  - 하루 피드백 upsert 를 UTC 날짜로 하거나, 검수 안 된(SAFE 아닌) 문장으로 덮는 것
  - SAFE 가 아니라고 점수까지 건너뛰어 앞 끼니까지의 점수를 남기는 것
  - 하루 영양 합계를 끼니마다 더해 가서 재확정·재시도에 두 번 더하는 것
  - 앞 끼니를 뒤늦게 입력·재확정했을 때 그 문장이 더 늦은 끼니의 문장을 덮는 것
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from app.crud import satiety as satiety_crud
from app.infra.queue import ClaimedTask
from app.models.enums import MealStatus, MedicationStage, SafetyStatus
from app.models.evaluation import QQSEvaluation
from app.models.feedback import DailyFeedback, DailyFeedbackSource, MealFeedback
from app.models.meal import Meal, SatietyLog
from app.tests.factories import (
    EATEN_AT,
    make_daily_feedback,
    make_food_ref,
    make_meal,
    make_meal_feedback,
    make_meal_item,
    make_qqs_evaluation,
    make_user,
)
from app.worker.dispatch import get_job
from app.worker.jobs import feedback_meal

# 세 단계를 세션 하나로 이어 돈다. 시나리오 단언은 3단계 분리 전과 같다.
run = feedback_meal.JOB.run_inline

_UNSET: Any = object()
"""`FakeAi(suggestions=...)` 를 안 넘겼다는 표시. `None` 은 "AI 가 null 을 줬다" 라 따로 둔다."""

# 셋 다 KST 2026-08-22 — 08:00 · 12:30 · 21:30.
BREAKFAST_AT = datetime(2026, 8, 21, 23, 0, tzinfo=UTC)
LUNCH_AT = datetime(2026, 8, 22, 3, 30, tzinfo=UTC)
DINNER_AT = EATEN_AT

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
    eaten_at: datetime = EATEN_AT,
) -> Meal:
    """확정까지 끝난 식사 — 확인 API 가 남기는 모양(상태 EVALUATED + qqs_evaluations 1행)."""
    meal = make_meal(db, user_id=user.id, stage=meal_stage, status=status, eaten_at=eaten_at)
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


def _dailies(db, user_id: uuid.UUID) -> list[DailyFeedback]:
    db.expire_all()
    return list(
        db.execute(select(DailyFeedback).where(DailyFeedback.user_id == user_id)).scalars()
    )


def _only_daily(db, user_id: uuid.UUID) -> DailyFeedback:
    rows = _dailies(db, user_id)
    assert len(rows) == 1
    return rows[0]


def _source_ids(db, daily_feedback_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        db.execute(
            select(DailyFeedbackSource.meal_feedback_id).where(
                DailyFeedbackSource.daily_feedback_id == daily_feedback_id
            )
        ).scalars()
    )


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
    daily = _only_daily(db, user.id)
    assert result == {
        "mealFeedbackId": str(row.id),
        "dailyFeedbackId": str(daily.id),
        "suggestionCount": 1,
    }


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


# ─────────────────────────── 하루 피드백 upsert ───────────────────────────


def test_meal_feedback_is_upserted_into_daily_feedback(db):
    """끼니 피드백이 그날(KST) 하루 피드백이 된다 — AI 가 그날 앞 끼니를 누적해 쓴 문장이다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(80, 60, 70))

    run(db, _task(meal.id), FakeAi(body="누적 본문", model_version="stub-short-9"))

    daily = _only_daily(db, user.id)
    assert daily.feedback_date == date(2026, 8, 22)
    assert daily.summary == "누적 본문"
    assert daily.model_version == "stub-short-9"
    assert daily.safety_status == SafetyStatus.SAFE
    assert (daily.quantity_score, daily.quality_score, daily.satiety_score) == (80, 60, 70)
    assert _source_ids(db, daily.id) == {_only_row(db, meal.id).id}


def test_next_meal_overwrites_the_same_daily_row(db):
    """같은 날 다음 끼니는 같은 행을 덮는다 — 문장은 최신 것, 근거 링크는 쌓인다."""
    user = make_user(db)
    breakfast = _evaluated_meal(db, user, scores=(80, 60, 70), eaten_at=BREAKFAST_AT)
    run(db, _task(breakfast.id), FakeAi(body="아침"))
    first_id = _only_daily(db, user.id).id

    lunch = _evaluated_meal(db, user, scores=(60, 90, 50), eaten_at=LUNCH_AT)
    run(db, _task(lunch.id), FakeAi(body="아침+점심"))

    daily = _only_daily(db, user.id)
    assert daily.id == first_id
    assert daily.summary == "아침+점심"
    assert _source_ids(db, daily.id) == {
        _only_row(db, breakfast.id).id,
        _only_row(db, lunch.id).id,
    }


def test_daily_scores_are_the_day_average(db):
    """점수는 그날 채점된 끼니의 평균이다. 채점 못 한 축(NULL)은 평균에서 빠진다."""
    user = make_user(db)
    _evaluated_meal(db, user, scores=(80, None, 70))
    meal = _evaluated_meal(db, user, scores=(61, 90, None))

    run(db, _task(meal.id), FakeAi())

    daily = _only_daily(db, user.id)
    # 141 / 2 = 70.5 → 은행가 반올림 70
    assert (daily.quantity_score, daily.quality_score, daily.satiety_score) == (70, 90, 70)


def test_axis_unscored_all_day_stays_null(db):
    user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(None, None, 68))

    run(db, _task(meal.id), FakeAi())

    daily = _only_daily(db, user.id)
    assert daily.quantity_score is None
    assert daily.quality_score is None
    assert daily.satiety_score == 68


def test_daily_scores_ignore_deleted_other_day_and_other_user_meals(db):
    user = make_user(db)
    other_user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(50, 50, 50))
    deleted = _evaluated_meal(db, user, scores=(100, 100, 100))
    _soft_delete(db, deleted.id)
    yesterday = make_meal(
        db, user_id=user.id, eaten_at=datetime(2026, 8, 21, 3, 0, tzinfo=UTC),
        status=MealStatus.EVALUATED,
    )
    make_qqs_evaluation(db, meal_id=yesterday.id, quantity_score=100, quality_score=100, satiety_score=100)
    _evaluated_meal(db, other_user, scores=(100, 100, 100))

    run(db, _task(meal.id), FakeAi())

    daily = _only_daily(db, user.id)
    assert (daily.quantity_score, daily.quality_score, daily.satiety_score) == (50, 50, 50)


def test_daily_date_is_the_kst_day(db):
    """UTC 2026-08-22 16:00 은 KST 08-23 01:00 이다 — UTC 날짜로 자르면 전날 요약을 덮는다."""
    user = make_user(db)
    meal = make_meal(
        db, user_id=user.id, eaten_at=datetime(2026, 8, 22, 16, 0, tzinfo=UTC),
        status=MealStatus.EVALUATED,
    )
    make_qqs_evaluation(db, meal_id=meal.id)

    run(db, _task(meal.id), FakeAi())

    assert _only_daily(db, user.id).feedback_date == date(2026, 8, 23)


def _set_totals(db, meal: Meal, **totals: Decimal | None) -> None:
    """확정이 남기는 끼니 영양 합계(`meals.total_*`). 안 넘긴 성분은 0 이다."""
    values = {name: Decimal(0) for name in _TOTAL_COLUMNS}
    values.update(totals)
    db.execute(update(Meal).where(Meal.id == meal.id).values(**values))


_TOTAL_COLUMNS = (
    "total_kcal",
    "total_protein_g",
    "total_fat_g",
    "total_carb_g",
    "total_fiber_g",
    "total_sodium_mg",
)


def _daily_totals(daily: DailyFeedback) -> dict[str, Decimal | None]:
    return {name: getattr(daily, name) for name in _TOTAL_COLUMNS}


def test_daily_nutrient_totals_are_the_day_sum(db):
    """영양 합계는 평균이 아니라 그날 끼니의 합이다 — "오늘 단백질 몇 g" 이다."""
    user = make_user(db)
    breakfast = _evaluated_meal(db, user, eaten_at=BREAKFAST_AT)
    _set_totals(
        db, breakfast,
        total_kcal=Decimal("400.5"), total_protein_g=Decimal(12), total_fat_g=Decimal(10),
        total_carb_g=Decimal(65), total_fiber_g=Decimal(4), total_sodium_mg=Decimal(780),
    )
    lunch = _evaluated_meal(db, user, eaten_at=LUNCH_AT)
    _set_totals(
        db, lunch,
        total_kcal=Decimal(600), total_protein_g=Decimal("30.25"), total_fat_g=Decimal(20),
        total_carb_g=Decimal(70), total_fiber_g=Decimal(6), total_sodium_mg=Decimal(1200),
    )

    run(db, _task(lunch.id), FakeAi())

    assert _daily_totals(_only_daily(db, user.id)) == {
        "total_kcal": Decimal("1000.5"),
        "total_protein_g": Decimal("42.25"),
        "total_fat_g": Decimal(30),
        "total_carb_g": Decimal(135),
        "total_fiber_g": Decimal(10),
        "total_sodium_mg": Decimal(1980),
    }


def test_daily_nutrient_totals_are_recomputed_not_added(db):
    """같은 끼니가 다시 돌아도 두 번 더하지 않는다 — 매번 그날 전체를 다시 더해 덮는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    _set_totals(db, meal, total_protein_g=Decimal(20))

    run(db, _task(meal.id), FakeAi())
    run(db, _task(meal.id), FakeAi())

    assert _only_daily(db, user.id).total_protein_g == Decimal(20)


def test_daily_nutrient_totals_ignore_deleted_other_day_and_other_user_meals(db):
    user = make_user(db)
    other_user = make_user(db)
    meal = _evaluated_meal(db, user)
    _set_totals(db, meal, total_protein_g=Decimal(10))
    deleted = _evaluated_meal(db, user)
    _set_totals(db, deleted, total_protein_g=Decimal(100))
    _soft_delete(db, deleted.id)
    yesterday = _evaluated_meal(db, user, eaten_at=datetime(2026, 8, 21, 3, 0, tzinfo=UTC))
    _set_totals(db, yesterday, total_protein_g=Decimal(100))
    others = _evaluated_meal(db, other_user)
    _set_totals(db, others, total_protein_g=Decimal(100))

    run(db, _task(meal.id), FakeAi())

    assert _only_daily(db, user.id).total_protein_g == Decimal(10)


def test_meal_without_totals_is_left_out_of_the_day_sum(db):
    """합계가 NULL 인 끼니(확정 전 · 음식을 고쳐 재계산 대기)는 뺀다. 다시 확정하면 그 작업이 채운다."""
    user = make_user(db)
    make_meal(db, user_id=user.id, eaten_at=BREAKFAST_AT, status=MealStatus.ANALYZING)
    meal = _evaluated_meal(db, user)
    _set_totals(db, meal, total_kcal=Decimal(500))

    run(db, _task(meal.id), FakeAi())

    assert _only_daily(db, user.id).total_kcal == Decimal(500)


def test_daily_nutrient_totals_stay_null_without_any_totals(db):
    """그날 합계를 남긴 끼니가 하나도 없으면 NULL 이다 — 0 은 "아무것도 안 먹었다" 다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)

    run(db, _task(meal.id), FakeAi())

    assert set(_daily_totals(_only_daily(db, user.id)).values()) == {None}


@pytest.mark.parametrize("status", ["BLOCKED", "REVIEW_REQUIRED"])
def test_unsafe_meal_feedback_updates_nutrient_totals(db, status):
    """문장을 덮지 않을 때도 영양 합계는 이 끼니까지 반영한다 — 점수와 같은 이유다."""
    user = make_user(db)
    breakfast = _evaluated_meal(db, user, eaten_at=BREAKFAST_AT)
    _set_totals(db, breakfast, total_protein_g=Decimal(10))
    run(db, _task(breakfast.id), FakeAi(body="아침까지"))
    dinner = _evaluated_meal(db, user, eaten_at=DINNER_AT)
    _set_totals(db, dinner, total_protein_g=Decimal(25))

    run(db, _task(dinner.id), FakeAi(body="저녁", safety_status=status))

    daily = _only_daily(db, user.id)
    assert daily.summary == "아침까지"
    assert daily.total_protein_g == Decimal(35)


def test_earlier_meal_updates_nutrient_totals_without_the_summary(db):
    """앞 끼니를 뒤늦게 확정해 문장을 덮지 않는 경우에도 합계는 반영한다."""
    user = make_user(db)
    dinner = _evaluated_meal(db, user, eaten_at=DINNER_AT)
    _set_totals(db, dinner, total_protein_g=Decimal(25))
    run(db, _task(dinner.id), FakeAi(body="저녁까지"))
    breakfast = _evaluated_meal(db, user, eaten_at=BREAKFAST_AT)
    _set_totals(db, breakfast, total_protein_g=Decimal(10))

    run(db, _task(breakfast.id), FakeAi(body="아침"))

    daily = _only_daily(db, user.id)
    assert daily.summary == "저녁까지"
    assert daily.total_protein_g == Decimal(35)


@pytest.mark.parametrize("status", ["BLOCKED", "REVIEW_REQUIRED"])
def test_unsafe_meal_feedback_keeps_summary_but_updates_scores(db, status):
    """검수 안 된 문장으로 요약은 덮지 않지만 점수는 이 끼니까지 반영한다.

    하루 조회는 BLOCKED 여도 점수를 내보낸다 — 덮지 않으면 저녁을 먹었는데 점심까지의 점수가 남는다.
    """
    user = make_user(db)
    breakfast = _evaluated_meal(db, user, scores=(80, 80, 80), eaten_at=BREAKFAST_AT)
    run(db, _task(breakfast.id), FakeAi(body="아침까지", model_version="stub-short-1"))
    dinner = _evaluated_meal(db, user, scores=(40, 60, 20), eaten_at=DINNER_AT)

    result = run(db, _task(dinner.id), FakeAi(body="저녁", safety_status=status))

    daily = _only_daily(db, user.id)
    assert daily.summary == "아침까지"
    assert daily.model_version == "stub-short-1"
    assert daily.safety_status == SafetyStatus.SAFE
    assert (daily.quantity_score, daily.quality_score, daily.satiety_score) == (60, 70, 50)
    assert _source_ids(db, daily.id) == {_only_row(db, breakfast.id).id}
    assert result["dailyFeedbackId"] == str(daily.id)


def test_unsafe_scores_update_bumps_updated_at(db):
    """점수를 덮으면 updated_at 도 찍는다 — 안 찍으면 하루 조회가 이 끼니를 "새 식사" 로 보고 stale 을 낸다."""
    user = make_user(db)
    make_daily_feedback(db, user_id=user.id, feedback_date=EATEN_AT.date())
    old = datetime(2026, 1, 1, tzinfo=UTC)
    db.execute(update(DailyFeedback).values(updated_at=old))
    meal = _evaluated_meal(db, user)

    run(db, _task(meal.id), FakeAi(safety_status="BLOCKED"))

    assert _only_daily(db, user.id).updated_at > old


def test_unsafe_first_meal_creates_a_row_with_scores_only(db):
    """그날 첫 끼니가 SAFE 가 아니면 요약 없이 점수만 담은 행을 만든다. 판정은 AI 가 준 그대로다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user, scores=(80, 60, 70))

    run(db, _task(meal.id), FakeAi(body="저녁", safety_status="BLOCKED"))

    daily = _only_daily(db, user.id)
    assert daily.summary is None
    assert daily.model_version is None
    assert daily.safety_status == SafetyStatus.BLOCKED
    assert (daily.quantity_score, daily.quality_score, daily.satiety_score) == (80, 60, 70)
    assert _source_ids(db, daily.id) == set()


def test_safe_meal_after_unsafe_fills_the_summary(db):
    """요약 없이 만든 행은 다음 SAFE 끼니가 채운다."""
    user = make_user(db)
    first = _evaluated_meal(db, user, eaten_at=BREAKFAST_AT)
    run(db, _task(first.id), FakeAi(safety_status="BLOCKED"))

    second = _evaluated_meal(db, user, eaten_at=LUNCH_AT)
    run(db, _task(second.id), FakeAi(body="점심까지"))

    daily = _only_daily(db, user.id)
    assert daily.summary == "점심까지"
    assert daily.safety_status == SafetyStatus.SAFE
    assert _source_ids(db, daily.id) == {_only_row(db, second.id).id}


def test_earlier_meal_does_not_overwrite_a_later_meals_summary(db):
    """아침을 뒤늦게 입력·재확정해도 저녁 문장이 남는다. 점수와 아침 피드백은 반영된다."""
    user = make_user(db)
    dinner = _evaluated_meal(db, user, scores=(40, 40, 40), eaten_at=DINNER_AT)
    run(db, _task(dinner.id), FakeAi(body="저녁까지"))
    breakfast = _evaluated_meal(db, user, scores=(80, 80, 80), eaten_at=BREAKFAST_AT)

    result = run(db, _task(breakfast.id), FakeAi(body="아침"))

    daily = _only_daily(db, user.id)
    assert daily.summary == "저녁까지"
    assert (daily.quantity_score, daily.quality_score, daily.satiety_score) == (60, 60, 60)
    assert _source_ids(db, daily.id) == {_only_row(db, dinner.id).id}
    assert _only_row(db, breakfast.id).body == "아침"
    assert result["dailyFeedbackId"] == str(daily.id)


def test_later_meal_whose_job_has_not_finished_leaves_no_safe_empty_row(db):
    """늦은 끼니가 확정만 되고 작업이 아직이면 점수만 담고, 문장 없는 행을 SAFE 로 만들지 않는다."""
    user = make_user(db)
    _evaluated_meal(db, user, eaten_at=DINNER_AT)
    breakfast = _evaluated_meal(db, user, eaten_at=BREAKFAST_AT)

    run(db, _task(breakfast.id), FakeAi(body="아침"))

    daily = _only_daily(db, user.id)
    assert daily.summary is None
    assert daily.safety_status == SafetyStatus.REVIEW_REQUIRED
    assert daily.satiety_score == 68


@pytest.mark.parametrize("status", [MealStatus.REVIEW_REQUIRED, MealStatus.ANALYZING])
def test_unconfirmed_later_meal_does_not_block_the_update(db, status):
    """확인 화면에서 멈춘 늦은 끼니는 비교에서 뺀다 — 세면 하루 피드백이 영영 안 바뀐다."""
    user = make_user(db)
    make_meal(db, user_id=user.id, eaten_at=DINNER_AT, status=status)
    breakfast = _evaluated_meal(db, user, eaten_at=BREAKFAST_AT)

    run(db, _task(breakfast.id), FakeAi(body="아침"))

    assert _only_daily(db, user.id).summary == "아침"


def test_deleted_or_next_day_later_meal_does_not_block_the_update(db):
    user = make_user(db)
    deleted = _evaluated_meal(db, user, eaten_at=DINNER_AT)
    _soft_delete(db, deleted.id)
    # KST 08-23 01:00 — 다음 날이다
    _evaluated_meal(db, user, eaten_at=datetime(2026, 8, 22, 16, 0, tzinfo=UTC))
    breakfast = _evaluated_meal(db, user, eaten_at=BREAKFAST_AT)

    run(db, _task(breakfast.id), FakeAi(body="아침"))

    daily = next(d for d in _dailies(db, user.id) if d.feedback_date == date(2026, 8, 22))
    assert daily.summary == "아침"


def test_same_eaten_at_is_ordered_by_id(db):
    """먹은 시각이 같으면 id 가 큰 끼니가 늦은 끼니다."""
    user = make_user(db)
    a = _evaluated_meal(db, user, eaten_at=LUNCH_AT)
    b = _evaluated_meal(db, user, eaten_at=LUNCH_AT)
    smaller, larger = sorted([a, b], key=lambda m: m.id)

    run(db, _task(larger.id), FakeAi(body="큰 id"))
    run(db, _task(smaller.id), FakeAi(body="작은 id"))

    assert _only_daily(db, user.id).summary == "큰 id"


def test_other_users_later_meal_does_not_block_the_update(db):
    user = make_user(db)
    other = make_user(db)
    _evaluated_meal(db, other, eaten_at=DINNER_AT)
    breakfast = _evaluated_meal(db, user, eaten_at=BREAKFAST_AT)

    run(db, _task(breakfast.id), FakeAi(body="아침"))

    assert _only_daily(db, user.id).summary == "아침"


def test_skipped_task_does_not_touch_daily(db):
    user = make_user(db)
    meal = _evaluated_meal(db, user)
    ai = FakeAi(on_call=lambda: _soft_delete(db, meal.id))

    run(db, _task(meal.id), ai)

    assert _dailies(db, user.id) == []


def test_rerun_keeps_a_single_source_link(db):
    """같은 끼니가 다시 돌아도 근거 링크는 하나다 — PK(daily, meal_feedback) 에 걸려 깨지지 않는다."""
    user = make_user(db)
    meal = _evaluated_meal(db, user)

    run(db, _task(meal.id), FakeAi(body="A"))
    run(db, _task(meal.id), FakeAi(body="B"))

    daily = _only_daily(db, user.id)
    assert daily.summary == "B"
    assert _source_ids(db, daily.id) == {_only_row(db, meal.id).id}


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

    task = _task(meal.id)
    result = get_job(task.type).run_inline(db, task, FakeAi())

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


# ─────────────────────────── 3단계 계약 ───────────────────────────


def test_load_hands_over_plain_data_not_orm_objects(db):
    from app.db.base import Base

    user = make_user(db)
    meal = _evaluated_meal(db, user)

    ctx = feedback_meal.load(db, _task(meal.id))

    assert ctx.meal_id == meal.id
    assert not any(isinstance(value, Base) for value in vars(ctx).values())
    assert ctx.request["scope"] == "MEAL"
