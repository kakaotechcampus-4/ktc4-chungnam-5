"""하루 피드백(daily_feedbacks) API 요청 스키마.

응답(202)은 장기 피드백과 같은 모양이라 schemas/insights.py 의 InsightRefreshResponse 를 쓴다.
"""

from datetime import date

from pydantic import ConfigDict

from app.schemas.base import CamelModel


class DailyFeedbackRefreshRequest(CamelModel):
    """POST /insights/daily/refresh 요청.

    extra="forbid": 오타난 필드(예: dat)를 조용히 무시하지 않는다 —
    schemas/user_state.py UserStateCreateRequest 와 같은 이유.
    """

    model_config = ConfigDict(extra="forbid")

    date: date
