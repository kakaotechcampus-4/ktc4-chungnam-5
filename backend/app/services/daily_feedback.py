"""하루 피드백(daily_feedbacks) 도메인 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 커밋은 응답을 만들어 return 하기 직전에
여기서 한다. 다른 services/ 모듈을 import 하지 않는다.
실제 생성은 워커(worker/jobs/feedback_daily.py)가 한다 — 여기서는 작업만 등록한다.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import ApiError, ErrorCode
from app.core.time import today_kst
from app.crud import user as user_crud
from app.infra.queue import enqueue
from app.models.enums import FeedbackStatus
from app.schemas.insights import InsightRefreshResponse


def _ensure_user_exists(db: Session, user_id: uuid.UUID) -> None:
    # services/user_state.py 의 _ensure_user_exists 와 같은 모양 — services 끼리 import 하지 않는다.
    if user_crud.get(db, user_id) is None:
        raise ApiError(ErrorCode.USER_NOT_FOUND, "사용자를 찾을 수 없습니다.", 404)


def request_refresh(
    db: Session, *, user_id: uuid.UUID, target_date: date
) -> InsightRefreshResponse:
    """그날(KST) 하루 피드백 재생성 작업(feedback.daily)을 등록하고 202 응답을 만든다.

    근거가 있는지는 여기서 따지지 않는다 — 없으면 워커가 행을 만들지 않고 끝난다 (D6(b)).
    이미 대기 중인 작업이 있어도 새로 넣는다 (D7(a)). 워커가 upsert 라 행은 하나다.
    """
    _ensure_user_exists(db, user_id)

    # "오늘" 은 KST 다 (core/time.today_kst). 명세에 날짜 전용 코드가 없어
    # 값이 잘못된 경우인 VALIDATION_ERROR 로 흡수한다 — 미래 투약 시작일 422 와 같은 판단.
    if target_date > today_kst():
        raise ApiError(
            ErrorCode.VALIDATION_ERROR, "미래 날짜의 하루 피드백은 만들 수 없습니다.", 422
        )

    # payload 키는 워커(jobs/feedback_daily.py)가 읽는 이름과 같아야 한다.
    enqueue(db, "feedback.daily", {"userId": str(user_id), "date": target_date.isoformat()})

    db.commit()

    return InsightRefreshResponse(status=FeedbackStatus.GENERATING)
