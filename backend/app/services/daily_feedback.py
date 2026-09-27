"""하루 피드백(daily_feedbacks) 도메인 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 커밋은 응답을 만들어 return 하기 직전에
여기서 한다. 다른 services/ 모듈을 import 하지 않는다.
실제 생성은 워커(worker/jobs/feedback_daily.py)가 한다 — 여기서는 작업만 등록한다.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.schemas.insights import InsightRefreshResponse


def request_refresh(
    db: Session, *, user_id: uuid.UUID, target_date: date
) -> InsightRefreshResponse:
    """그날(KST) 하루 피드백 재생성 작업(feedback.daily)을 등록하고 202 응답을 만든다."""
    raise NotImplementedError("request_refresh 미구현")
