"""더미 응답. 전부 고정값이다.

입력을 보고 내용을 바꾸지 않는다 — 되돌려주는 건 `mealId` 뿐이다.
BE 가 응답을 파싱하고 DB 에 넣는 코드를 짜는 데는 이걸로 충분하다.
"""

from __future__ import annotations

from schemas import (
    AnalyzeMealResponse,
    LongFeedbackResponse,
    RecognizedItem,
    SafetyStatus,
    Scope,
    ShortFeedbackResponse,
    Suggestion,
)

BLOCKED_MESSAGE = "약물 관련 판단은 담당 의료진과 상의해 주세요."


def analyze_meal(meal_id: str) -> AnalyzeMealResponse:
    return AnalyzeMealResponse(
        meal_id=meal_id,
        model_version="stub-vision-0",
        safety_status=SafetyStatus.SAFE,
        items=[
            RecognizedItem(
                original_food_name="참치김밥",
                estimated_amount=250,
                unit="g",
                confidence=0.62,
                candidate_food_ref_id="D000123",
                clarify_question="김밥 속재료가 참치가 맞나요?",
            ),
            RecognizedItem(
                original_food_name="삶은 계란",
                estimated_amount=2,
                unit="개",
                confidence=0.96,
                candidate_food_ref_id=None,  # 매칭 실패 — BE 는 matched:false 로 내보낸다
                clarify_question=None,
            ),
        ],
    )


def analyze_meal_blocked(meal_id: str) -> AnalyzeMealResponse:
    return AnalyzeMealResponse(
        meal_id=meal_id,
        model_version="stub-vision-0",
        safety_status=SafetyStatus.BLOCKED,
        items=[],
    )


def short_feedback(scope: Scope) -> ShortFeedbackResponse:
    if scope is Scope.DAILY:
        # daily_feedbacks 에는 summary 컬럼뿐이라 reasoning · suggestions 를 비운다
        return ShortFeedbackResponse(
            body="단백질이 고르게 들어간 하루였어요. 저녁만 포만감이 짧게 끝났어요.",
            model_version="stub-short-0",
            safety_status=SafetyStatus.SAFE,
        )

    return ShortFeedbackResponse(
        body="유지기 기준으로 보면 포만감이 부족한 식사예요.",
        reasoning="지금은 포만감 유지가 중요한데 단백질 비중이 낮았어요.",
        suggestions=[
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
        ],
        model_version="stub-short-0",
        safety_status=SafetyStatus.SAFE,
    )


def short_feedback_blocked() -> ShortFeedbackResponse:
    return ShortFeedbackResponse(
        body=BLOCKED_MESSAGE,
        model_version="stub-short-0",
        safety_status=SafetyStatus.BLOCKED,
    )


def long_feedback() -> LongFeedbackResponse:
    return LongFeedbackResponse(
        trend_summary="이번 기간은 Quality 가 조금씩 오르는 흐름이에요.",
        recommendation="저녁 포만감이 낮게 끝나는 날이 많은 편이에요. "
        "단백질을 이른 끼니에 배치하면 달라지는 경우가 있어요.",
        model_version="stub-long-0",
        safety_status=SafetyStatus.SAFE,
    )


def long_feedback_blocked() -> LongFeedbackResponse:
    return LongFeedbackResponse(
        trend_summary=BLOCKED_MESSAGE,
        recommendation="",
        model_version="stub-long-0",
        safety_status=SafetyStatus.BLOCKED,
    )
