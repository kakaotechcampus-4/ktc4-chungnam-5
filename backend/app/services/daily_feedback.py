"""하루 피드백(daily_feedbacks) 도메인 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 커밋은 응답을 만들어 return 하기 직전에
여기서 한다. 다른 services/ 모듈을 import 하지 않는다.
실제 생성은 워커(worker/jobs/feedback_daily.py)가 한다 — 여기서는 작업만 등록한다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import ApiError, ErrorCode
from app.core.time import KST, today_kst
from app.crud import daily_feedback as daily_feedback_crud
from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.infra.queue import enqueue
from app.models.enums import FeedbackStatus, SafetyStatus, TaskStatus
from app.models.feedback import DailyFeedback
from app.models.task import Task
from app.schemas.daily_feedbacks import DailyFeedbackResponse
from app.schemas.insights import InsightRefreshResponse, StaleReason
from app.schemas.meal import MealScores


def _ensure_user_exists(db: Session, user_id: uuid.UUID) -> None:
    # services/user_state.py 의 _ensure_user_exists 와 같은 모양 — services 끼리 import 하지 않는다.
    if user_crud.get(db, user_id) is None:
        raise ApiError(ErrorCode.USER_NOT_FOUND, "사용자를 찾을 수 없습니다.", 404)


def _ensure_not_future(target_date: date) -> None:
    # "오늘" 은 KST 다 (core/time.today_kst). 명세에 날짜 전용 코드가 없어
    # 값이 잘못된 경우인 VALIDATION_ERROR 로 흡수한다 — 미래 투약 시작일 422 와 같은 판단.
    if target_date > today_kst():
        raise ApiError(
            ErrorCode.VALIDATION_ERROR, "미래 날짜는 요청할 수 없습니다.", 422
        )


def _kst_day_range(day: date) -> tuple[datetime, datetime]:
    """KST 하루 [00:00, 다음날 00:00). eaten_at 은 UTC 로 저장되므로 UTC 날짜로 자르지 않는다."""
    # worker/jobs/feedback_daily.py 의 _kst_day_range 와 같은 규칙 — 워커가 근거를 모은
    # 범위와 stale 검사 범위가 어긋나면 안 된다. services 가 worker 를 import 하지 않도록 사본을 둔다.
    start = datetime.combine(day, time.min, tzinfo=KST)
    return start, start + timedelta(days=1)


def _round_or_none(value: Decimal | None) -> int | None:
    # services/dashboard.py 의 _round_or_none 과 같은 규칙(Python round, 은행가 반올림)
    # — services 끼리 import 하지 않는다.
    return round(value) if value is not None else None


def _determine_status(row: DailyFeedback | None, latest_task: Task | None) -> FeedbackStatus:
    """행·작업 상태를 조합해 하나의 status 로 만든다.

    services/insight.py 의 _determine_status 와 같은 규칙 — services 끼리 import 하지 않는다.
    대기·처리 중인 작업이 있으면(갱신 중) 행이 있어도 GENERATING 이 우선이다.
    행 없이 작업이 DONE 이면 근거가 없어 워커가 행을 안 만든 경우라 READY 다.
    """
    if latest_task is not None and latest_task.status.is_in_flight:
        return FeedbackStatus.GENERATING
    if row is not None:
        return FeedbackStatus.READY
    if latest_task is not None and latest_task.status == TaskStatus.FAILED:
        return FeedbackStatus.FAILED
    if latest_task is not None and latest_task.status == TaskStatus.DONE:
        return FeedbackStatus.READY
    return FeedbackStatus.PENDING


def _check_stale(
    db: Session, *, user_id: uuid.UUID, row: DailyFeedback
) -> tuple[bool, StaleReason | None]:
    """마지막 생성 시점(row.updated_at) 이후 그날 안에서 뭐가 바뀌었는지 확인한다.

    services/insight.py 의 _check_stale 과 같은 규칙을 하루 범위로 옮긴 것이다 —
    services 끼리 import 하지 않는다. 기준은 `created_at` 이 아니라 `updated_at`
    (재생성 때 갱신된다), 우선순위는 삭제 > 항목 수정 > 단계 변경 > 새 식사.
    """
    range_start, range_end = _kst_day_range(row.feedback_date)

    if meal_crud.has_deleted_meals_since(
        db, user_id=user_id, since=row.updated_at, range_start=range_start, range_end=range_end
    ):
        return True, StaleReason.MEAL_DELETED

    if meal_crud.has_edited_items_since(
        db, user_id=user_id, since=row.updated_at, range_start=range_start, range_end=range_end
    ):
        return True, StaleReason.MEAL_EDITED

    if medication_crud.has_stage_change_since(
        db,
        user_id=user_id,
        since=row.updated_at,
        date_from=row.feedback_date,
        date_to=row.feedback_date,
    ):
        return True, StaleReason.STAGE_CHANGED

    if meal_crud.has_new_meals_since(
        db, user_id=user_id, since=row.updated_at, range_start=range_start, range_end=range_end
    ):
        return True, StaleReason.NEW_MEALS

    return False, None


def request_refresh(
    db: Session, *, user_id: uuid.UUID, target_date: date
) -> InsightRefreshResponse:
    """그날(KST) 하루 피드백 재생성 작업(feedback.daily)을 등록하고 202 응답을 만든다.

    근거가 있는지는 여기서 따지지 않는다 — 없으면 워커가 행을 만들지 않고 끝난다 (D6(b)).

    **이 사용자·날짜의 최신 작업이 대기 중이면 새로 넣지 않는다** (장기 피드백 refresh 와
    같은 판단, PR #46 리뷰). 안 그러면 새로고침을 연타할 때마다 큐에 쌓여서 (1) AI 를 여러
    번 불러 비용이 늘고, (2) 워커 여러 대가 같은 `(user_id, feedback_date)` 행을 동시에
    upsert 하면서 근거 링크(`daily_feedback_sources`)가 꼬일 수 있고, (3) 상태 판정이
    가장 최근 작업만 보므로 뒤늦게 등록된 작업이 이미 나온 결과를 GENERATING 으로 가린다.
    """
    _ensure_user_exists(db, user_id)
    _ensure_not_future(target_date)

    latest_task = daily_feedback_crud.get_latest_refresh_task(
        db, user_id=user_id, feedback_date=target_date
    )
    if latest_task is not None and latest_task.status.is_in_flight:
        return InsightRefreshResponse(feedback_status=FeedbackStatus.GENERATING)

    # payload 키는 워커(jobs/feedback_daily.py)가 읽는 이름과 같아야 한다.
    enqueue(
        db,
        daily_feedback_crud.REFRESH_TASK_TYPE,
        {"userId": str(user_id), "date": target_date.isoformat()},
    )

    db.commit()

    return InsightRefreshResponse(feedback_status=FeedbackStatus.GENERATING)


def get_daily_feedback(
    db: Session, *, user_id: uuid.UUID, target_date: date
) -> DailyFeedbackResponse:
    """그날(KST) 하루 피드백을 상태·stale 판정과 함께 돌려준다. 읽기 전용 — 커밋하지 않는다.

    404(없는 사용자)를 422(미래 날짜)보다 먼저 본다 — refresh 와 같은 순서.
    행이 없어도 404 가 아니라 200 + 빈 모양이다 — 폴링 중인 FE 는 상태만 보면 된다.
    """
    _ensure_user_exists(db, user_id)
    _ensure_not_future(target_date)

    row = daily_feedback_crud.get_for_day(db, user_id=user_id, feedback_date=target_date)
    latest_task = daily_feedback_crud.get_latest_refresh_task(
        db, user_id=user_id, feedback_date=target_date
    )
    status = _determine_status(row, latest_task)

    if row is None:
        # 비교할 생성 시각이 없으니 stale 헬퍼를 부르지 않는다 — 폴링마다 쿼리 4개가 나간다.
        return DailyFeedbackResponse(
            daily_feedback_id=None,
            feedback_date=target_date,
            feedback_status=status,
            summary=None,
            scores=None,
            source_meal_ids=[],
            safety_status=None,
            generated_at=None,
            stale=False,
            stale_reason=None,
        )

    stale, stale_reason = _check_stale(db, user_id=user_id, row=row)

    return DailyFeedbackResponse(
        daily_feedback_id=row.id,
        feedback_date=target_date,
        feedback_status=status,
        # SAFE 만 노출한다 — REVIEW_REQUIRED(가드레일 전)도 BLOCKED 와 똑같이 숨긴다
        # (services/insight.py · services/meal.py 와 같은 규칙). 숨기는 건 AI 문장(summary)뿐이고
        # 점수·근거 끼니는 AI 가 쓴 문장이 아니라 그대로 내보낸다.
        summary=row.summary if row.safety_status is SafetyStatus.SAFE else None,
        scores=MealScores(
            quantity=_round_or_none(row.quantity_score),
            quality=_round_or_none(row.quality_score),
            satiety=_round_or_none(row.satiety_score),
        ),
        source_meal_ids=daily_feedback_crud.list_source_meal_ids(
            db, daily_feedback_id=row.id
        ),
        safety_status=row.safety_status,
        # generatedAt 은 created_at 이 아니라 updated_at — 재생성 때 upsert 가 갱신한다.
        generated_at=row.updated_at,
        stale=stale,
        stale_reason=stale_reason,
    )
