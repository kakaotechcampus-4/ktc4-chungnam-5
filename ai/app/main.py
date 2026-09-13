"""AI Service 엔트리포인트.

설정 검증은 import 시점에 돈다 — 런타임에 이상하게 죽는 것보다 기동 시 명확히 죽는 게 낫다.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers import analyze_meal, long_feedback, short_feedback

settings = get_settings()

app = FastAPI(
    title="AI Service" + (" (stub)" if settings.stub_mode else ""),
    version="0.1.0",
    description=(
        "식사 사진·텍스트에서 음식을 인식하고 Q/Q/S 점수를 근거로 피드백 문장을 생성한다.\n\n"
        "STUB_MODE 에서는 더미 데이터를 돌려준다. `X-Stub-Scenario` 헤더로 상황을 고를 수 있다."
    ),
)

app.include_router(analyze_meal.router)
app.include_router(short_feedback.router)
app.include_router(long_feedback.router)


@app.exception_handler(NotImplementedError)
async def _not_implemented(request: Request, exc: NotImplementedError) -> JSONResponse:
    """실사용 이미지에 agents/llm/ 이 아직 없을 때. 스택트레이스 대신 읽을 수 있는 응답을 준다."""
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={"detail": str(exc)},
    )


@app.get("/health", tags=["meta"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "stubMode": settings.stub_mode,
        "defaultScenario": settings.stub_default_scenario.value,
    }
