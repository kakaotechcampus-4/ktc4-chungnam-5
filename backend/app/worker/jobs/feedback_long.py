"""`feedback.long` — 여러 날을 모아 장기 추세 피드백을 만든다.

`/long-feedback` → `long_term_feedbacks` + `long_term_feedback_sources`.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `LongFeedbackRequest` · `LongFeedbackResponse` 다.
응답에 `chartData` 는 없다 — AI 는 문장만 쓰고, BE 가 AI 에 보낸 시계열을 `chart_data` 에 남긴다.

들어오는 경로가 둘이다 — `POST /insights/long-term/refresh` 또는 스케줄 배치.
payload(userId · periodType · periodStart · periodEnd)는 `services/insight.py::refresh_long_term_insight`
가 넣는다. `periodType=ALL` 이면 `periodStart` 는 실제 시작일이 아니라 `ALL_PERIOD_START`
(UNIQUE 키 고정값)라 하한 없이 모은다.

순서 (load 1~3 · call_ai 4 · apply 5, 데이터가 모자라면 apply 가 2 의 삭제):
  1. 기간 안 `qqs_evaluations` 를 KST 날짜별 평균으로 모은다 — 세 점수가 다 있는 날만
  2. 점수 있는 날이 `MIN_SCORED_DAYS` 미만이면 같은 키의 행을 지우고 AI 없이 끝낸다
  3. 같은 기간 SAFE `daily_feedbacks` 본문을 모은다 (없어도 진행)
  4. AI 호출
  5. `long_term_feedbacks` upsert + `long_term_feedback_sources` 재생성
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import kst_day_range
from app.crud import dashboard as dashboard_crud
from app.crud import long_term_feedback as long_term_feedback_crud
from app.crud import meal as meal_crud
from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask
from app.models.enums import FeedbackPeriodType, SafetyStatus
from app.worker.job import Job

logger = logging.getLogger("worker.feedback_long")

MIN_SCORED_DAYS = 3
"""추세를 말하려면 점수 있는 날이 최소 이만큼 있어야 한다. 모자라면 AI 를 부르지 않는다.

하루 피드백 수가 아니라 Q/Q/S 가 있는 날 수로 센다 — 하루 피드백은 새로고침해야만 생겨서
비어 있기 쉽다. 이 경우 조회 API 는 "작업 DONE + 행 없음" 을 `dataSufficient=false` 로 보여 준다."""


@dataclass(frozen=True)
class _Ctx:
    """`load` 가 넘기는 값. ORM 객체를 싣지 않는다 — `load` 의 세션은 곧 닫힌다."""

    user_id: uuid.UUID
    period_type: FeedbackPeriodType
    period_start: date
    period_end: date
    day_count: int
    """점수 있는 날 수. 모자랄 때 로그에 남긴다."""
    series: list[dict[str, Any]]
    daily_feedback_ids: list[uuid.UUID]
    request: dict[str, Any] | None
    """None 이면 점수 있는 날이 `MIN_SCORED_DAYS` 미만이다 — AI 를 부르지 않고 `apply` 가 행을 지운다."""


def load(db: Session, task: ClaimedTask) -> _Ctx:
    body = task.payload
    user_id = uuid.UUID(body["userId"])
    period_type = FeedbackPeriodType(body["periodType"])
    period_start = date.fromisoformat(body["periodStart"])
    period_end = date.fromisoformat(body["periodEnd"])

    date_from = None if period_type is FeedbackPeriodType.ALL else period_start
    # [date_from, period_end] 양끝 포함 KST 날짜 → [start, end). eaten_at 은 UTC 로 저장된다.
    range_start = kst_day_range(date_from)[0] if date_from is not None else None
    range_end = kst_day_range(period_end)[1]

    # dashboard 와 같은 집계·반올림 — 차트 숫자와 AI 가 본 숫자가 같아야 한다.
    # 평균이 NULL 인 축이 있는 날은 뺀다. AI 계약(SeriesPoint)은 세 값을 다 요구한다.
    daily_scores = [
        row
        for row in dashboard_crud.get_daily_scores(
            db, user_id=user_id, range_start=range_start, range_end=range_end
        )
        if None not in (row.avg_quantity, row.avg_quality, row.avg_satiety)
    ]

    if len(daily_scores) < MIN_SCORED_DAYS:
        return _Ctx(
            user_id=user_id,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            day_count=len(daily_scores),
            series=[],
            daily_feedback_ids=[],
            request=None,
        )

    # AI payload 는 JSON 으로 나간다 — Decimal·UUID·date 객체를 싣지 않는다.
    series = [
        {
            "date": row.day.date().isoformat(),
            "quantity": round(row.avg_quantity),
            "quality": round(row.avg_quality),
            "satiety": round(row.avg_satiety),
        }
        for row in daily_scores
    ]

    # TODO: ALL 은 series · dailySummaries 에 상한이 없어 오래 쓴 사용자는 요청이 커진다.
    #  허용 최대치를 AI 계약(LongFeedbackRequest)으로 정한 뒤 자른다.
    sources = long_term_feedback_crud.list_period_sources(
        db, user_id=user_id, date_from=date_from, date_to=period_end
    )

    # 점수 있는 마지막 날의 마지막 식사 스냅샷 단계. 그날 식사는 반드시 있다.
    last_start, last_end = kst_day_range(daily_scores[-1].day.date())
    stage = meal_crud.get_day_stages(
        db, user_id=user_id, range_start=last_start, range_end=last_end
    )[-1].stage

    # ALL 의 period_start 는 UNIQUE 키 고정값(1970-01-01)이지 실제 날짜가 아니다. 그대로 보내면
    # 모델이 문장에 그 날짜를 쓸 수 있어, AI 에는 실제 분석 시작일(점수 있는 첫 날)을 보낸다.
    # WEEKLY · MONTHLY 는 창 자체가 분석 구간이라 첫 며칠이 비어도 창 시작일을 보낸다.
    ai_period_start = series[0]["date"] if date_from is None else period_start.isoformat()

    return _Ctx(
        user_id=user_id,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        day_count=len(daily_scores),
        series=series,
        daily_feedback_ids=[row.daily_feedback_id for row in sources],
        request={
            "userId": str(user_id),
            "periodType": period_type.value,
            "periodStart": ai_period_start,
            "periodEnd": period_end.isoformat(),
            "stage": stage.value,
            "series": series,
            "dailySummaries": [row.summary for row in sources],
        },
    )


def call_ai(ctx: _Ctx, ai: AiClient) -> dict[str, Any] | None:
    if ctx.request is None:
        return None
    return ai.long_feedback(ctx.request)


def apply(db: Session, task: ClaimedTask, ctx: _Ctx, result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        long_term_feedback_crud.delete_for_period(
            db, user_id=ctx.user_id, period_type=ctx.period_type, period_start=ctx.period_start
        )
        logger.info(
            "장기 피드백 데이터 부족 userId=%s periodType=%s periodStart=%s dayCount=%s",
            ctx.user_id,
            ctx.period_type.value,
            ctx.period_start,
            ctx.day_count,
        )
        return None

    # safetyStatus 는 AI 가 준 그대로 저장한다. SAFE 로 올리지 않는다 (규칙 1).
    long_term_feedback_id = long_term_feedback_crud.upsert(
        db,
        user_id=ctx.user_id,
        period_type=ctx.period_type,
        period_start=ctx.period_start,
        period_end=ctx.period_end,
        trend_summary=result["trendSummary"],
        recommendation=result["recommendation"],
        chart_data={"series": ctx.series},
        model_version=result["modelVersion"],
        safety_status=SafetyStatus(result["safetyStatus"]),
    )
    long_term_feedback_crud.replace_sources(
        db,
        long_term_feedback_id=long_term_feedback_id,
        daily_feedback_ids=ctx.daily_feedback_ids,
    )

    # 커밋하지 않는다. 큐가 DONE 과 함께 한 번에 커밋한다(`queue.complete`).

    # 로그·반환값(task_queue.result)에는 식별자와 개수만 — 문장은 남기지 않는다 (규칙 6).
    logger.info(
        "장기 피드백 완료 userId=%s periodType=%s safetyStatus=%s",
        ctx.user_id,
        ctx.period_type.value,
        result["safetyStatus"],
    )

    return {
        "longTermFeedbackId": str(long_term_feedback_id),
        "dayCount": len(ctx.series),
        "sourceCount": len(ctx.daily_feedback_ids),
    }


JOB = Job(load=load, call_ai=call_ai, apply=apply)
