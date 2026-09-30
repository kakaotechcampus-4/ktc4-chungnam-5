"""`services/daily_feedback.py::get_daily_feedback` 단위 테스트. DB·네트워크 의존 0.

서비스는 crud 를 모듈 경유(`daily_feedback_crud.x` · `meal_crud.x` · `medication_crud.x` ·
`user_crud.get`)로 부른다 — 그래서 모듈 속성을 monkeypatch 해 stale 헬퍼의 우선순위와
넘기는 인자를 DB 없이 본다. 실제 쿼리 결과는 `test_daily_feedback_get_api.py` 가 본다.
테스트 번호(#n)는 계획 `.claude/tdd/current/plan.md` §6 명세표의 행 번호다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.time import KST
from app.crud import daily_feedback as daily_feedback_crud
from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.models.enums import SafetyStatus
from app.schemas.insights import StaleReason
from app.services.daily_feedback import get_daily_feedback

D = date(2026, 8, 21)
UPDATED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)  # = 21:00 KST

_MEAL_HELPERS = ("has_deleted_meals_since", "has_edited_items_since", "has_new_meals_since")


# ─────────────────────────── helper ───────────────────────────


def _row(**overrides) -> SimpleNamespace:
    """daily_feedbacks 행 대역. 서비스가 읽는 속성만 둔다."""
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        feedback_date=D,
        summary="요약",
        quantity_score=Decimal("74.00"),
        quality_score=Decimal("81.00"),
        satiety_score=Decimal("65.00"),
        model_version="stub-daily-0",
        safety_status=SafetyStatus.SAFE,
        created_at=datetime(2026, 8, 21, 11, 0, tzinfo=UTC),
        updated_at=UPDATED_AT,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """KST 오늘을 D 로 고정한다 — 미래 날짜 검사가 실제 시계에 흔들리지 않게."""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("app.core.time.datetime", _FrozenDatetime)


@pytest.fixture
def crud_with_row(monkeypatch):
    """사용자 있음 · 그날 행 있음 · 작업 없음 · 근거 없음. 행을 돌려준다."""
    row = _row()
    monkeypatch.setattr(user_crud, "get", lambda *a, **k: SimpleNamespace(id=row.user_id))
    monkeypatch.setattr(daily_feedback_crud, "get_for_day", lambda *a, **k: row)
    monkeypatch.setattr(daily_feedback_crud, "get_latest_refresh_task", lambda *a, **k: None)
    monkeypatch.setattr(daily_feedback_crud, "list_source_meal_ids", lambda *a, **k: [])
    return row


def _set_stale_helpers(monkeypatch, *, deleted: bool, edited: bool, stage: bool, new: bool) -> None:
    monkeypatch.setattr(meal_crud, "has_deleted_meals_since", lambda *a, **k: deleted)
    monkeypatch.setattr(meal_crud, "has_edited_items_since", lambda *a, **k: edited)
    monkeypatch.setattr(medication_crud, "has_stage_change_since", lambda *a, **k: stage)
    monkeypatch.setattr(meal_crud, "has_new_meals_since", lambda *a, **k: new)


def _call(row) -> object:
    return get_daily_feedback(None, user_id=row.user_id, target_date=D)


# ─────────────────────────── stale 우선순위 ───────────────────────────


def test_24_all_changes_overlap_deleted_wins(monkeypatch, crud_with_row):
    """#24: 삭제·수정·단계 변경·새 식사가 모두 겹치면 MEAL_DELETED 다.

    staleReason 은 하나만 보여줄 수 있다 — 이미 반영된 데이터가 사라진 삭제가 가장 치명적이다.
    """
    _set_stale_helpers(monkeypatch, deleted=True, edited=True, stage=True, new=True)

    result = _call(crud_with_row)

    assert result.stale is True
    assert result.stale_reason == StaleReason.MEAL_DELETED


def test_25_without_deletion_edited_wins(monkeypatch, crud_with_row):
    """#25: 삭제가 없으면 수정이 단계 변경·새 식사보다 우선한다 → MEAL_EDITED."""
    _set_stale_helpers(monkeypatch, deleted=False, edited=True, stage=True, new=True)

    result = _call(crud_with_row)

    assert result.stale is True
    assert result.stale_reason == StaleReason.MEAL_EDITED


def test_26_without_deletion_or_edit_stage_wins(monkeypatch, crud_with_row):
    """#26: 삭제·수정이 없으면 단계 변경이 새 식사보다 우선한다 → STAGE_CHANGED."""
    _set_stale_helpers(monkeypatch, deleted=False, edited=False, stage=True, new=True)

    result = _call(crud_with_row)

    assert result.stale is True
    assert result.stale_reason == StaleReason.STAGE_CHANGED


# ─────────────────────────── stale 헬퍼 인자 ───────────────────────────


def test_27_stale_helpers_get_updated_at_and_kst_day_range(monkeypatch, crud_with_row):
    """#27: stale 헬퍼는 since=row.updated_at, 범위는 KST [D 00:00, D+1 00:00), 투약은 date_from=date_to=D 로 불린다.

    created_at 을 기준으로 하면 재생성 뒤에도 이미 반영된 변화가 계속 낡음으로 나온다.
    UTC 날짜로 자르면 KST 새벽 식사를 전날로 보낸다.
    """
    captured: dict[str, dict] = {}

    def _capture(name):
        def _fake(*args, **kwargs):
            captured[name] = kwargs
            return False

        return _fake

    for name in _MEAL_HELPERS:
        monkeypatch.setattr(meal_crud, name, _capture(name))
    monkeypatch.setattr(
        medication_crud, "has_stage_change_since", _capture("has_stage_change_since")
    )

    result = _call(crud_with_row)

    assert result.stale is False
    assert set(captured) == {*_MEAL_HELPERS, "has_stage_change_since"}
    for name in _MEAL_HELPERS:
        kwargs = captured[name]
        assert kwargs["user_id"] == crud_with_row.user_id
        assert kwargs["since"] == UPDATED_AT
        assert kwargs["range_start"] == datetime(2026, 8, 21, tzinfo=KST)
        assert kwargs["range_end"] == datetime(2026, 8, 22, tzinfo=KST)
    stage_kwargs = captured["has_stage_change_since"]
    assert stage_kwargs["user_id"] == crud_with_row.user_id
    assert stage_kwargs["since"] == UPDATED_AT
    assert stage_kwargs["date_from"] == D
    assert stage_kwargs["date_to"] == D


# ─────────────────────────── 행 없음 ───────────────────────────


def test_28_no_row_does_not_call_stale_helpers(monkeypatch):
    """#28: 그날 행이 없으면 stale 헬퍼를 부르지 않고 stale=False 다.

    비교할 생성 시각이 없다 — 부르면 쓸데없는 쿼리 4개가 폴링마다 나간다.
    """
    user_id = uuid.uuid4()
    monkeypatch.setattr(user_crud, "get", lambda *a, **k: SimpleNamespace(id=user_id))
    monkeypatch.setattr(daily_feedback_crud, "get_for_day", lambda *a, **k: None)
    monkeypatch.setattr(daily_feedback_crud, "get_latest_refresh_task", lambda *a, **k: None)
    monkeypatch.setattr(daily_feedback_crud, "list_source_meal_ids", lambda *a, **k: [])
    calls: list[str] = []

    def _record(name):
        def _fake(*args, **kwargs):
            calls.append(name)
            return True

        return _fake

    for name in _MEAL_HELPERS:
        monkeypatch.setattr(meal_crud, name, _record(name))
    monkeypatch.setattr(
        medication_crud, "has_stage_change_since", _record("has_stage_change_since")
    )

    result = get_daily_feedback(None, user_id=user_id, target_date=D)

    assert calls == []
    assert result.stale is False
    assert result.stale_reason is None
