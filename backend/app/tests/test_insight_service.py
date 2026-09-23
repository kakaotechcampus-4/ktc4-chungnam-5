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
from app.models.enums import SafetyStatus, TaskStatus
from app.schemas.insights import InsightStatus, StaleReason
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


@pytest.mark.parametrize("period", ["all", "3d"])
def test_resolve_period_rejects_unsupported_values(period):
    """all 은 이번 범위에서 미지원 — FeedbackPeriodType 에 ALL 이 아직 없어서."""
    with pytest.raises(ValueError):
        _resolve_period(period, today=date(2026, 9, 21))


def test_to_kst_range_covers_whole_to_day():
    range_start, range_end = _to_kst_range(date(2026, 9, 15), date(2026, 9, 21))

    assert range_start == datetime(2026, 9, 15, tzinfo=_KST)
    assert range_end == datetime(2026, 9, 22, tzinfo=_KST)  # to 다음날 자정 (배타적 상한)


def _task(status: TaskStatus) -> SimpleNamespace:
    return SimpleNamespace(status=status)


def _feedback_row(**overrides) -> SimpleNamespace:
    defaults = dict(
        period_start=date(2026, 8, 25),
        period_end=date(2026, 9, 21),
        created_at=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
        trend_summary="요약",
        recommendation="제안",
        safety_status=SafetyStatus.SAFE,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_status_pending_task_wins_even_with_row():
    """갱신 중이면 낡은 행이 있어도 GENERATING — 폴링 중인 FE 에게 알려야 한다."""
    assert _determine_status(_feedback_row(), _task(TaskStatus.PENDING)) == InsightStatus.GENERATING


def test_status_ready_when_row_exists():
    assert _determine_status(_feedback_row(), None) == InsightStatus.READY


def test_status_ready_when_row_exists_despite_failed_task():
    """행이 있으면(과거에 성공) 이후 재시도가 실패해도 기존 데이터로 READY."""
    assert _determine_status(_feedback_row(), _task(TaskStatus.FAILED)) == InsightStatus.READY


def test_status_failed_when_no_row_and_last_task_failed():
    assert _determine_status(None, _task(TaskStatus.FAILED)) == InsightStatus.FAILED


def test_status_ready_when_done_without_row():
    """데이터 부족으로 워커가 행 없이 정상 종료한 경우도 READY (dataSufficient=false 로 구분)."""
    assert _determine_status(None, _task(TaskStatus.DONE)) == InsightStatus.READY


def test_status_not_generated_when_nothing_exists():
    assert _determine_status(None, None) == InsightStatus.NOT_GENERATED


def test_check_stale_prioritizes_deletion_over_everything(monkeypatch):
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: True)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: True)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: True)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: True)

    stale, reason = _check_stale(db=None, user_id=uuid.uuid4(), row=_feedback_row())

    assert stale is True
    assert reason == StaleReason.MEAL_DELETED


def test_check_stale_falls_back_to_new_meals(monkeypatch):
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: False)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: True)

    stale, reason = _check_stale(db=None, user_id=uuid.uuid4(), row=_feedback_row())

    assert stale is True
    assert reason == StaleReason.NEW_MEALS


def test_check_stale_false_when_nothing_changed(monkeypatch):
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: False)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: False)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: False)

    stale, reason = _check_stale(db=None, user_id=uuid.uuid4(), row=_feedback_row())

    assert stale is False
    assert reason is None


def test_get_long_term_insight_not_generated_when_no_row(monkeypatch):
    monkeypatch.setattr(insight_crud, "get_latest", lambda *a, **k: None)
    monkeypatch.setattr(insight_crud, "get_latest_refresh_task", lambda *a, **k: None)

    result = get_long_term_insight(
        db=None, user_id=uuid.uuid4(), period="7d", today=date(2026, 9, 21)
    )

    assert result.status == InsightStatus.NOT_GENERATED
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

    assert result.status == InsightStatus.READY
    assert result.data_sufficient is True
    assert result.trend_summary is None
    assert result.recommendation is None


def test_refresh_enqueues_feedback_long_task_and_commits(monkeypatch):
    """payload 키(userId/periodType/periodStart/periodEnd)는 worker/jobs/feedback_long.py
    가 이미 읽기로 정해둔 이름과 맞아야 한다."""
    captured = {}
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
    assert result.status == InsightStatus.GENERATING
    assert captured["task_type"] == "feedback.long"
    assert captured["payload"] == {
        "userId": str(user_id),
        "periodType": "WEEKLY",
        "periodStart": "2026-09-17",
        "periodEnd": "2026-09-23",
    }


def test_refresh_maps_28d_to_monthly(monkeypatch):
    captured = {}
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
