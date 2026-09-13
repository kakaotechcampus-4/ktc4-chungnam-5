"""/long-feedback 더미 응답 생성기.

chartData 는 여기서 만들지 않는다 — BE 가 qqs_evaluations 를 집계해 채운다 (S6).
"""

from __future__ import annotations

from agents.schemas import (
    LongFeedbackRequest,
    LongFeedbackResponse,
    PeriodType,
    SafetyStatus,
    Stage,
)
from app.config import Scenario
from guardrail.medical import BLOCKED_MESSAGE

MODEL_VERSION = "stub-long-0"

_PERIOD_LABEL = {
    PeriodType.WEEKLY: "이번 주는",
    PeriodType.MONTHLY: "이번 달은",
    PeriodType.ALL: "기록 전체를 보면",
}

_STAGE_LABEL = {
    Stage.PRE_DOSE: "시작 전",
    Stage.INITIAL: "초기",
    Stage.TITRATION: "적응기",
    Stage.MAINTENANCE: "유지기",
}


def _trend(req: LongFeedbackRequest) -> str:
    """series 의 앞뒤를 비교해 방향만 말한다. 더미지만 숫자와 어긋나지는 않게."""
    if len(req.series) < 2:
        return "아직 흐름을 말하기에는 기록이 짧아요."

    first, last = req.series[0], req.series[-1]
    delta = last.quality - first.quality
    if delta > 1:
        return "Quality 가 조금씩 오르는 흐름이에요."
    if delta < -1:
        return "Quality 가 조금씩 내려가는 흐름이에요."
    return "Quality 는 큰 변화 없이 비슷하게 유지되고 있어요."


def build(req: LongFeedbackRequest, scenario: Scenario) -> LongFeedbackResponse:
    period = _PERIOD_LABEL[req.period_type]
    stage = _STAGE_LABEL[req.stage]

    return LongFeedbackResponse(
        trend_summary=f"{period} {_trend(req)}",
        recommendation=(
            f"{stage}에는 저녁 포만감이 낮게 끝나는 날이 많은 편이에요. "
            "단백질을 이른 끼니에 배치하면 달라지는 경우가 있어요."
        ),
        model_version=MODEL_VERSION,
        safety_status=SafetyStatus.SAFE,
    )


def blocked(req: LongFeedbackRequest) -> LongFeedbackResponse:
    return LongFeedbackResponse(
        trend_summary=BLOCKED_MESSAGE,
        recommendation="",
        model_version=MODEL_VERSION,
        safety_status=SafetyStatus.BLOCKED,
    )


__all__ = ["MODEL_VERSION", "blocked", "build"]
