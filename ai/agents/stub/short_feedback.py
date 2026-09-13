"""/short-feedback 더미 응답 생성기 (scope=MEAL · DAILY).

문구는 전부 서술형이다. "이만큼 드세요" 같은 명령형 처방 톤을 쓰지 않는다.
"""

from __future__ import annotations

from agents.schemas import (
    SafetyStatus,
    Scope,
    ShortFeedbackRequest,
    ShortFeedbackResponse,
    Stage,
    Suggestion,
)
from app.config import Scenario
from guardrail.medical import BLOCKED_MESSAGE

MODEL_VERSION = "stub-short-0"

_STAGE_LABEL = {
    Stage.PRE_DOSE: "시작 전",
    Stage.INITIAL: "초기",
    Stage.TITRATION: "적응기",
    Stage.MAINTENANCE: "유지기",
}

# 가장 낮은 축에 따라 다른 문장을 고른다. 더미지만 입력에 반응하는 편이 개발할 때 낫다.
_WEAKEST_REASON = {
    "quantity": "평소 한 끼보다 양이 많았던 편이에요.",
    "quality": "탄수화물 비중이 높고 단백질이 적은 구성이었어요.",
    "satiety": "단백질 비중이 낮은 편이라 포만감이 오래 가지 않았어요.",
}

_SUGGESTIONS = (
    Suggestion(
        food_name="두부 반 모",
        advice="단백질을 조금 더 채우는 쪽이에요",
        candidate_food_ref_id="D004512",
    ),
    Suggestion(
        food_name="나물 한 접시",
        advice="식이섬유가 포만감을 늘리는 데 도움이 돼요",
        candidate_food_ref_id=None,
    ),
)


def _weakest_axis(req: ShortFeedbackRequest) -> str:
    scores = {
        "quantity": req.qqs.quantity,
        "quality": req.qqs.quality,
        "satiety": req.qqs.satiety,
    }
    return min(scores, key=lambda axis: scores[axis])


def build(req: ShortFeedbackRequest, scenario: Scenario) -> ShortFeedbackResponse:
    label = _STAGE_LABEL[req.stage]
    axis = _weakest_axis(req)

    if req.scope is Scope.DAILY:
        # daily_feedbacks 에는 summary 컬럼뿐이라 reasoning · suggestions 를 비운다.
        return ShortFeedbackResponse(
            body=f"{label} 기준으로 {len(req.meals)}끼를 기록한 하루였어요. {_WEAKEST_REASON[axis]}",
            model_version=MODEL_VERSION,
            safety_status=SafetyStatus.SAFE,
            reasoning=None,
            suggestions=None,
        )

    return ShortFeedbackResponse(
        body=f"{label} 기준으로 보면 포만감이 조금 짧게 끝난 식사예요.",
        model_version=MODEL_VERSION,
        safety_status=SafetyStatus.SAFE,
        reasoning=_WEAKEST_REASON[axis],
        suggestions=list(_SUGGESTIONS),
    )


def blocked(req: ShortFeedbackRequest) -> ShortFeedbackResponse:
    return ShortFeedbackResponse(
        body=BLOCKED_MESSAGE,
        model_version=MODEL_VERSION,
        safety_status=SafetyStatus.BLOCKED,
        reasoning=None,
        suggestions=None,
    )


__all__ = ["MODEL_VERSION", "blocked", "build"]
