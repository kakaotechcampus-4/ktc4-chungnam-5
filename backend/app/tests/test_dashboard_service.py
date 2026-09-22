"""`services/dashboard.py` 단위 테스트. DB·네트워크 의존 0."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import MedicationStage
from app.services.dashboard import (
    _compute_stage_changes,
    _monthly_range,
    _resolve_period,
    _shift_month,
    _to_kst_range,
)

_KST = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize(
    ("period", "expected_from", "expected_to"),
    [
        ("7d", date(2026, 9, 15), date(2026, 9, 21)),
        ("28d", date(2026, 8, 25), date(2026, 9, 21)),
        ("all", None, date(2026, 9, 21)),
    ],
)
def test_resolve_period(period, expected_from, expected_to):
    date_from, date_to = _resolve_period(period, today=date(2026, 9, 21))
    assert date_from == expected_from
    assert date_to == expected_to


def test_resolve_period_rejects_unknown_value():
    with pytest.raises(ValueError):
        _resolve_period("3d", today=date(2026, 9, 21))


@pytest.mark.parametrize(
    ("start", "delta", "expected"),
    [
        (date(2026, 9, 15), 0, date(2026, 9, 1)),
        (date(2026, 9, 15), -1, date(2026, 8, 1)),
        (date(2026, 1, 15), -1, date(2025, 12, 1)),  # 연도 넘어감 (역방향)
        (date(2026, 12, 15), 1, date(2027, 1, 1)),  # 연도 넘어감 (정방향)
    ],
)
def test_shift_month(start, delta, expected):
    assert _shift_month(start, delta) == expected


def test_monthly_range_covers_last_n_months_including_current():
    start, end = _monthly_range(today=date(2026, 9, 21), month_count=2)

    assert start == datetime(2026, 8, 1, tzinfo=_KST)
    assert end == datetime(2026, 10, 1, tzinfo=_KST)


def test_to_kst_range_all_period_has_no_lower_bound():
    range_start, range_end = _to_kst_range(None, date(2026, 9, 21))

    assert range_start is None
    assert range_end == datetime(2026, 9, 22, tzinfo=_KST)


def test_to_kst_range_covers_whole_to_day():
    range_start, range_end = _to_kst_range(date(2026, 9, 15), date(2026, 9, 21))

    assert range_start == datetime(2026, 9, 15, tzinfo=_KST)
    assert range_end == datetime(2026, 9, 22, tzinfo=_KST)  # to 다음날 자정 (배타적 상한)


def _record(stage: MedicationStage, effective_from: date):
    return SimpleNamespace(stage=stage, effective_from=effective_from)


def test_compute_stage_changes_ignores_repeated_stage():
    records = [
        _record(MedicationStage.INITIAL, date(2026, 8, 1)),
        _record(MedicationStage.INITIAL, date(2026, 8, 15)),  # 갱신이지 변경 아님
    ]
    assert _compute_stage_changes(records, date(2026, 8, 1)) == []


def test_compute_stage_changes_detects_transition_within_period():
    records = [
        _record(MedicationStage.INITIAL, date(2026, 8, 1)),
        _record(MedicationStage.TITRATION, date(2026, 9, 16)),
    ]
    changes = _compute_stage_changes(records, date(2026, 8, 25))

    assert len(changes) == 1
    assert changes[0].date == date(2026, 9, 16)
    assert changes[0].from_ == MedicationStage.INITIAL
    assert changes[0].to == MedicationStage.TITRATION


def test_compute_stage_changes_excludes_transition_before_period():
    """변경 시점 자체가 기간보다 앞이면, 그 변경은 결과 목록에 안 나와야 한다."""
    records = [
        _record(MedicationStage.INITIAL, date(2026, 7, 1)),
        _record(MedicationStage.TITRATION, date(2026, 8, 1)),  # 기간(8/25~) 이전 변경
    ]
    changes = _compute_stage_changes(records, date(2026, 8, 25))

    assert changes == []


def test_compute_stage_changes_includes_future_scheduled_change():
    """명세가 예정된(미래) 단계 변경도 포함하도록 정의돼 있다 — 상한을 두지 않는다.

    (period.to=8/21 인 예시에 8/24 짜리 stageChanges 항목이 나오는 게 근거)
    """
    records = [
        _record(MedicationStage.INITIAL, date(2026, 8, 1)),
        _record(MedicationStage.TITRATION, date(2026, 9, 25)),  # period.to(9/21) 이후 예정된 변경
    ]
    changes = _compute_stage_changes(records, date(2026, 8, 25))

    assert len(changes) == 1
    assert changes[0].date == date(2026, 9, 25)
