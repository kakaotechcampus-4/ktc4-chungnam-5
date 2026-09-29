"""AI Service HTTP 클라이언트.

D13 — `infra/` 는 `Protocol` 뒤에 구현을 숨긴다. 도메인은 어느 구현이 붙는지 모른다.
지금 붙는 상대는 `ai-stub/` 이고, 나중에 실제 AI Service 로 바뀌어도 이 경계는 그대로다.

**Worker 만 이 클라이언트를 쓴다.** BE API 는 큐에 넣고 202 를 돌려줄 뿐,
AI 를 직접 부르지 않는다 — 분석이 10~30초 걸리기 때문이다.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict


class AiClient(Protocol):
    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def short_feedback(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def long_feedback(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class AiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    AI_SERVICE_BASE_URL: str = "http://localhost:8001"
    # 분석은 10~30초 걸린다. 넉넉히 잡는다.
    AI_TIMEOUT_SEC: float = 45.0

    # ai-stub 의 X-Stub-Scenario 를 강제로 지정한다. 실패 경로를 부르는 손잡이다.
    # 로컬에서만 채우고 프로덕션에서는 비운다.
    # 비어 있으면 헤더 자체가 붙지 않으므로 실제 AI 에는 아무 영향이 없다.
    AI_STUB_SCENARIO: str = ""


class HttpAiClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = 45.0,
        stub_scenario: str = "",
    ) -> None:
        headers = {}
        if stub_scenario:
            headers["X-Stub-Scenario"] = stub_scenario
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_sec,
            headers=headers,
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(path, json=payload)
        # 5xx 든 4xx 든 예외로 올린다. 워커가 메시지를 지우지 않아 재시도된다.
        response.raise_for_status()
        return response.json()

    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/analyze-meal", payload)

    def short_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/short-feedback", payload)

    def long_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/long-feedback", payload)

    def close(self) -> None:
        self._client.close()


def build_ai_client(settings: AiSettings | None = None) -> HttpAiClient:
    settings = settings or AiSettings()
    return HttpAiClient(
        settings.AI_SERVICE_BASE_URL,
        timeout_sec=settings.AI_TIMEOUT_SEC,
        stub_scenario=settings.AI_STUB_SCENARIO,
    )
