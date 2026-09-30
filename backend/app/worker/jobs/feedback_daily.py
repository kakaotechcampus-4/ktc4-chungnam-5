"""`feedback.daily` — 하루치 끼니를 모아 일일 피드백을 만든다.

`/short-feedback` `scope=DAILY` → `daily_feedbacks` + `daily_feedback_sources`.

**`feedback.meal` 과 타입을 묶지 않는다.** AI 는 같은 엔드포인트를 쓰지만(하는 일이
"Q/Q/S 를 짧은 문장으로 옮긴다" 로 같다) 모으는 데이터도 쓰는 테이블도 다르다.
묶으면 DLQ 에서 "feedback 10건 실패" 밖에 안 보인다.

들어오는 경로가 둘이다 — `POST /insights/daily/refresh` 가 큐에 넣거나,
Worker 의 스케줄 배치가 직접 시작한다. 어느 쪽이든 이 함수가 같은 일을 한다.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `ShortFeedbackRequest` · `ShortFeedbackResponse` 다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import KST
from app.crud import daily_feedback as daily_feedback_crud
from app.crud import meal as meal_crud
from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask
from app.models.enums import SafetyStatus

logger = logging.getLogger("worker.feedback_daily")


def _kst_day_range(day: date) -> tuple[datetime, datetime]:
    """KST 하루 [00:00, 다음날 00:00). eaten_at 은 UTC 로 저장되므로 UTC 날짜로 자르지 않는다."""
    start = datetime.combine(day, time.min, tzinfo=KST)
    return start, start + timedelta(days=1)


def _average(values: list[Decimal]) -> int:
    # 끼니 점수의 평균 → 정수. Python round(은행가 반올림) — services/dashboard.py 와 같은 규칙.
    return round(sum(values) / len(values))


def run(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None:
    """
    순서:
      1. 그날(KST)의 근거(SAFE 끼니 피드백 + Q/Q/S)를 모은다. 하나도 없으면 그날 행을
         지우고 AI 를 부르지 않고 끝낸다 — 삭제된 식사로 만든 낡은 요약을 남기지 않는다
      2. 끼니별 요약과 Q/Q/S 로 `meals` 목록을 만든다
      3. 하루 전체 Q/Q/S 를 집계한다 (끼니 점수의 평균 — 새로 채점하지 않는다)
      4. AI 호출
      5. `daily_feedbacks` upsert + `daily_feedback_sources` 재생성.
         근거는 이 피드백이 어느 끼니에서 나왔는지 되짚는 링크다
    """
    body = task.payload
    user_id = uuid.UUID(body["userId"])
    feedback_date = date.fromisoformat(body["date"])
    range_start, range_end = _kst_day_range(feedback_date)

    evidence = daily_feedback_crud.list_day_evidence(
        db, user_id=user_id, range_start=range_start, range_end=range_end
    )
    if not evidence:
        daily_feedback_crud.delete_for_day(db, user_id=user_id, feedback_date=feedback_date)
        logger.info("일일 피드백 근거 없음 userId=%s date=%s", user_id, feedback_date)
        return None

    # AI payload 는 JSON 으로 나간다 — Decimal·UUID·date 객체를 싣지 않는다.
    meals = [
        {
            "mealType": row.meal_type.value,
            "summary": row.body,
            "qqs": {
                "quantity": float(row.quantity_score),
                "quality": float(row.quality_score),
                "satiety": float(row.satiety_score),
            },
        }
        for row in evidence
    ]
    qqs = {
        "quantity": _average([row.quantity_score for row in evidence]),
        "quality": _average([row.quality_score for row in evidence]),
        "satiety": _average([row.satiety_score for row in evidence]),
    }

    # 그날 마지막 식사 시점의 스냅샷 단계. 근거가 있으면 그날 식사도 있다.
    stages = meal_crud.get_day_stages(
        db, user_id=user_id, range_start=range_start, range_end=range_end
    )
    stage = stages[-1].stage

    result = ai.short_feedback(
        {
            "scope": "DAILY",
            "userId": str(user_id),
            "stage": stage.value,
            "qqs": qqs,
            "date": feedback_date.isoformat(),
            "meals": meals,
        }
    )

    # scope=DAILY 면 suggestions 는 null 이다 — 저장할 곳도 없다.
    # safetyStatus 는 AI 가 준 그대로 저장한다. SAFE 로 올리지 않는다 (규칙 1).
    daily_feedback_id = daily_feedback_crud.upsert(
        db,
        user_id=user_id,
        feedback_date=feedback_date,
        summary=result["body"],
        quantity_score=qqs["quantity"],
        quality_score=qqs["quality"],
        satiety_score=qqs["satiety"],
        model_version=result["modelVersion"],
        safety_status=SafetyStatus(result["safetyStatus"]),
    )
    daily_feedback_crud.replace_sources(
        db,
        daily_feedback_id=daily_feedback_id,
        meal_feedback_ids=[row.meal_feedback_id for row in evidence],
    )

    # 커밋하지 않는다. 이 세션은 큐가 작업을 잠근 트랜잭션이고, 큐가 DONE 과 함께
    # 한 번에 커밋한다. 여기서 터지면 도메인 변경까지 통째로 롤백된다.

    # 로그·반환값(task_queue.result)에는 식별자와 개수만 — 본문·음식명은 남기지 않는다 (규칙 6).
    logger.info(
        "일일 피드백 완료 userId=%s date=%s safetyStatus=%s",
        user_id,
        feedback_date,
        result["safetyStatus"],
    )

    return {"dailyFeedbackId": str(daily_feedback_id), "sourceCount": len(evidence)}
