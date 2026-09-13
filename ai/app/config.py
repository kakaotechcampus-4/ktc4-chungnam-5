"""설정. 없거나 형식이 틀리면 기동 시점에 실패한다."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Scenario(str, Enum):
    """스텁이 흉내 낼 상황. X-Stub-Scenario 헤더로 고른다."""

    SUCCESS = "SUCCESS"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NO_MATCH = "NO_MATCH"
    BLOCKED = "BLOCKED"
    ERROR_500 = "ERROR_500"
    SLOW = "SLOW"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    stub_mode: bool = True
    stub_latency_ms: int = 0
    stub_default_scenario: Scenario = Scenario.SUCCESS

    # STUB_MODE=false 에서만 필요하다
    llm_api_key: str | None = None
    llm_base_url: str | None = None

    @model_validator(mode="after")
    def _require_llm_keys_when_real(self) -> Settings:
        if self.stub_mode:
            return self
        if not self.llm_api_key:
            raise ValueError("STUB_MODE=false 면 LLM_API_KEY 가 필요하다")
        if not self.llm_base_url:
            raise ValueError("STUB_MODE=false 면 LLM_BASE_URL 이 필요하다")
        if not self.llm_base_url.startswith("https://"):
            raise ValueError("LLM_BASE_URL 은 https 여야 한다")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
