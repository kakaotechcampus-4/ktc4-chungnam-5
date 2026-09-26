"""insights(장기 피드백) 도메인 로직. DB 세션은 crud 를 통해서만 접근한다."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.crud import insight as insight_crud
from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.infra.queue import enqueue
from app.models.enums import FeedbackPeriodType, SafetyStatus, TaskStatus
from app.models.feedback import LongTermFeedback
from app.models.task import Task
from app.schemas.insights import (
    InsightPeriod,
    InsightRefreshResponse,
    InsightStatus,
    LongTermInsightResponse,
    StaleReason,
)

_KST = ZoneInfo("Asia/Seoul")

_PERIOD_TYPES: dict[str, FeedbackPeriodType] = {
    "7d": FeedbackPeriodType.WEEKLY,
    "28d": FeedbackPeriodType.MONTHLY,
}


def _resolve_period(period: str, today: date) -> tuple[date, date]:
    """"7d"/"28d" 를 [시작일, 종료일] 로 바꾼다. 종료일은 항상 오늘(KST).

    period=all 은 이번 범위에서 지원하지 않는다 — FeedbackPeriodType 에 ALL 이 아직
    없고, 그 값이 의미할 기간(가입일? 첫 식사일?)도 팀 합의가 안 났다.
    """
    if period == "7d":
        return today - timedelta(days=6), today
    if period == "28d":
        return today - timedelta(days=27), today
    raise ValueError(f"알 수 없는 period 입니다: {period!r}")


def _to_kst_range(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """[date_from, date_to] (양끝 포함, 달력 날짜) 를 [start, end) 순간 구간으로 바꾼다."""
    range_start = datetime(date_from.year, date_from.month, date_from.day, tzinfo=_KST)
    range_end = datetime(date_to.year, date_to.month, date_to.day, tzinfo=_KST) + timedelta(days=1)
    return range_start, range_end


def _determine_status(row: LongTermFeedback | None, latest_task: Task | None) -> InsightStatus:
    """행·작업 상태를 조합해 하나의 status 로 만든다.

    우선순위: 대기 중인 작업이 있으면(갱신 중) 낡은 행이 있어도 GENERATING —
    폴링 중인 FE 에게 지금 새로 만드는 중이라는 걸 알려야 한다.
    """
    if latest_task is not None and latest_task.status == TaskStatus.PENDING:
        return InsightStatus.GENERATING
    if row is not None:
        return InsightStatus.READY
    if latest_task is not None and latest_task.status == TaskStatus.FAILED:
        return InsightStatus.FAILED
    if latest_task is not None and latest_task.status == TaskStatus.DONE:
        # 워커가 데이터 부족으로 행을 안 만들고 정상 종료한 경우 (dataSufficient=false).
        return InsightStatus.READY
    return InsightStatus.NOT_GENERATED


def _check_stale(
    db: Session, *, user_id: uuid.UUID, row: LongTermFeedback
) -> tuple[bool, StaleReason | None]:
    """생성 시점(row.created_at) 이후 그 기간 안에서 뭐가 바뀌었는지 확인한다.

    우선순위(삭제 > 항목 수정 > 단계 변경 > 새 식사)는 응답에 미치는 영향이 큰
    순서다 — 삭제는 이미 반영된 데이터 자체가 사라진 것이라 가장 치명적이고,
    새 식사 추가는 "더 볼 게 생겼다" 정도라 가장 가볍다. 여러 개 겹쳐도
    staleReason 은 하나만 보여줄 수 있어 이 순서로 고른다.
    """
    range_start, range_end = _to_kst_range(row.period_start, row.period_end)

    if meal_crud.has_deleted_meals_since(
        db, user_id=user_id, since=row.created_at, range_start=range_start, range_end=range_end
    ):
        return True, StaleReason.MEAL_DELETED

    if meal_crud.has_edited_items_since(
        db, user_id=user_id, since=row.created_at, range_start=range_start, range_end=range_end
    ):
        return True, StaleReason.MEAL_EDITED

    if medication_crud.has_stage_change_since(
        db,
        user_id=user_id,
        since=row.created_at,
        date_from=row.period_start,
        date_to=row.period_end,
    ):
        return True, StaleReason.STAGE_CHANGED

    if meal_crud.has_new_meals_since(
        db, user_id=user_id, since=row.created_at, range_start=range_start, range_end=range_end
    ):
        return True, StaleReason.NEW_MEALS

    return False, None


def get_long_term_insight(
    db: Session, *, user_id: uuid.UUID, period: str, today: date
) -> LongTermInsightResponse:
    period_type = _PERIOD_TYPES[period]  # period 는 endpoint 의 Query pattern 이 먼저 검증한다.
    date_from, date_to = _resolve_period(period, today)

    row = insight_crud.get_latest(db, user_id=user_id, period_type=period_type)
    latest_task = insight_crud.get_latest_refresh_task(db, user_id=user_id, period_type=period_type)
    status = _determine_status(row, latest_task)

    if row is None:
        return LongTermInsightResponse(
            period=InsightPeriod(from_=date_from, to=date_to),
            status=status,
            data_sufficient=False,
            trend_summary=None,
            recommendation=None,
            generated_at=None,
            stale=False,
            stale_reason=None,
        )

    # SAFE 만 노출한다 — REVIEW_REQUIRED(가드레일 전)도 BLOCKED 와 똑같이 숨긴다
    # (services/meal.py::_build_feedback 와 같은 규칙, PR #36 리뷰로 확정됨).
    is_safe = row.safety_status is SafetyStatus.SAFE
    stale, stale_reason = _check_stale(db, user_id=user_id, row=row)

    return LongTermInsightResponse(
        period=InsightPeriod(from_=row.period_start, to=row.period_end),
        status=status,
        data_sufficient=True,
        trend_summary=row.trend_summary if is_safe else None,
        recommendation=row.recommendation if is_safe else None,
        generated_at=row.created_at,
        stale=stale,
        stale_reason=stale_reason,
    )


def refresh_long_term_insight(
    db: Session, *, user_id: uuid.UUID, period: str, today: date
) -> InsightRefreshResponse:
    """`feedback.long` 작업을 큐에 넣는다. 실제 생성은 워커(미구현) 담당이다.

    payload 키(userId/periodType/periodStart/periodEnd)는
    `worker/jobs/feedback_long.py` 가 이미 정해둔 이름 그대로 맞춘다.
    """
    period_type = _PERIOD_TYPES[period]
    date_from, date_to = _resolve_period(period, today)

    enqueue(
        db,
        insight_crud.REFRESH_TASK_TYPE,
        {
            "userId": str(user_id),
            "periodType": period_type.value,
            "periodStart": date_from.isoformat(),
            "periodEnd": date_to.isoformat(),
        },
    )
    db.commit()

    return InsightRefreshResponse(status=InsightStatus.GENERATING)
