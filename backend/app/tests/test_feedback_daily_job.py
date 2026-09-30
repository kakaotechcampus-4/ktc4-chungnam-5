"""`feedback.daily` 워커 — 하루치 SAFE 끼니 피드백을 모아 daily_feedbacks 1행을 만든다.

틀리기 쉬운 것:
  - 걸러야 할 근거(BLOCKED · REVIEW_REQUIRED · 삭제된 식사 · 다른 날 · 다른 사용자)를 섞는 것
  - 두 번 돌았을 때 행·근거 링크가 쌓이는 것
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
from sqlalchemy import func, select, update

from app.core.time import KST
from app.infra.queue import ClaimedTask
from app.models.enums import MealType, MedicationStage, SafetyStatus
from app.models.feedback import DailyFeedback, DailyFeedbackSource
from app.tests.factories import (
    make_meal,
    make_meal_feedback,
    make_meal_item,
    make_qqs_evaluation,
    make_user,
)
from app.worker.dispatch import handle
from app.worker.jobs.feedback_daily import run

D = "2026-08-21"
D_DATE = datetime(2026, 8, 21).date()


# ─────────────────────────── Fake ───────────────────────────


class FakeAi:
    """`/short-feedback` 호출을 기록하고 정해진 응답을 돌려주는 가짜 AI.

    `ai-stub/schemas.py` ShortFeedbackResponse 모양이다. DAILY 에서 suggestions 는 null.
    """

    def __init__(
        self,
        *,
        body: str = "오늘은 단백질을 챙기셨어요.",
        safety_status: str = "SAFE",
        reasoning: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.body = body
        self.safety_status = safety_status
        self.reasoning = reasoning
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("feedback.daily 가 analyze_meal 을 불렀다")

    def short_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return {
            "body": self.body,
            "modelVersion": "stub-short-0",
            "safetyStatus": self.safety_status,
            "reasoning": self.reasoning,
            "suggestions": None,
        }

    def long_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("feedback.daily 가 long_feedback 을 불렀다")


# ─────────────────────────── helper ───────────────────────────


def _task(user_id: uuid.UUID, date: str = D) -> ClaimedTask:
    return ClaimedTask(
        id=uuid.uuid4(),
        type="feedback.daily",
        payload={"userId": str(user_id), "date": date},
        attempts=0,
    )


def _kst(hour: int, minute: int = 0, day: int = 21) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=KST)


def _evidence(
    db,
    user,
    *,
    eaten_at: datetime | None = None,
    meal_type: MealType = MealType.LUNCH,
    scores: tuple[Any, Any, Any] | None = (60, 60, 60),
    safety_status: SafetyStatus = SafetyStatus.SAFE,
    body: str | None = "채소를 먼저 드셔서 좋았어요.",
    stage: MedicationStage = MedicationStage.MAINTENANCE,
):
    """식사 + Q/Q/S(scores=None 이면 평가 행 없음) + 끼니 피드백. (meal, meal_feedback) 을 돌려준다."""
    meal = make_meal(
        db,
        user_id=user.id,
        eaten_at=eaten_at or _kst(12, 30),
        meal_type=meal_type,
        stage=stage,
    )
    if scores is not None:
        quantity, quality, satiety = scores
        make_qqs_evaluation(
            db,
            meal_id=meal.id,
            quantity_score=quantity,
            quality_score=quality,
            satiety_score=satiety,
        )
    feedback = make_meal_feedback(
        db, user_id=user.id, meal_id=meal.id, body=body, safety_status=safety_status
    )
    return meal, feedback


def _daily_rows(db, user_id: uuid.UUID, feedback_date=D_DATE) -> list[DailyFeedback]:
    # 워커가 upsert(Core INSERT … ON CONFLICT)로 쓸 수 있어 identity map 을 믿지 않는다.
    db.expire_all()
    return list(
        db.execute(
            select(DailyFeedback).where(
                DailyFeedback.user_id == user_id,
                DailyFeedback.feedback_date == feedback_date,
            )
        ).scalars()
    )


def _only_row(db, user_id: uuid.UUID) -> DailyFeedback:
    rows = _daily_rows(db, user_id)
    assert len(rows) == 1
    return rows[0]


def _source_ids(db, daily_feedback_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(DailyFeedbackSource.meal_feedback_id).where(
                DailyFeedbackSource.daily_feedback_id == daily_feedback_id
            )
        ).scalars()
    )


def _count_sources(db, daily_feedback_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count())
        .select_from(DailyFeedbackSource)
        .where(DailyFeedbackSource.daily_feedback_id == daily_feedback_id)
    ).scalar_one()


def _soft_delete(db, meal) -> None:
    meal.deleted_at = datetime.now(UTC)
    db.flush()


def _scores(row: DailyFeedback) -> tuple[Any, Any, Any]:
    return (row.quantity_score, row.quality_score, row.satiety_score)


# ─────────────────────────── 정상 ───────────────────────────


def test_safe_meal_feedbacks_make_one_daily_feedback(db):
    """W1: 그날 SAFE 끼니 피드백 둘로 하루 피드백 1행을 만든다 — 본문·모델·안전 상태·근거 링크."""
    user = make_user(db)
    _, lunch = _evidence(db, user, meal_type=MealType.LUNCH, eaten_at=_kst(12, 30))
    _, dinner = _evidence(db, user, meal_type=MealType.DINNER, eaten_at=_kst(19, 0))
    ai = FakeAi(body="하루 요약 본문")

    run(db, _task(user.id), ai)

    row = _only_row(db, user.id)
    assert row.summary == "하루 요약 본문"
    assert row.model_version == "stub-short-0"
    assert row.safety_status == SafetyStatus.SAFE
    assert set(_source_ids(db, row.id)) == {lunch.id, dinner.id}


def test_daily_scores_are_the_average_of_meal_scores(db):
    """W2: 하루 점수는 끼니 점수의 평균이다 — 새로 채점하지 않는다. AI 에도 같은 값이 간다."""
    user = make_user(db)
    _evidence(db, user, meal_type=MealType.LUNCH, eaten_at=_kst(12, 30), scores=(60, 70, 80))
    _evidence(db, user, meal_type=MealType.DINNER, eaten_at=_kst(19, 0), scores=(80, 90, 100))
    ai = FakeAi()

    run(db, _task(user.id), ai)

    row = _only_row(db, user.id)
    assert _scores(row) == (70, 80, 90)
    assert ai.calls[0]["qqs"] == {"quantity": 70, "quality": 80, "satiety": 90}


def test_ai_request_is_daily_scope_with_meals(db):
    """W3: AI 요청은 scope=DAILY · date · userId 와 끼니별 mealType · summary(끼니 피드백 본문) · qqs 다."""
    user = make_user(db)
    _evidence(
        db, user, meal_type=MealType.LUNCH, eaten_at=_kst(12, 30),
        scores=(60, 70, 80), body="점심 본문",
    )
    _evidence(
        db, user, meal_type=MealType.DINNER, eaten_at=_kst(19, 0),
        scores=(80, 90, 100), body="저녁 본문",
    )
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert len(ai.calls) == 1
    payload = ai.calls[0]
    assert payload["scope"] == "DAILY"
    assert payload["date"] == D
    assert payload["userId"] == str(user.id)
    assert len(payload["meals"]) == 2
    meals = {meal["mealType"]: meal for meal in payload["meals"]}
    assert set(meals) == {"LUNCH", "DINNER"}
    assert meals["LUNCH"]["summary"] == "점심 본문"
    assert meals["LUNCH"]["qqs"] == {"quantity": 60, "quality": 70, "satiety": 80}
    assert meals["DINNER"]["summary"] == "저녁 본문"
    assert meals["DINNER"]["qqs"] == {"quantity": 80, "quality": 90, "satiety": 100}


def test_ai_stage_is_the_last_meal_snapshot_stage(db):
    """W4: AI 요청의 stage 는 그날 마지막 식사 시점의 스냅샷 단계다 (get_day_stages 기준).

    기대값(INITIAL)이 factories 기본값(MAINTENANCE)과 다르고, 가장 늦은 식사를 생성 순서의
    가운데에 둔다 — 기본값을 쓰거나 생성 순서(created_at·id)의 첫 행·마지막 행을 고르는
    구현을 모두 잡는다.
    """
    user = make_user(db)
    _evidence(
        db, user, meal_type=MealType.LUNCH, eaten_at=_kst(13, 0),
        stage=MedicationStage.MAINTENANCE,
    )
    _evidence(
        db, user, meal_type=MealType.DINNER, eaten_at=_kst(19, 0),
        stage=MedicationStage.INITIAL,
    )
    _evidence(
        db, user, meal_type=MealType.BREAKFAST, eaten_at=_kst(8, 0),
        stage=MedicationStage.MAINTENANCE,
    )
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls[0]["stage"] == "INITIAL"


def test_rerun_overwrites_summary(db):
    """W16: 재실행하면 summary 를 새 AI 본문으로 덮어쓴다."""
    user = make_user(db)
    _evidence(db, user)

    run(db, _task(user.id), FakeAi(body="A"))
    run(db, _task(user.id), FakeAi(body="B"))

    assert _only_row(db, user.id).summary == "B"


def test_rerun_keeps_id_and_created_at(db):
    """R13(W21 교체): 재실행해도 같은 행(id 유지)이고 created_at 은 최초 생성 시각 그대로다.

    created_at 은 최초 INSERT 시각이고, 마지막 생성 시각은 updated_at 이 맡는다 (#46 과 같은 결론).
    트랜잭션 안에서 now() 가 고정되므로 첫 실행 뒤 created_at 을 과거로 옮겨 두고,
    재실행이 그 값을 건드리지 않는지로 본다.
    """
    user = make_user(db)
    _evidence(db, user)
    run(db, _task(user.id), FakeAi())
    first = _only_row(db, user.id)
    past = datetime(2000, 1, 1, tzinfo=UTC)
    db.execute(
        update(DailyFeedback).where(DailyFeedback.id == first.id).values(created_at=past)
    )
    db.flush()

    run(db, _task(user.id), FakeAi())

    rows = _daily_rows(db, user.id)
    assert len(rows) == 1
    assert rows[0].id == first.id
    assert rows[0].created_at == past


def test_rerun_refreshes_updated_at(db):
    """R14: 재실행하면 같은 행의 updated_at 이 새로 찍힌다 — generatedAt · stale 판정 기준이다.

    ON CONFLICT 는 SQLAlchemy onupdate 를 타지 않으므로 upsert 가 직접 넣어야 한다.
    첫 실행 뒤 updated_at 을 과거로 옮겨 두고, 재실행이 그 값을 바꾸는지로 본다.
    """
    user = make_user(db)
    _evidence(db, user)
    run(db, _task(user.id), FakeAi())
    first = _only_row(db, user.id)
    past = datetime(2000, 1, 1, tzinfo=UTC)
    db.execute(
        update(DailyFeedback).where(DailyFeedback.id == first.id).values(updated_at=past)
    )
    db.flush()

    run(db, _task(user.id), FakeAi())

    rows = _daily_rows(db, user.id)
    assert len(rows) == 1
    assert rows[0].updated_at > past


def test_first_run_sets_updated_at_equal_to_created_at(db):
    """R15: 처음 만든 행은 updated_at 이 채워져 있고 created_at 과 같다.

    축: 회귀 — 컬럼 기본값(now())이 빠지면 첫 생성 행의 generatedAt 이 비거나 어긋난다.
    """
    user = make_user(db)
    _evidence(db, user)

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert row.updated_at is not None
    assert row.updated_at == row.created_at


# ─────────────────────────── 근거 거르기 ───────────────────────────


def test_blocked_meal_feedback_is_excluded(db):
    """W5: BLOCKED 끼니 피드백은 근거·점수·AI 요청 어디에도 들어가지 않는다."""
    user = make_user(db)
    _, safe = _evidence(db, user, eaten_at=_kst(12, 30), scores=(60, 60, 60))
    _evidence(
        db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER,
        scores=(100, 100, 100), safety_status=SafetyStatus.BLOCKED,
    )
    ai = FakeAi()

    run(db, _task(user.id), ai)

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [safe.id]
    assert row.quantity_score == 60
    assert len(ai.calls[0]["meals"]) == 1


def test_review_required_meal_feedback_is_excluded(db):
    """W6: REVIEW_REQUIRED 끼니 피드백도 BLOCKED 처럼 근거·점수에서 빠진다."""
    user = make_user(db)
    _, safe = _evidence(db, user, eaten_at=_kst(12, 30), scores=(60, 60, 60))
    _evidence(
        db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER,
        scores=(100, 100, 100), safety_status=SafetyStatus.REVIEW_REQUIRED,
    )

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [safe.id]
    assert row.quantity_score == 60


def test_safe_meal_feedback_is_kept_among_unsafe_ones(db):
    """W7: BLOCKED · REVIEW_REQUIRED 와 섞여 있어도 SAFE 끼니 피드백은 근거로 남는다 (W5·W6 짝)."""
    user = make_user(db)
    _, safe = _evidence(db, user, eaten_at=_kst(12, 30))
    _evidence(
        db, user, eaten_at=_kst(8, 0), meal_type=MealType.BREAKFAST,
        safety_status=SafetyStatus.BLOCKED,
    )
    _evidence(
        db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER,
        safety_status=SafetyStatus.REVIEW_REQUIRED,
    )

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert safe.id in _source_ids(db, row.id)


def test_deleted_meal_is_excluded(db):
    """W8: 삭제된(soft delete) 식사의 끼니 피드백은 근거·점수에서 빠진다."""
    user = make_user(db)
    _, alive = _evidence(db, user, eaten_at=_kst(12, 30), scores=(60, 60, 60))
    deleted_meal, _ = _evidence(
        db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER, scores=(100, 100, 100)
    )
    _soft_delete(db, deleted_meal)

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [alive.id]
    assert row.quantity_score == 60


def test_kst_early_morning_meal_belongs_to_that_day(db):
    """W9: KST 00:30(UTC 로는 전날 15:30) 식사는 그날 근거다.

    UTC 날짜로 자르면 이 식사가 전날로 밀려 빠진다 — KST 경계(kst_day)를 쓰는지 본다.
    """
    user = make_user(db)
    _, early = _evidence(
        db, user, eaten_at=datetime(2026, 8, 20, 15, 30, tzinfo=UTC), meal_type=MealType.SNACK
    )

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [early.id]


def test_previous_day_kst_late_night_meal_is_excluded(db):
    """W9b: 전날 KST 23:50(UTC 전날 14:50) 식사는 그날 근거가 아니다 (W9 짝)."""
    user = make_user(db)
    _, today = _evidence(db, user, eaten_at=_kst(12, 30), scores=(60, 60, 60))
    _evidence(
        db, user, eaten_at=datetime(2026, 8, 20, 14, 50, tzinfo=UTC),
        meal_type=MealType.SNACK, scores=(100, 100, 100),
    )

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [today.id]
    assert row.quantity_score == 60


def test_next_day_kst_meal_is_excluded(db):
    """W10: 다음날 KST 00:10(UTC 15:10) 식사는 그날 근거가 아니다."""
    user = make_user(db)
    _, today = _evidence(db, user, eaten_at=_kst(12, 30), scores=(60, 60, 60))
    _evidence(
        db, user, eaten_at=datetime(2026, 8, 21, 15, 10, tzinfo=UTC),
        meal_type=MealType.SNACK, scores=(100, 100, 100),
    )

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [today.id]
    assert row.quantity_score == 60


def test_meal_at_kst_midnight_start_belongs_to_that_day(db):
    """W28: 그날 00:00:00 KST 정각 식사는 그날 근거다 — 하루 경계의 시작은 포함이다."""
    user = make_user(db)
    _, midnight = _evidence(db, user, eaten_at=_kst(0, 0), meal_type=MealType.SNACK)

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [midnight.id]


def test_meal_at_next_kst_midnight_is_excluded(db):
    """W28b: 다음날 00:00:00 KST 정각 식사는 그날 근거가 아니다 — 하루 경계의 끝은 제외다 (W28 짝)."""
    user = make_user(db)
    _, today = _evidence(db, user, eaten_at=_kst(12, 30))
    _evidence(db, user, eaten_at=_kst(0, 0, day=22), meal_type=MealType.SNACK)

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [today.id]


def test_meal_without_evaluation_is_excluded(db):
    """W22: Q/Q/S 평가 행이 없는 끼니는 근거·점수·AI 요청에서 빠진다 (Q6)."""
    user = make_user(db)
    _, evaluated = _evidence(db, user, eaten_at=_kst(12, 30), scores=(60, 60, 60))
    _evidence(db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER, scores=None)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [evaluated.id]
    assert row.quantity_score == 60
    assert len(ai.calls[0]["meals"]) == 1


def test_meal_with_a_null_score_is_excluded(db):
    """W24: 세 점수 중 하나라도 null 인 끼니는 근거·점수에서 빠진다 (Q6)."""
    user = make_user(db)
    _, complete = _evidence(db, user, eaten_at=_kst(12, 30), scores=(60, 70, 80))
    _evidence(
        db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER, scores=(100, 100, None)
    )

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [complete.id]
    assert _scores(row) == (60, 70, 80)


def test_safe_feedback_with_null_body_is_excluded(db):
    """W25: body 가 null 인 SAFE 끼니 피드백은 근거에서 빠진다 — AI 에 넘길 summary 가 없다 (Q7)."""
    user = make_user(db)
    _, with_body = _evidence(db, user, eaten_at=_kst(12, 30), body="a")
    _evidence(db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER, body=None)

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [with_body.id]


# ─────────────────────────── 금지 ───────────────────────────


def test_other_users_data_is_not_mixed(db):
    """W11: 다른 사용자의 그날 데이터는 근거·점수에 섞이지 않고, 그 사용자의 행도 만들지 않는다."""
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _, mine = _evidence(db, user, scores=(60, 60, 60))
    _, theirs = _evidence(db, other, scores=(100, 100, 100))

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    source_ids = _source_ids(db, row.id)
    assert theirs.id not in source_ids
    assert source_ids == [mine.id]
    assert row.quantity_score == 60
    assert _daily_rows(db, other.id) == []


def test_rerun_keeps_one_row_without_duplicate_sources(db):
    """W14: 같은 날 두 번 돌아도 daily_feedbacks 는 1행이고 근거 링크가 중복되지 않는다."""
    user = make_user(db)
    _evidence(db, user, eaten_at=_kst(12, 30))
    _evidence(db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER)

    run(db, _task(user.id), FakeAi())
    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _count_sources(db, row.id) == 2


def test_rerun_drops_links_to_excluded_evidence(db):
    """W15: 재실행 때 더는 근거가 아닌 끼니(삭제됨)는 링크에서 빠지고 점수도 다시 계산된다."""
    user = make_user(db)
    _, kept = _evidence(db, user, eaten_at=_kst(12, 30), scores=(60, 60, 60))
    removed_meal, _ = _evidence(
        db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER, scores=(100, 100, 100)
    )
    run(db, _task(user.id), FakeAi())
    _soft_delete(db, removed_meal)

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == [kept.id]
    assert row.quantity_score == 60


def test_ai_blocked_result_is_stored_as_blocked(db):
    """W17: AI 가 BLOCKED 를 주면 그대로 BLOCKED 로 저장한다 — SAFE 로 올리지 않는다 (규칙 1)."""
    user = make_user(db)
    _evidence(db, user)

    run(db, _task(user.id), FakeAi(safety_status="BLOCKED"))

    assert _only_row(db, user.id).safety_status == SafetyStatus.BLOCKED


def test_worker_does_not_commit(db, monkeypatch):
    """W18: 워커는 커밋하지 않는다 — 큐가 DONE 과 함께 커밋해야 실패 시 도메인 변경까지 롤백된다."""
    user = make_user(db)
    _evidence(db, user)
    commits: list[None] = []
    monkeypatch.setattr(db, "commit", lambda: commits.append(None))

    run(db, _task(user.id), FakeAi())

    assert len(commits) == 0


def test_ai_failure_propagates_and_leaves_no_row(db):
    """W19: AI 가 실패하면 예외를 그대로 올리고 행을 남기지 않는다 — 삼키면 DONE 으로 커밋된다.

    축: 회귀. 워커 뼈대가 이미 AI 를 먼저 부르고 예외를 올리므로 RED 에서도 통과한다.
    GREEN 구현이 예외를 삼키는 회귀를 막는 보호 테스트다.
    """
    user = make_user(db)
    _evidence(db, user)

    with pytest.raises(RuntimeError, match="AI 500"):
        run(db, _task(user.id), FakeAi(error=RuntimeError("AI 500")))

    assert _daily_rows(db, user.id) == []


def test_logs_do_not_contain_food_names_or_feedback_bodies(db, caplog):
    """W20: 로그에 음식명·끼니 피드백 본문·AI 본문·근거를 남기지 않는다 (규칙 6)."""
    caplog.set_level(logging.DEBUG)
    user = make_user(db)
    meal, _ = _evidence(db, user, body="끼니본문-SECRET-7f3a")
    make_meal_item(db, meal_id=meal.id, display_name="음식명-SECRET-9c1d")

    result = run(
        db,
        _task(user.id),
        FakeAi(body="AI본문-SECRET-2b8e", reasoning="AI근거-SECRET-4d6f"),
    )

    # 반환값은 task_queue.result 에 그대로 남는다 — 로그와 같은 기준을 적용한다.
    result_text = json.dumps(result, ensure_ascii=False, default=str)
    for secret in (
        "끼니본문-SECRET-7f3a",
        "음식명-SECRET-9c1d",
        "김치찌개",  # make_meal 의 raw_text
        "AI본문-SECRET-2b8e",
        "AI근거-SECRET-4d6f",
    ):
        assert secret not in caplog.text
        assert secret not in result_text


def test_ai_payload_is_json_serializable(db):
    """W27: AI 요청 payload 는 기본 인코더로 json.dumps 된다 — Decimal·date 객체가 섞이지 않는다.

    실제 HTTP 클라이언트(httpx json=)가 직렬화에 실패하면 모든 작업이 재시도 끝에 FAILED 가 된다.
    FakeAi 는 dict 를 그대로 받아 이 실패를 가리므로 여기서 직접 확인한다. 점수는
    Numeric(5,2) 라 DB 에서 Decimal 로 읽히고, 소수 점수로 평균·끼니 점수 모두 그 경로를 탄다.
    """
    user = make_user(db)
    _evidence(
        db, user, eaten_at=_kst(12, 30),
        scores=(Decimal("60.50"), Decimal("70.25"), Decimal("80.75")),
    )
    _evidence(
        db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER,
        scores=(Decimal("71.50"), Decimal("65.00"), Decimal("90.00")),
    )
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert len(ai.calls) == 1
    json.dumps(ai.calls[0])


# ─────────────────────────── 경계 ───────────────────────────


def test_no_evidence_creates_nothing_and_skips_ai(db):
    """W12: 그날 근거가 하나도 없으면 행을 만들지 않고 AI 도 부르지 않는다."""
    user = make_user(db)
    ai = FakeAi()

    result = run(db, _task(user.id), ai)

    assert result is None
    assert _daily_rows(db, user.id) == []
    assert ai.calls == []


def test_all_evidence_filtered_out_creates_nothing(db):
    """W13: 근거가 전부 걸러지면(BLOCKED 1건뿐) 행을 만들지 않고 AI 도 부르지 않는다."""
    user = make_user(db)
    _evidence(db, user, safety_status=SafetyStatus.BLOCKED)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert _daily_rows(db, user.id) == []
    assert ai.calls == []


def test_existing_row_is_deleted_when_evidence_drops_to_zero(db):
    """W23: 기존 행이 있는데 근거가 0 이 되면 그 행과 근거 링크를 지우고 AI 는 부르지 않는다 (D6(b)).

    남겨 두면 삭제된 식사로 만든 낡은 요약이 계속 보인다.
    """
    user = make_user(db)
    meal, _ = _evidence(db, user)
    first_ai = FakeAi()
    run(db, _task(user.id), first_ai)
    old_id = _only_row(db, user.id).id
    _soft_delete(db, meal)
    second_ai = FakeAi()

    run(db, _task(user.id), second_ai)

    assert _daily_rows(db, user.id) == []
    assert _count_sources(db, old_id) == 0
    assert second_ai.calls == []


def test_zero_evidence_does_not_delete_other_days_row(db):
    """W23b: 근거 0 인 날을 처리해도 다른 날짜의 행은 지우지 않는다 (W23 짝)."""
    user = make_user(db)
    _evidence(db, user, eaten_at=_kst(12, 30, day=20))
    run(db, _task(user.id, "2026-08-20"), FakeAi())
    previous_day = datetime(2026, 8, 20).date()
    assert len(_daily_rows(db, user.id, previous_day)) == 1

    run(db, _task(user.id, D), FakeAi())

    assert len(_daily_rows(db, user.id, previous_day)) == 1


def test_zero_evidence_does_not_delete_other_users_row(db):
    """W23c: U 의 근거가 0 이 되어 U 의 행을 지울 때, U2 의 같은 날 행과 근거 링크는 남는다."""
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    my_meal, _ = _evidence(db, user)
    _, theirs = _evidence(db, other)
    run(db, _task(user.id), FakeAi())
    run(db, _task(other.id), FakeAi())
    other_row_id = _only_row(db, other.id).id
    _soft_delete(db, my_meal)

    run(db, _task(user.id), FakeAi())

    assert _daily_rows(db, user.id) == []
    other_rows = _daily_rows(db, other.id)
    assert [row.id for row in other_rows] == [other_row_id]
    assert _source_ids(db, other_row_id) == [theirs.id]


def test_average_is_rounded_with_python_round(db):
    """W26: 평균의 반올림은 Python round(은행가 반올림)다 — 72.5 → 72 (Q5, dashboard 와 동일)."""
    user = make_user(db)
    _evidence(db, user, eaten_at=_kst(12, 30), scores=(72, 60, 60))
    _evidence(db, user, eaten_at=_kst(19, 0), meal_type=MealType.DINNER, scores=(73, 60, 60))
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert _only_row(db, user.id).quantity_score == Decimal("72")
    assert ai.calls[0]["qqs"]["quantity"] == 72


# ─────────────────────────── dispatch ───────────────────────────


def test_handle_routes_feedback_daily_to_the_handler(db):
    """D2: dispatch.handle 이 feedback.daily 를 핸들러로 보낸다 — 근거 없는 사용자면 None, 0행."""
    user = make_user(db)

    result = handle(db, _task(user.id), FakeAi())

    assert result is None
    assert _daily_rows(db, user.id) == []
