"""AI Service 진입점.

BE 와는 HTTP 로만 대화한다. 경로·요청·응답 모양은 ai-stub/main.py 와 같다.
컨테이너가 기대하는 것(ai/Dockerfile): 진입점은 이 파일의 `app`, GET /health 가 200.

    uvicorn app.main:app --reload --port 8002
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, status

from app.schemas import AnalyzeMealRequest, AnalyzeMealResponse

app = FastAPI(title="AI Service", version="0.1.0")


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
def analyze_meal(req: AnalyzeMealRequest) -> AnalyzeMealResponse:
    # TODO: agents/ 의 Food Analyzer 를 붙인다.
    raise _not_implemented("analyze-meal")


# TODO: 계약 확정 후 schemas 의 ShortFeedbackRequest / Response 로 바꾼다.
@app.post("/short-feedback", tags=["feedback"])
def short_feedback(req: dict[str, Any]) -> dict[str, Any]:
    raise _not_implemented("short-feedback")


# TODO: 계약 확정 후 schemas 의 LongFeedbackRequest / Response 로 바꾼다.
@app.post("/long-feedback", tags=["feedback"])
def long_feedback(req: dict[str, Any]) -> dict[str, Any]:
    raise _not_implemented("long-feedback")
