"""AI Service 설정 — 모든 모델 호출은 이 설정을 거친다.

에이전트가 LLM 클라이언트를 직접 만들지 않는다(README). API 키는 `SecretStr`로,
base URL은 `Field(repr=False)`로 감싸서 로그·스택트레이스에 안 찍히게 한다.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_MODELS: frozenset[str] = frozenset(
    {
        # TODO: 실제 사용할 모델명이 정해지면 채운다. 목록 밖 모델은 기동 시 거부된다.
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LLM_API_KEY: SecretStr
    LLM_BASE_URL: str = Field(repr=False)
    LLM_MODEL_VISION: str
    LLM_MODEL_REASONING: str
    LLM_TIMEOUT_SEC: float = 45.0
    """AI 호출은 10~30초 걸린다(README). 넉넉히 45초."""
    LLM_MAX_RETRIES: int = 1
    """재시도는 비용 × 지연의 곱셈이라 기본값을 낮게 둔다."""

    @field_validator("LLM_BASE_URL")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("LLM_BASE_URL 은 https 여야 합니다.")
        return value

    @model_validator(mode="after")
    def _models_in_whitelist(self) -> Settings:
        for field_name, model_name in (
            ("LLM_MODEL_VISION", self.LLM_MODEL_VISION),
            ("LLM_MODEL_REASONING", self.LLM_MODEL_REASONING),
        ):
            if model_name not in ALLOWED_MODELS:
                raise ValueError(
                    f"{field_name}={model_name!r} 는 ALLOWED_MODELS 화이트리스트에 없습니다."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """`Settings()` 는 매번 `.env`를 다시 읽으므로, 호출부는 이 함수로 캐시된 값을 쓴다."""
    return Settings()
