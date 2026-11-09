"""AI Service 진입점.

BE 와는 HTTP 로만 대화한다. 경로·요청·응답 모양은 ai-stub/main.py 와 같다.
컨테이너가 기대하는 것(ai/Dockerfile): 진입점은 이 파일의 `app`, GET /health 가 200.

    uvicorn app.main:app --reload --port 8002
"""

from __future__ import annotations

from typing import Any
import httpx

from fastapi import FastAPI, HTTPException, status

from app.food_analyzer import InvalidVisionResponse, VisionUnavailable, analyze_food
from app.short_feedback import generate_short_feedback
from app.safety_guard import BLOCKED_BODY, is_unsafe
from app.ai_coach import generate_chat, CoachUnavailable, InvalidCoachResponse
from app.schemas import (
    ChatRequest,
    ChatResponse,
    AnalyzeMealRequest,
    AnalyzeMealResponse,
    MealType,
    Scope,
    SafetyStatus,
    ShortFeedbackRequest,
    ShortFeedbackResponse,
)

app = FastAPI(title="AI Service", version="0.1.0")


@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(req: ChatRequest) -> ChatResponse:
    try:
        return await generate_chat(req)
    except CoachUnavailable:
        raise HTTPException(status_code=503, detail="AI coach model is unavailable") from None
    except TimeoutError:
        raise HTTPException(status_code=504, detail="AI coach timed out") from None
    except InvalidCoachResponse:
        raise HTTPException(status_code=502, detail="Invalid AI coach response") from None


# 사람이 읽을 수 있는 라벨일 뿐, 실제 모델 식별자가 아니다 — 실제 모델은
# LLM_VISION_URL(mlapi.run/<UUID>)이 가리키고, 그 UUID는 비밀로 다룬다(config.py).
_VISION_MODEL_VERSION = "gemini-flash-gateway"


def _not_implemented(feature: str) -> HTTPException:
    # 5xx 라 BE 워커는 재시도하다가 마지막 시도에서 FAILED 로 끝낸다.
    return HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"{feature} 는 아직 구현되지 않았다",
    )


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze-meal", response_model=AnalyzeMealResponse, tags=["analyze"])
async def analyze_meal(req: AnalyzeMealRequest) -> AnalyzeMealResponse:
    try:
        items = await analyze_food(req.meal_type.value, req.image_url)
    except VisionUnavailable:
        raise HTTPException(status_code=503, detail="음식 분석 모델을 사용할 수 없습니다") from None
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="음식 분석 요청 시간이 초과되었습니다") from None
    except (httpx.HTTPError, InvalidVisionResponse):
        raise HTTPException(status_code=502, detail="음식 분석 모델 응답을 처리하지 못했습니다") from None
    return AnalyzeMealResponse(
        meal_id=req.meal_id,
        model_version=_VISION_MODEL_VERSION,
        # 음식 인식만 하고 자유 텍스트 조언을 생성하지 않는 엔드포인트라 늘 SAFE다 —
        # safety_guard.py(의료 조언 검사)는 자유 텍스트를 만드는 short/long-feedback용이다.
        safety_status=SafetyStatus.SAFE,
        items=items,
    )


@app.post("/short-feedback", response_model=ShortFeedbackResponse, tags=["feedback"])
async def short_feedback(req: ShortFeedbackRequest) -> ShortFeedbackResponse:
    def blocked() -> ShortFeedbackResponse:
        return ShortFeedbackResponse(body=BLOCKED_BODY, model_version="reasoning-gateway",
                                     safety_status=SafetyStatus.BLOCKED)

    input_texts = [item.display_name for item in req.items]
    for meal in req.previous_meals:
        input_texts.extend(item.display_name for item in meal.items)
    input_texts += [meal.summary for meal in req.meals]
    if req.satiety and req.satiety.user_comment:
        input_texts.append(req.satiety.user_comment)
    if any(is_unsafe(text) for text in input_texts):
        return blocked()
    try:
        draft = await generate_short_feedback(req)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="피드백 모델 응답 시간이 초과되었다") from None
    except (httpx.HTTPError, ValueError):
        # 게이트웨이 URL·키·사용자 기록을 오류 응답에 노출하지 않는다.
        raise HTTPException(status_code=502, detail="피드백 모델 응답을 처리하지 못했다") from None
    output_texts = [draft.body, draft.reasoning or ""]
    for suggestion in draft.suggestions or []:
        output_texts.extend([suggestion.food_name, suggestion.advice])
    if any(is_unsafe(text) for text in output_texts):
        return blocked()
    return ShortFeedbackResponse(
        body=draft.body, reasoning=draft.reasoning,
        suggestions=[] if req.scope is Scope.MEAL and req.meal_type is MealType.DINNER else draft.suggestions,
        model_version="reasoning-gateway", safety_status=SafetyStatus.SAFE,
    )


# TODO: 계약 확정 후 schemas 의 LongFeedbackRequest / Response 로 바꾼다.
@app.post("/long-feedback", tags=["feedback"])
def long_feedback(req: dict[str, Any]) -> dict[str, Any]:
    raise _not_implemented("long-feedback")
