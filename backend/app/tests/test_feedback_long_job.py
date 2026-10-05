"""`feedback.long` 워커 — 기간 안 Q/Q/S 시계열과 SAFE 하루 피드백으로 long_term_feedbacks 1행을 만든다.

틀리기 쉬운 것:
  - 시계열에 섞이면 안 되는 식사(삭제 · 기간 밖 · 다른 사용자 · 점수 빠진 날)를 넣는 것
  - 근거에 SAFE 가 아닌 하루 피드백을 넣는 것
  - 데이터가 부족한데 AI 를 부르거나, 낡은 행을 남기는 것
  - 두 번 돌았을 때 근거 링크가 쌓이는 것 · updated_at 을 안 찍는 것
  - 워커 안에서 커밋하는 것 — 큐가 DONE 과 함께 커밋해야 실패 시 통째로 롤백된다
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from app.core.time import KST
from app.infra.queue import ClaimedTask
from app.models.enums import (
    FeedbackPeriodType,
    FeedbackStatus,
    MedicationStage,
    SafetyStatus,
    TaskStatus,
)
from app.models.feedback import ALL_PERIOD_START, LongTermFeedback, LongTermFeedbackSource
from app.models.task import Task
from app.services.insight import get_long_term_insight
from app.tests.factories import (
    make_daily_feedback,
    make_meal,
    make_qqs_evaluation,
    make_user,
)
from app.worker.dispatch import handle
from app.worker.jobs import feedback_long

# 세 단계를 세션 하나로 이어 돈다. 시나리오 단언은 3단계 분리 전과 같다.
run = feedback_long.JOB.run_inline

START = date(2026, 8, 15)
END = date(2026, 8, 21)


# ─────────────────────────── Fake ───────────────────────────


class FakeAi:
    """`/long-feedback` 호출을 기록하고 정해진 응답을 돌려주는 가짜 AI.

    `ai-stub/schemas.py` LongFeedbackResponse 모양이다 — chartData 는 없다.
    """

    def __init__(
        self,
        *,
        trend_summary: str = "이번 주는 Quality 가 올랐어요.",
        recommendation: str = "저녁에 단백질을 앞으로 당겨 보세요.",
        safety_status: str = "SAFE",
        error: Exception | None = None,
    ) -> None:
        self.trend_summary = trend_summary
        self.recommendation = recommendation
        self.safety_status = safety_status
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("feedback.long 이 analyze_meal 을 불렀다")

    def short_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("feedback.long 이 short_feedback 을 불렀다")

    def long_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return {
            "trendSummary": self.trend_summary,
            "recommendation": self.recommendation,
            "modelVersion": "stub-long-0",
            "safetyStatus": self.safety_status,
        }


# ─────────────────────────── helper ───────────────────────────


def _task(
    user_id: uuid.UUID,
    *,
    period_type: str = "WEEKLY",
    period_start: date = START,
    period_end: date = END,
) -> ClaimedTask:
    # payload 모양은 services/insight.py::refresh_long_term_insight 가 넣는 그대로다.
    return ClaimedTask(
        id=uuid.uuid4(),
        type="feedback.long",
        payload={
            "userId": str(user_id),
            "periodType": period_type,
            "periodStart": period_start.isoformat(),
            "periodEnd": period_end.isoformat(),
        },
        attempts=0,
    )


def _scored_meal(
    db,
    user,
    day: date,
    *,
    scores: tuple[Any, Any, Any] = (60, 60, 60),
    hour: int = 12,
    minute: int = 30,
    stage: MedicationStage = MedicationStage.MAINTENANCE,
):
    """그날(KST) hour:minute 에 먹은 식사 + Q/Q/S 평가 행."""
    meal = make_meal(
        db,
        user_id=user.id,
        eaten_at=datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST),
        stage=stage,
    )
    quantity, quality, satiety = scores
    make_qqs_evaluation(
        db,
        meal_id=meal.id,
        quantity_score=quantity,
        quality_score=quality,
        satiety_score=satiety,
    )
    return meal


def _enough_days(db, user, days: int = 3, *, start: date = START) -> list[date]:
    """start 부터 연속 `days` 일, 하루 한 끼씩 점수 있는 식사를 만든다. 만든 날짜들을 돌려준다."""
    made = [start + timedelta(days=offset) for offset in range(days)]
    for day in made:
        _scored_meal(db, user, day)
    return made


def _rows(db, user_id: uuid.UUID) -> list[LongTermFeedback]:
    # 워커가 upsert(Core INSERT … ON CONFLICT)로 쓸 수 있어 identity map 을 믿지 않는다.
    db.expire_all()
    return list(
        db.execute(
            select(LongTermFeedback)
            .where(LongTermFeedback.user_id == user_id)
            .order_by(LongTermFeedback.period_start)
        ).scalars()
    )


def _only_row(db, user_id: uuid.UUID) -> LongTermFeedback:
    rows = _rows(db, user_id)
    assert len(rows) == 1
    return rows[0]


def _source_ids(db, long_term_feedback_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(LongTermFeedbackSource.daily_feedback_id).where(
                LongTermFeedbackSource.long_term_feedback_id == long_term_feedback_id
            )
        ).scalars()
    )


def _series_dates(ai: FakeAi) -> list[str]:
    return [point["date"] for point in ai.calls[0]["series"]]


# ─────────────────────────── 정상 ───────────────────────────


def test_enough_days_make_one_long_term_feedback(db):
    """L1: 점수 있는 날이 3일이면 행 1개를 만든다 — 기간 · 문장 · 모델 · 안전 상태."""
    user = make_user(db)
    _enough_days(db, user)
    ai = FakeAi(trend_summary="추세 문장", recommendation="제안 문장")

    run(db, _task(user.id), ai)

    row = _only_row(db, user.id)
    assert row.period_type == FeedbackPeriodType.WEEKLY
    assert row.period_start == START
    assert row.period_end == END
    assert row.trend_summary == "추세 문장"
    assert row.recommendation == "제안 문장"
    assert row.model_version == "stub-long-0"
    assert row.safety_status == SafetyStatus.SAFE


def test_ai_request_carries_period_and_user(db):
    """L2: AI 요청에 userId · periodType · periodStart · periodEnd 가 payload 그대로 실린다."""
    user = make_user(db)
    _enough_days(db, user)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert len(ai.calls) == 1
    payload = ai.calls[0]
    assert payload["userId"] == str(user.id)
    assert payload["periodType"] == "WEEKLY"
    assert payload["periodStart"] == "2026-08-15"
    assert payload["periodEnd"] == "2026-08-21"


def test_series_is_daily_average_in_date_order(db):
    """L3: series 는 KST 날짜별 끼니 점수 평균이고 날짜순이다.

    하루 두 끼(60/70/80 · 80/90/100)의 평균이 70/80/90 인지로 본다. 식사를 날짜 역순으로
    만들어 생성 순서를 믿는 구현을 잡는다.
    """
    user = make_user(db)
    _scored_meal(db, user, date(2026, 8, 19), scores=(50, 50, 50))
    _scored_meal(db, user, date(2026, 8, 17), scores=(40, 40, 40))
    _scored_meal(db, user, date(2026, 8, 15), scores=(60, 70, 80), hour=12)
    _scored_meal(db, user, date(2026, 8, 15), scores=(80, 90, 100), hour=19)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls[0]["series"] == [
        {"date": "2026-08-15", "quantity": 70, "quality": 80, "satiety": 90},
        {"date": "2026-08-17", "quantity": 40, "quality": 40, "satiety": 40},
        {"date": "2026-08-19", "quantity": 50, "quality": 50, "satiety": 50},
    ]


def test_series_scores_are_rounded_like_dashboard(db):
    """L4: 날짜 평균은 정수로 반올림한다 — dashboard 와 같은 Python round(은행가 반올림).

    60·61 평균 60.5 → 60, 61·62 평균 61.5 → 62. 대시보드 차트와 AI 가 본 숫자가 같아야 한다.
    """
    user = make_user(db)
    _scored_meal(db, user, START, scores=(60, 61, 60), hour=8)
    _scored_meal(db, user, START, scores=(61, 62, 60), hour=19)
    _enough_days(db, user, 2, start=START + timedelta(days=1))
    ai = FakeAi()

    run(db, _task(user.id), ai)

    first = ai.calls[0]["series"][0]
    assert first == {"date": "2026-08-15", "quantity": 60, "quality": 62, "satiety": 60}


def test_chart_data_stores_the_series_sent_to_ai(db):
    """L5: chart_data 에는 AI 에 보낸 series 를 그대로 남긴다 — 문장이 어떤 수치에서 나왔는지 되짚는다."""
    user = make_user(db)
    _enough_days(db, user)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert _only_row(db, user.id).chart_data == {"series": ai.calls[0]["series"]}


def test_ai_stage_is_the_last_scored_day_stage(db):
    """L6: stage 는 점수 있는 마지막 날의 마지막 식사 스냅샷 단계다.

    기대값(INITIAL)을 factories 기본값(MAINTENANCE)과 다르게 두고, 마지막 날을 생성 순서의
    가운데에 둔다 — 기본값이나 생성 순서의 첫·마지막 행을 고르는 구현을 모두 잡는다.
    """
    user = make_user(db)
    _scored_meal(db, user, date(2026, 8, 15), stage=MedicationStage.MAINTENANCE)
    _scored_meal(db, user, date(2026, 8, 20), stage=MedicationStage.INITIAL)
    _scored_meal(db, user, date(2026, 8, 17), stage=MedicationStage.MAINTENANCE)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls[0]["stage"] == "INITIAL"


def test_daily_summaries_are_safe_daily_feedbacks_in_date_order(db):
    """L7: dailySummaries 는 기간 안 SAFE 하루 피드백 본문이고 날짜순이다. 근거 링크도 그 행들이다."""
    user = make_user(db)
    _enough_days(db, user)
    later = make_daily_feedback(
        db, user_id=user.id, feedback_date=date(2026, 8, 20), summary="20일 요약"
    )
    earlier = make_daily_feedback(
        db, user_id=user.id, feedback_date=date(2026, 8, 16), summary="16일 요약"
    )
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls[0]["dailySummaries"] == ["16일 요약", "20일 요약"]
    row = _only_row(db, user.id)
    assert set(_source_ids(db, row.id)) == {earlier.id, later.id}


def test_no_daily_feedback_still_generates_with_empty_summaries(db):
    """L8: 하루 피드백이 하나도 없어도 점수가 충분하면 만든다 — 하루 피드백은 새로고침해야만 생긴다."""
    user = make_user(db)
    _enough_days(db, user)
    ai = FakeAi()

    result = run(db, _task(user.id), ai)

    assert ai.calls[0]["dailySummaries"] == []
    row = _only_row(db, user.id)
    assert _source_ids(db, row.id) == []
    assert result == {"longTermFeedbackId": str(row.id), "dayCount": 3, "sourceCount": 0}


def test_result_reports_ids_and_counts(db):
    """L9: 반환값(task_queue.result)은 행 id 와 개수만 담는다."""
    user = make_user(db)
    _enough_days(db, user, 4)
    make_daily_feedback(db, user_id=user.id, feedback_date=START)

    result = run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert result == {"longTermFeedbackId": str(row.id), "dayCount": 4, "sourceCount": 1}


def test_ai_payload_is_json_serializable(db):
    """L10: AI payload 는 JSON 으로 나간다 — Decimal · UUID · date 객체가 섞이면 안 된다."""
    user = make_user(db)
    _enough_days(db, user)
    make_daily_feedback(db, user_id=user.id, feedback_date=START)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    json.dumps(ai.calls[0])


# ─────────────────────────── 시계열 거르기 ───────────────────────────


def test_deleted_meal_is_not_in_series(db):
    """L11: 삭제된(soft delete) 식사는 시계열에서 빠진다."""
    user = make_user(db)
    _enough_days(db, user)
    deleted = _scored_meal(db, user, date(2026, 8, 20))
    deleted.deleted_at = datetime.now(UTC)
    db.flush()
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert "2026-08-20" not in _series_dates(ai)


def test_meals_outside_period_are_not_in_series(db):
    """L12: 기간 전날 · 다음날 식사는 시계열에서 빠진다."""
    user = make_user(db)
    _enough_days(db, user)
    _scored_meal(db, user, START - timedelta(days=1))
    _scored_meal(db, user, END + timedelta(days=1))
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert _series_dates(ai) == ["2026-08-15", "2026-08-16", "2026-08-17"]


def test_period_edges_follow_kst_day(db):
    """L13: 기간 경계는 KST 날짜다 — 시작일 00:10 · 종료일 23:50(KST) 식사는 들어간다.

    시작일 00:10 KST 는 UTC 로 전날이고, 종료일 23:50 KST 는 UTC 로 그날 14:50 이다.
    UTC 날짜로 자르면 시작일 식사가 빠진다.
    """
    user = make_user(db)
    _scored_meal(db, user, START, hour=0, minute=10)
    _scored_meal(db, user, date(2026, 8, 18))
    _scored_meal(db, user, END, hour=23, minute=50)
    _scored_meal(db, user, END + timedelta(days=1), hour=0, minute=10)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert _series_dates(ai) == ["2026-08-15", "2026-08-18", "2026-08-21"]


def test_other_users_meals_are_not_mixed(db):
    """L14: 다른 사용자의 식사는 시계열에도, 부족 판정에도 섞이지 않는다."""
    user = make_user(db)
    other = make_user(db, nickname="다른사람")
    _enough_days(db, other, 5)
    _enough_days(db, user)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert len(ai.calls[0]["series"]) == 3


def test_day_with_a_missing_score_is_not_in_series(db):
    """L15: 세 점수 중 하나라도 비어 있는 날은 시계열에서 빠진다 — AI 계약상 세 값이 다 있어야 한다."""
    user = make_user(db)
    _enough_days(db, user)
    _scored_meal(db, user, date(2026, 8, 20), scores=(60, 60, None))
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert "2026-08-20" not in _series_dates(ai)


# ─────────────────────────── 근거 거르기 ───────────────────────────


@pytest.mark.parametrize(
    "safety_status", [SafetyStatus.BLOCKED, SafetyStatus.REVIEW_REQUIRED]
)
def test_unsafe_daily_feedback_is_not_a_source(db, safety_status):
    """L16: SAFE 가 아닌 하루 피드백은 dailySummaries 에도 근거 링크에도 들어가지 않는다."""
    user = make_user(db)
    _enough_days(db, user)
    safe = make_daily_feedback(db, user_id=user.id, feedback_date=START, summary="안전")
    make_daily_feedback(
        db,
        user_id=user.id,
        feedback_date=START + timedelta(days=1),
        summary="숨길 문장",
        safety_status=safety_status,
    )
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls[0]["dailySummaries"] == ["안전"]
    assert _source_ids(db, _only_row(db, user.id).id) == [safe.id]


def test_daily_feedback_without_summary_is_not_a_source(db):
    """L17: 본문이 없는 하루 피드백은 근거가 아니다."""
    user = make_user(db)
    _enough_days(db, user)
    make_daily_feedback(db, user_id=user.id, feedback_date=START, summary=None)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls[0]["dailySummaries"] == []
    assert _source_ids(db, _only_row(db, user.id).id) == []


def test_daily_feedback_outside_period_or_other_user_is_not_a_source(db):
    """L18: 기간 밖(시작 전날 · 종료 다음날) · 다른 사용자의 하루 피드백은 근거가 아니다."""
    user = make_user(db)
    other = make_user(db, nickname="다른사람")
    _enough_days(db, user)
    inside = make_daily_feedback(db, user_id=user.id, feedback_date=END, summary="종료일")
    make_daily_feedback(db, user_id=user.id, feedback_date=START - timedelta(days=1))
    make_daily_feedback(db, user_id=user.id, feedback_date=END + timedelta(days=1))
    make_daily_feedback(db, user_id=other.id, feedback_date=START)
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls[0]["dailySummaries"] == ["종료일"]
    assert _source_ids(db, _only_row(db, user.id).id) == [inside.id]


# ─────────────────────────── 데이터 부족 ───────────────────────────


def test_two_scored_days_skip_ai_and_create_nothing(db):
    """L19: 점수 있는 날이 2일이면 AI 를 부르지 않고 행도 만들지 않는다.

    같은 날 여러 끼는 하루로 센다 — 이틀에 걸친 네 끼여도 부족이다.
    """
    user = make_user(db)
    _scored_meal(db, user, START, hour=8)
    _scored_meal(db, user, START, hour=19)
    _scored_meal(db, user, END, hour=8)
    _scored_meal(db, user, END, hour=19)
    ai = FakeAi()

    result = run(db, _task(user.id), ai)

    assert result is None
    assert ai.calls == []
    assert _rows(db, user.id) == []


def test_day_with_missing_score_does_not_count_toward_enough(db):
    """L20: 점수가 빠진 날은 부족 판정에서도 세지 않는다 — 온전한 날 2일 + 빠진 날 1일은 부족이다."""
    user = make_user(db)
    _enough_days(db, user, 2)
    _scored_meal(db, user, END, scores=(60, None, 60))
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls == []
    assert _rows(db, user.id) == []


def test_insufficient_data_deletes_the_same_period_row(db):
    """L21: 식사가 지워져 부족해지면 같은 (기간 유형 · 시작일) 행을 지운다 — 낡은 추세 문장을 남기지 않는다.

    다른 시작일(어제 만든 주간 행)과 다른 기간 유형(월간 행)은 건드리지 않는다.
    """
    user = make_user(db)
    meals = [_scored_meal(db, user, START + timedelta(days=offset)) for offset in range(3)]
    run(db, _task(user.id), FakeAi())
    yesterday_start = START - timedelta(days=1)
    run(
        db,
        _task(user.id, period_start=yesterday_start, period_end=END - timedelta(days=1)),
        FakeAi(),
    )
    run(
        db,
        _task(user.id, period_type="MONTHLY", period_start=END - timedelta(days=27)),
        FakeAi(),
    )
    assert len(_rows(db, user.id)) == 3

    meals[0].deleted_at = datetime.now(UTC)
    db.flush()
    run(db, _task(user.id), FakeAi())

    remaining = {(row.period_type, row.period_start) for row in _rows(db, user.id)}
    assert remaining == {
        (FeedbackPeriodType.WEEKLY, yesterday_start),
        (FeedbackPeriodType.MONTHLY, END - timedelta(days=27)),
    }


# ─────────────────────────── ALL ───────────────────────────


def test_all_period_has_no_lower_bound(db):
    """L22: ALL 은 하한이 없다 — 몇 해 전 식사도 시계열에 들어간다. 행의 period_start 는 고정 키다."""
    user = make_user(db)
    _scored_meal(db, user, date(2024, 1, 5))
    _enough_days(db, user, 2)
    ai = FakeAi()

    run(db, _task(user.id, period_type="ALL", period_start=ALL_PERIOD_START), ai)

    assert _series_dates(ai)[0] == "2024-01-05"
    assert ai.calls[0]["periodType"] == "ALL"
    row = _only_row(db, user.id)
    assert row.period_type == FeedbackPeriodType.ALL
    assert row.period_start == ALL_PERIOD_START
    assert row.period_end == END


def test_all_period_sends_first_scored_date_to_ai(db):
    """L22b: ALL 이면 AI 에는 점수 있는 첫 날을 periodStart 로 보낸다 — 고정 키(1970-01-01)는 실제 날짜가 아니다.

    고정 키가 넘어가면 모델이 "1970년부터" 같은 문장을 쓸 수 있다. 저장 키는 그대로다.
    """
    user = make_user(db)
    _scored_meal(db, user, date(2024, 1, 5))
    _enough_days(db, user, 2)
    ai = FakeAi()

    run(db, _task(user.id, period_type="ALL", period_start=ALL_PERIOD_START), ai)

    assert ai.calls[0]["periodStart"] == "2024-01-05"
    assert _only_row(db, user.id).period_start == ALL_PERIOD_START


def test_rolling_window_sends_window_start_even_if_first_days_are_empty(db):
    """L22c: WEEKLY 는 첫 며칠에 식사가 없어도 창 시작일을 그대로 보낸다 — "이번 주" 창 자체가 분석 구간이다 (L22b 짝)."""
    user = make_user(db)
    _enough_days(db, user, start=START + timedelta(days=2))
    ai = FakeAi()

    run(db, _task(user.id), ai)

    assert ai.calls[0]["periodStart"] == START.isoformat()


def test_all_period_excludes_meals_after_period_end(db):
    """L23: ALL 도 상한(periodEnd)은 지킨다."""
    user = make_user(db)
    _enough_days(db, user)
    _scored_meal(db, user, END + timedelta(days=1))
    ai = FakeAi()

    run(db, _task(user.id, period_type="ALL", period_start=ALL_PERIOD_START), ai)

    assert _series_dates(ai)[-1] == "2026-08-17"


# ─────────────────────────── 재실행 ───────────────────────────


def test_rerun_keeps_one_row_without_duplicate_sources(db):
    """L24: 같은 작업이 두 번 돌아도 행 1개 · 근거 링크도 쌓이지 않는다. 문장은 새 값으로 덮는다."""
    user = make_user(db)
    _enough_days(db, user)
    make_daily_feedback(db, user_id=user.id, feedback_date=START)

    run(db, _task(user.id), FakeAi(trend_summary="A"))
    run(db, _task(user.id), FakeAi(trend_summary="B"))

    row = _only_row(db, user.id)
    assert row.trend_summary == "B"
    assert len(_source_ids(db, row.id)) == 1


def test_rerun_drops_links_to_sources_no_longer_safe(db):
    """L25: 재실행 때 더는 근거가 아닌 하루 피드백의 링크는 사라진다."""
    user = make_user(db)
    _enough_days(db, user)
    daily = make_daily_feedback(db, user_id=user.id, feedback_date=START)
    run(db, _task(user.id), FakeAi())

    daily.safety_status = SafetyStatus.BLOCKED
    db.flush()
    run(db, _task(user.id), FakeAi())

    assert _source_ids(db, _only_row(db, user.id).id) == []


def test_rerun_keeps_id_and_created_at_and_refreshes_updated_at(db):
    """L26: 재실행해도 같은 행(id · created_at 유지)이고 updated_at 은 새로 찍힌다.

    updated_at 이 GET /insights/long-term 의 generatedAt · stale 판정 기준이다. ON CONFLICT 는
    SQLAlchemy onupdate 를 타지 않으므로 upsert 가 직접 넣어야 한다. 트랜잭션 안에서 now() 가
    고정되므로 첫 실행 뒤 두 시각을 과거로 옮겨 두고 재실행 결과를 본다.
    """
    user = make_user(db)
    _enough_days(db, user)
    run(db, _task(user.id), FakeAi())
    first = _only_row(db, user.id)
    past = datetime(2000, 1, 1, tzinfo=UTC)
    db.execute(
        update(LongTermFeedback)
        .where(LongTermFeedback.id == first.id)
        .values(created_at=past, updated_at=past)
    )
    db.flush()

    run(db, _task(user.id), FakeAi())

    row = _only_row(db, user.id)
    assert row.id == first.id
    assert row.created_at == past
    assert row.updated_at > past


# ─────────────────────────── 안전 · 트랜잭션 · 로그 ───────────────────────────


@pytest.mark.parametrize("status", ["REVIEW_REQUIRED", "BLOCKED"])
def test_ai_safety_status_is_stored_as_is(db, status):
    """L27: AI 가 준 safetyStatus 를 그대로 저장한다 — SAFE 로 올리지 않는다 (규칙 1)."""
    user = make_user(db)
    _enough_days(db, user)

    run(db, _task(user.id), FakeAi(safety_status=status))

    assert _only_row(db, user.id).safety_status == SafetyStatus(status)


def test_worker_does_not_commit(db, monkeypatch):
    """L28: 워커는 커밋하지 않는다 — 큐가 DONE 과 함께 커밋한다."""
    user = make_user(db)
    _enough_days(db, user)
    commits: list[None] = []
    monkeypatch.setattr(db, "commit", lambda: commits.append(None))

    run(db, _task(user.id), FakeAi())

    assert commits == []


def test_ai_failure_propagates_and_leaves_no_row(db):
    """L29: AI 가 실패하면 예외가 그대로 올라가고(큐가 재시도한다) 행은 생기지 않는다."""
    user = make_user(db)
    _enough_days(db, user)

    with pytest.raises(RuntimeError, match="ai down"):
        run(db, _task(user.id), FakeAi(error=RuntimeError("ai down")))

    assert _rows(db, user.id) == []


def test_logs_do_not_contain_feedback_text(db, caplog):
    """L30: 로그에는 식별자와 개수만 — 추세 문장 · 제안 · 하루 요약 본문은 남기지 않는다 (규칙 6)."""
    caplog.set_level(logging.DEBUG)
    user = make_user(db)
    _enough_days(db, user)
    make_daily_feedback(db, user_id=user.id, feedback_date=START, summary="비밀 하루 요약")

    run(db, _task(user.id), FakeAi(trend_summary="비밀 추세", recommendation="비밀 제안"))

    for secret in ("비밀 하루 요약", "비밀 추세", "비밀 제안"):
        assert secret not in caplog.text


def test_dispatch_routes_feedback_long_to_this_job(db):
    """L31: dispatch.handle 이 feedback.long 을 이 워커로 보낸다 — 미구현 오류가 아니다."""
    user = make_user(db)
    _enough_days(db, user)

    result = handle(db, _task(user.id), FakeAi())

    assert result is not None
    assert result["dayCount"] == 3


# ─────────────────────────── 조회 API 와 맞물림 ───────────────────────────
#
# 워커가 남긴 결과(행 · 작업 상태)를 GET /insights/long-term 서비스가 어떻게 읽는지 본다.
# 워커 단위 테스트만으로는 "부족으로 끝났는데 이전 기간 행이 대신 보이는" 조회 경로 결함이 안 드러난다.


def _done_task(
    db,
    user_id: uuid.UUID,
    *,
    period_start: date,
    period_end: date,
    created_at: datetime,
) -> ClaimedTask:
    """큐에 DONE 으로 끝난 feedback.long 작업 행을 남기고, 워커에 넘길 ClaimedTask 를 돌려준다.

    조회 서비스는 created_at 이 가장 늦은 작업을 본다. 한 트랜잭션 안에서는 now() 가 같으므로
    created_at 을 직접 준다.
    """
    task = _task(user_id, period_start=period_start, period_end=period_end)
    db.add(
        Task(
            id=task.id,
            type=task.type,
            payload=task.payload,
            status=TaskStatus.DONE,
            created_at=created_at,
        )
    )
    db.flush()
    return task


def test_insight_shows_generated_feedback_as_fresh(db):
    """I1: 생성 직후 조회는 READY · dataSufficient=true · 문장 노출 · generatedAt=updated_at · stale=false."""
    user = make_user(db)
    _enough_days(db, user)
    task = _done_task(
        db, user.id, period_start=START, period_end=END,
        created_at=datetime(2026, 8, 21, 10, 0, tzinfo=KST),
    )
    run(db, task, FakeAi(trend_summary="추세"))

    insight = get_long_term_insight(db, user_id=user.id, period="7d", today=END)

    row = _only_row(db, user.id)
    assert insight.status == FeedbackStatus.READY
    assert insight.data_sufficient is True
    assert insight.trend_summary == "추세"
    assert insight.generated_at == row.updated_at
    assert insight.stale is False


def test_insight_hides_older_window_row_when_latest_run_was_insufficient(db):
    """I2: 오늘 창이 부족으로 끝나면 어제 창의 행을 대신 보여 주지 않는다 — dataSufficient=false.

    어제 창(8/14–8/20)은 8/14 · 8/16 · 8/17 로 3일이라 행이 생긴다. 오늘 창(8/15–8/21)은
    8/14 가 빠져 2일이라 부족이다. 워커는 오늘 키(8/15) 행만 지우므로 어제 행이 남는다.
    """
    user = make_user(db)
    for day in (date(2026, 8, 14), date(2026, 8, 16), date(2026, 8, 17)):
        _scored_meal(db, user, day)
    yesterday = _done_task(
        db, user.id, period_start=date(2026, 8, 14), period_end=date(2026, 8, 20),
        created_at=datetime(2026, 8, 20, 10, 0, tzinfo=KST),
    )
    run(db, yesterday, FakeAi())
    today = _done_task(
        db, user.id, period_start=START, period_end=END,
        created_at=datetime(2026, 8, 21, 10, 0, tzinfo=KST),
    )
    assert run(db, today, FakeAi()) is None

    insight = get_long_term_insight(db, user_id=user.id, period="7d", today=END)

    assert insight.status == FeedbackStatus.READY
    assert insight.data_sufficient is False
    assert insight.trend_summary is None
    assert insight.recommendation is None
    assert insight.generated_at is None
    assert insight.period.from_ == START
    assert insight.period.to == END


def test_insight_keeps_older_window_row_until_a_newer_run_finishes(db):
    """I3: 오늘 창을 아직 안 돌렸으면 어제 창의 행을 그대로 보여 준다 — I2 의 규칙이 기존 동작을 안 깬다."""
    user = make_user(db)
    for day in (date(2026, 8, 14), date(2026, 8, 16), date(2026, 8, 17)):
        _scored_meal(db, user, day)
    yesterday = _done_task(
        db, user.id, period_start=date(2026, 8, 14), period_end=date(2026, 8, 20),
        created_at=datetime(2026, 8, 20, 10, 0, tzinfo=KST),
    )
    run(db, yesterday, FakeAi(trend_summary="어제 추세"))

    insight = get_long_term_insight(db, user_id=user.id, period="7d", today=END)

    assert insight.data_sufficient is True
    assert insight.trend_summary == "어제 추세"


def test_insight_is_insufficient_when_first_run_had_too_few_days(db):
    """I4: 처음부터 부족하면(행이 한 번도 없음) READY · dataSufficient=false 다."""
    user = make_user(db)
    _enough_days(db, user, 2)
    task = _done_task(
        db, user.id, period_start=START, period_end=END,
        created_at=datetime(2026, 8, 21, 10, 0, tzinfo=KST),
    )
    run(db, task, FakeAi())

    insight = get_long_term_insight(db, user_id=user.id, period="7d", today=END)

    assert insight.status == FeedbackStatus.READY
    assert insight.data_sufficient is False


# ─────────────────────────── 3단계 계약 ───────────────────────────


def test_insufficient_days_mean_no_ai_call(db):
    user = make_user(db)
    ctx = feedback_long.load(db, _task(user.id))

    assert ctx.request is None
    assert feedback_long.call_ai(ctx, FakeAi()) is None
