"""`services/insight.py` 단위 테스트. DB·네트워크 의존 0."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.crud import insight as insight_crud
from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.models.enums import FeedbackPeriodType, FeedbackStatus, SafetyStatus, TaskStatus
from app.schemas.insights import StaleReason
from app.services import insight as insight_service
from app.services.insight import (
    _check_stale,
    _determine_status,
    _resolve_period,
    _to_kst_range,
    get_long_term_insight,
    refresh_long_term_insight,
)

_KST = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize(
    ("period", "expected_from", "expected_to"),
    [
        ("7d", date(2026, 9, 15), date(2026, 9, 21)),
        ("28d", date(2026, 8, 25), date(2026, 9, 21)),
    ],
)
def test_resolve_period(period, expected_from, expected_to):
    date_from, date_to = _resolve_period(period, today=date(2026, 9, 21))
    assert date_from == expected_from
    assert date_to == expected_to


def test_resolve_period_all_has_no_lower_bound():
    date_from, date_to = _resolve_period("all", today=date(2026, 9, 21))
    assert date_from is None
    assert date_to == date(2026, 9, 21)


def test_resolve_period_rejects_unsupported_values():
    with pytest.raises(ValueError):
        _resolve_period("3d", today=date(2026, 9, 21))


def test_to_kst_range_covers_whole_to_day():
    range_start, range_end = _to_kst_range(date(2026, 9, 15), date(2026, 9, 21))

    assert range_start == datetime(2026, 9, 15, tzinfo=_KST)
    assert range_end == datetime(2026, 9, 22, tzinfo=_KST)  # to 다음날 자정 (배타적 상한)


def _task(status: TaskStatus) -> SimpleNamespace:
    return SimpleNamespace(status=status)


def _feedback_row(**overrides) -> SimpleNamespace:
    defaults = dict(
        period_type=FeedbackPeriodType.WEEKLY,
        period_start=date(2026, 8, 25),
        period_end=date(2026, 9, 21),
        updated_at=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
        trend_summary="요약",
        recommendation="제안",
        safety_status=SafetyStatus.SAFE,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_status_pending_task_wins_even_with_row():
    """갱신 중이면 낡은 행이 있어도 GENERATING — 폴링 중인 FE 에게 알려야 한다."""
    assert _determine_status(_feedback_row(), _task(TaskStatus.PENDING)) == FeedbackStatus.GENERATING


def test_status_ready_when_row_exists():
    assert _determine_status(_feedback_row(), None) == FeedbackStatus.READY


def test_status_ready_when_row_exists_despite_failed_task():
    """행이 있으면(과거에 성공) 이후 재시도가 실패해도 기존 데이터로 READY."""
    assert _determine_status(_feedback_row(), _task(TaskStatus.FAILED)) == FeedbackStatus.READY


def test_status_failed_when_no_row_and_last_task_failed():
    assert _determine_status(None, _task(TaskStatus.FAILED)) == FeedbackStatus.FAILED


def test_status_ready_when_done_without_row():
    """데이터 부족으로 워커가 행 없이 정상 종료한 경우도 READY (dataSufficient=false 로 구분)."""
    assert _determine_status(None, _task(TaskStatus.DONE)) == FeedbackStatus.READY


def test_status_pending_when_nothing_exists():
    assert _determine_status(None, None) == FeedbackStatus.PENDING


def test_check_stale_prioritizes_deletion_over_everything(monkeypatch):
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: True)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: True)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: True)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: True)

    stale, reason = _check_stale(db=None, user_id=uuid.uuid4(), row=_feedback_row(), requested_to=date(2026, 9, 28))

    assert stale is True
    assert reason == StaleReason.MEAL_DELETED


def test_check_stale_falls_back_to_new_meals(monkeypatch):
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: False)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: True)

    stale, reason = _check_stale(db=None, user_id=uuid.uuid4(), row=_feedback_row(), requested_to=date(2026, 9, 28))

    assert stale is True
    assert reason == StaleReason.NEW_MEALS


def test_check_stale_false_when_nothing_changed(monkeypatch):
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: False)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: False)

    stale, reason = _check_stale(db=None, user_id=uuid.uuid4(), row=_feedback_row(), requested_to=date(2026, 9, 28))

    assert stale is False
    assert reason is None


def test_check_stale_extends_range_to_requested_to_not_row_period_end(monkeypatch):
    """행의 period_end(9/21)가 아니라 요청 기준 종료일(9/28)까지 봐야 한다.

    안 그러면 행 생성 다음 날부터 쌓인 식사를 영영 못 잡는다 (PR #46 리뷰).
    """
    captured = {}
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: False)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: False)

    def fake_has_new_meals_since(db, *, user_id, since, range_start, range_end):
        captured["range_end"] = range_end
        return False

    monkeypatch.setattr(meal_crud, "has_new_meals_since", fake_has_new_meals_since)

    _check_stale(
        db=None, user_id=uuid.uuid4(), row=_feedback_row(), requested_to=date(2026, 9, 28)
    )

    assert captured["range_end"] == datetime(2026, 9, 29, tzinfo=_KST)


def test_get_long_term_insight_pending_when_no_row(monkeypatch):
    monkeypatch.setattr(insight_crud, "get_latest", lambda *a, **k: None)
    monkeypatch.setattr(insight_crud, "get_latest_refresh_task", lambda *a, **k: None)

    result = get_long_term_insight(
        db=None, user_id=uuid.uuid4(), period="7d", today=date(2026, 9, 21)
    )

    assert result.status == FeedbackStatus.PENDING
    assert result.data_sufficient is False
    assert result.trend_summary is None
    assert result.period.from_ == date(2026, 9, 15)
    assert result.period.to == date(2026, 9, 21)


def test_get_long_term_insight_hides_content_when_blocked(monkeypatch):
    row = _feedback_row(safety_status=SafetyStatus.BLOCKED)
    monkeypatch.setattr(insight_crud, "get_latest", lambda *a, **k: row)
    monkeypatch.setattr(insight_crud, "get_latest_refresh_task", lambda *a, **k: None)
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: False)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: False)

    result = get_long_term_insight(
        db=None, user_id=uuid.uuid4(), period="7d", today=date(2026, 9, 21)
    )

    assert result.status == FeedbackStatus.READY
    assert result.data_sufficient is True
    assert result.trend_summary is None
    assert result.recommendation is None


def test_get_long_term_insight_hides_content_when_review_required(monkeypatch):
    """가드레일 검사 전(기본값)도 SAFE 가 아니므로 BLOCKED 와 똑같이 숨겨야 한다."""
    row = _feedback_row(safety_status=SafetyStatus.REVIEW_REQUIRED)
    monkeypatch.setattr(insight_crud, "get_latest", lambda *a, **k: row)
    monkeypatch.setattr(insight_crud, "get_latest_refresh_task", lambda *a, **k: None)
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: False)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: False)

    result = get_long_term_insight(
        db=None, user_id=uuid.uuid4(), period="7d", today=date(2026, 9, 21)
    )

    assert result.trend_summary is None
    assert result.recommendation is None


def test_get_long_term_insight_hides_sentinel_period_start_for_all(monkeypatch):
    """ALL 행의 period_start 는 DB 유니크 키용 고정값이지 실제 날짜가 아니므로
    응답에는 null 로 나가야 한다 (PR #46 리뷰)."""
    row = _feedback_row(period_type=FeedbackPeriodType.ALL, period_start=date(1970, 1, 1))
    monkeypatch.setattr(insight_crud, "get_latest", lambda *a, **k: row)
    monkeypatch.setattr(insight_crud, "get_latest_refresh_task", lambda *a, **k: None)
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: False)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: False)

    result = get_long_term_insight(
        db=None, user_id=uuid.uuid4(), period="all", today=date(2026, 9, 21)
    )

    assert result.period.from_ is None


def test_refresh_enqueues_feedback_long_task_and_commits(monkeypatch):
    """payload 키(userId/periodType/periodStart/periodEnd)는 worker/jobs/feedback_long.py
    가 이미 읽기로 정해둔 이름과 맞아야 한다."""
    captured = {}
    monkeypatch.setattr(insight_crud, "get_latest_refresh_task", lambda *a, **k: None)
    monkeypatch.setattr(
        insight_service,
        "enqueue",
        lambda db, task_type, payload: captured.update(task_type=task_type, payload=payload),
    )
    fake_db = MagicMock()
    user_id = uuid.uuid4()

    result = refresh_long_term_insight(
        fake_db, user_id=user_id, period="7d", today=date(2026, 9, 23)
    )

    fake_db.commit.assert_called_once()
    assert result.status == FeedbackStatus.GENERATING
    assert captured["task_type"] == "feedback.long"
    assert captured["payload"] == {
        "userId": str(user_id),
        "periodType": "WEEKLY",
        "periodStart": "2026-09-17",
        "periodEnd": "2026-09-23",
    }


def test_refresh_maps_28d_to_monthly(monkeypatch):
    captured = {}
    monkeypatch.setattr(insight_crud, "get_latest_refresh_task", lambda *a, **k: None)
    monkeypatch.setattr(
        insight_service,
        "enqueue",
        lambda db, task_type, payload: captured.update(payload=payload),
    )

    refresh_long_term_insight(
        MagicMock(), user_id=uuid.uuid4(), period="28d", today=date(2026, 9, 23)
    )

    assert captured["payload"]["periodType"] == "MONTHLY"
    assert captured["payload"]["periodStart"] == "2026-08-27"


def test_refresh_maps_all_to_sentinel_period_start(monkeypatch):
    """period=all 은 하한이 없지만, period_start 는 DB 에서 NOT NULL + UNIQUE 키라
    null 을 그대로 못 넣는다 — ALL_PERIOD_START 고정값으로 채워야 한다 (PR #46 리뷰)."""
    captured = {}
    monkeypatch.setattr(insight_crud, "get_latest_refresh_task", lambda *a, **k: None)
    monkeypatch.setattr(
        insight_service,
        "enqueue",
        lambda db, task_type, payload: captured.update(payload=payload),
    )

    refresh_long_term_insight(
        MagicMock(), user_id=uuid.uuid4(), period="all", today=date(2026, 9, 23)
    )

    assert captured["payload"]["periodType"] == "ALL"
    assert captured["payload"]["periodStart"] == "1970-01-01"
    assert captured["payload"]["periodEnd"] == "2026-09-23"


def test_refresh_skips_enqueue_when_already_pending(monkeypatch):
    """이미 대기 중인 feedback.long 작업이 있으면 새로 넣지 않는다 (PR #46 리뷰).

    안 그러면 새로고침 연타마다 큐에 쌓여 AI 를 중복 호출하고, 여러 워커가 같은
    행을 동시에 upsert 하면서 근거 링크가 꼬일 수 있다.
    """
    monkeypatch.setattr(
        insight_crud, "get_latest_refresh_task", lambda *a, **k: _task(TaskStatus.PENDING)
    )
    enqueue_calls = []
    monkeypatch.setattr(
        insight_service, "enqueue", lambda db, task_type, payload: enqueue_calls.append(payload)
    )
    fake_db = MagicMock()

    result = refresh_long_term_insight(
        fake_db, user_id=uuid.uuid4(), period="7d", today=date(2026, 9, 23)
    )

    assert enqueue_calls == []
    fake_db.commit.assert_not_called()
    assert result.status == FeedbackStatus.GENERATING


def test_refresh_enqueues_when_latest_task_already_done(monkeypatch):
    """마지막 작업이 끝났으면(PENDING 이 아니면) 새로고침 요청을 새로 넣어야 한다."""
    monkeypatch.setattr(
        insight_crud, "get_latest_refresh_task", lambda *a, **k: _task(TaskStatus.DONE)
    )
    enqueue_calls = []
    monkeypatch.setattr(
        insight_service, "enqueue", lambda db, task_type, payload: enqueue_calls.append(payload)
    )

    refresh_long_term_insight(
        MagicMock(), user_id=uuid.uuid4(), period="7d", today=date(2026, 9, 23)
    )

    assert len(enqueue_calls) == 1
