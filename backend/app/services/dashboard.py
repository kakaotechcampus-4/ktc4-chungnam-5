"""대시보드(dashboard) 도메인 로직. DB 세션은 crud 를 통해서만 접근한다."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.crud import dashboard as dashboard_crud
from app.crud import meal as meal_crud
from sqlalchemy import Row

from app.schemas.dashboard import (
    DashboardPeriod,
    DashboardResponse,
    DashboardSeriesPoint,
    MonthlyAverage,
    StageChange,
    WeightPoint,
)
from app.schemas.meal import MealScores

_KST_ZONE = ZoneInfo("Asia/Seoul")


def _round_or_none(value: Decimal | None) -> int | None:
    return round(value) if value is not None else None


def _resolve_period(period: str, today: date) -> tuple[date | None, date]:
    """"7d"/"28d"/"all" 을 [시작일, 종료일] 로 바꾼다. 종료일은 항상 오늘(KST)."""
    if period == "7d":
        return today - timedelta(days=6), today
    if period == "28d":
        return today - timedelta(days=27), today
    if period == "all":
        return None, today
    raise ValueError(f"알 수 없는 period 입니다: {period!r}")


def _to_kst_range(date_from: date | None, date_to: date) -> tuple[datetime | None, datetime]:
    """[date_from, date_to] (양끝 포함, 달력 날짜) 를 crud 가 쓰는 [start, end) 순간 구간으로 바꾼다."""
    range_start = (
        datetime(date_from.year, date_from.month, date_from.day, tzinfo=_KST_ZONE)
        if date_from is not None
        else None
    )
    # date_to 는 그날 전체를 포함해야 하니, 다음날 자정을 배타적 상한으로 쓴다.
    range_end = datetime(date_to.year, date_to.month, date_to.day, tzinfo=_KST_ZONE) + timedelta(days=1)
    return range_start, range_end


def _compute_stage_changes(records: list[Row], date_from: date | None) -> list[StageChange]:
    """연속된 투약 기록을 순서대로 비교해 stage 가 바뀐 지점만 뽑는다.

    records 는 기간으로 미리 안 잘려있다 — 그래서 기간 시작 직전의 변경도
    "이전 stage" 로 정확히 알 수 있다. 상한(date_to)은 두지 않는다 — 명세가
    예정된(미래) 단계 변경도 포함하도록 정의돼 있다 (예: period.to=8/21 인데
    stageChanges 예시에 8/24 가 나옴). date_from 이전 것만 거른다.
    """
    changes: list[StageChange] = []
    for prev, curr in zip(records, records[1:]):
        if prev.stage == curr.stage:
            continue
        change_date = curr.effective_from
        if date_from is not None and change_date < date_from:
            continue
        changes.append(StageChange(date=change_date, from_=prev.stage, to=curr.stage))
    return changes


def get_dashboard(db: Session, *, user_id: uuid.UUID, period: str) -> DashboardResponse:
    """period 에 해당하는 날짜별 추이 + 기간 평균을 조립한다."""
    today = datetime.now(_KST_ZONE).date()
    date_from, date_to = _resolve_period(period, today)
    range_start, range_end = _to_kst_range(date_from, date_to)

    daily_rows = dashboard_crud.get_daily_scores(
        db, user_id=user_id, range_start=range_start, range_end=range_end
    )
    stage_by_day = {
        row.day.date(): row.stage
        for row in meal_crud.get_day_stages(
            db, user_id=user_id, range_start=range_start, range_end=range_end
        )
    }

    series = [
        DashboardSeriesPoint(
            date=row.day.date(),
            stage=stage_by_day[row.day.date()],
            quantity=_round_or_none(row.avg_quantity),
            quality=_round_or_none(row.avg_quality),
            satiety=_round_or_none(row.avg_satiety),
        )
        for row in daily_rows
    ]

    avg_row = dashboard_crud.get_period_averages(
        db, user_id=user_id, range_start=range_start, range_end=range_end
    )
    averages = MealScores(
        quantity=_round_or_none(avg_row.avg_quantity),
        quality=_round_or_none(avg_row.avg_quality),
        satiety=_round_or_none(avg_row.avg_satiety),
    )

    by_meal_type = {
        row.meal_type: MealScores(
            quantity=_round_or_none(row.avg_quantity),
            quality=_round_or_none(row.avg_quality),
            satiety=_round_or_none(row.avg_satiety),
        )
        for row in dashboard_crud.get_meal_type_averages(
            db, user_id=user_id, range_start=range_start, range_end=range_end
        )
    }

    monthly_averages = [
        MonthlyAverage(
            month=row.month.strftime("%Y-%m"),
            quantity=_round_or_none(row.avg_quantity),
            quality=_round_or_none(row.avg_quality),
            satiety=_round_or_none(row.avg_satiety),
        )
        for row in dashboard_crud.get_monthly_averages(db, user_id=user_id)
    ]

    weight_series = [
        WeightPoint(date=row.day.date(), weight_kg=float(row.weight_kg))
        for row in dashboard_crud.get_weight_series(
            db, user_id=user_id, range_start=range_start, range_end=range_end
        )
    ]

    stage_changes = _compute_stage_changes(
        dashboard_crud.get_medication_records(db, user_id=user_id), date_from
    )

    return DashboardResponse(
        period=DashboardPeriod(type=period, from_=date_from, to=date_to),
        series=series,
        averages=averages,
        by_meal_type=by_meal_type,
        monthly_averages=monthly_averages,
        weight_series=weight_series,
        stage_changes=stage_changes,
    )
