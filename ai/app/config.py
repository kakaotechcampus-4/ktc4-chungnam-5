"""AI Service 설정 — 모든 모델 호출은 이 설정을 거친다.

에이전트가 LLM 클라이언트를 직접 만들지 않는다(README). Serverless API 게이트웨이라
URL 자체가 모델을 고정한다 — `LLM_VISION_URL`/`LLM_REASONING_URL`이 `mlapi.run` 밖을
가리키면 기동을 거부한다. API 키·엔드포인트 URL은 `Field(repr=False)`로 감싸서
로그·스택트레이스에 안 찍히게 한다(README: "URL도 비밀로 다룬다").
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_GATEWAY_PREFIX = "https://mlapi.run/"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LLM_API_KEY: SecretStr
    LLM_VISION_URL: str = Field(repr=False)
    """음식 인식(/analyze-meal) — Gemini Flash 게이트웨이."""
    LLM_REASONING_URL: str = Field(repr=False)
    """피드백(/short-feedback, /long-feedback) — Claude Sonnet 5 게이트웨이."""
    LLM_TIMEOUT_SEC: float = 45.0
    """AI 호출은 10~30초 걸린다(README). 넉넉히 45초."""
    LLM_MAX_RETRIES: int = 1
    """재시도는 비용 × 지연의 곱셈이라 기본값을 낮게 둔다."""

    DEV_VISION_MODEL: str | None = None
    """⚠️ 임시(개발용). mlapi.run 게이트웨이 요청/응답 스펙을 아직 못 받아서, 이 값이
    설정되면 게이트웨이 대신 Claude API를 직접 호출해 실제 사진 분석을 확인한다
    (여러 모델을 바꿔가며 테스트할 수 있게 이름을 뺐다. 예: `claude-haiku-5-5`).
    게이트웨이 스펙 받으면 이 필드와 관련 분기를 통째로 지운다 — `LLM_VISION_URL`
    구조(팀 공유 계약)는 그대로 둔다."""

    @field_validator("LLM_VISION_URL", "LLM_REASONING_URL")
    @classmethod
    def _gateway_host_only(cls, value: str) -> str:
        if not value.startswith(_GATEWAY_PREFIX):
            raise ValueError(f"엔드포인트는 {_GATEWAY_PREFIX}... 형태여야 합니다.")
        return value


@lru_cache
def get_settings() -> Settings:
    """`Settings()` 는 매번 `.env`를 다시 읽으므로, 호출부는 이 함수로 캐시된 값을 쓴다."""
    return Settings()
