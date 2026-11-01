"""AI Service 진입점.

BE 와는 HTTP 로만 대화한다. 경로·요청·응답 모양은 ai-stub/main.py 와 같다.
컨테이너가 기대하는 것(ai/Dockerfile): 진입점은 이 파일의 `app`, GET /health 가 200.

    uvicorn app.main:app --reload --port 8002
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, status

from app.food_analyzer import analyze_food
from app.ai_coach import generate_chat, CoachUnavailable, InvalidCoachResponse
from app.schemas import (
    ChatRequest,
    ChatResponse,
    AnalyzeMealRequest,
    AnalyzeMealResponse,
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
    items = await analyze_food(req.meal_type.value, req.image_url)
    return AnalyzeMealResponse(
        meal_id=req.meal_id,
        model_version=_VISION_MODEL_VERSION,
        # 음식 인식만 하고 자유 텍스트 조언을 생성하지 않는 엔드포인트라 늘 SAFE다 —
        # safety_guard.py(의료 조언 검사)는 자유 텍스트를 만드는 short/long-feedback용이다.
        safety_status=SafetyStatus.SAFE,
        items=items,
    )


@app.post("/short-feedback", response_model=ShortFeedbackResponse, tags=["feedback"])
def short_feedback(req: ShortFeedbackRequest) -> ShortFeedbackResponse:
    # TODO: agents/ 의 Feedback Agent 를 붙인다.
    raise _not_implemented("short-feedback")


# TODO: 계약 확정 후 schemas 의 LongFeedbackRequest / Response 로 바꾼다.
@app.post("/long-feedback", tags=["feedback"])
def long_feedback(req: dict[str, Any]) -> dict[str, Any]:
    raise _not_implemented("long-feedback")
