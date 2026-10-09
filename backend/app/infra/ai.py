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

from app.infra.queue import NonRetryableError

# 4xx 지만 "잠시 뒤엔 될" 응답이다. 5xx 처럼 재시도에 맡긴다.
_TRANSIENT_CLIENT_ERRORS = frozenset({408, 429})


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


class AiRequestRejected(NonRetryableError):
    """AI 가 요청을 4xx 로 거부했다 — 계약 위반·인증 실패 등. 재시도해도 같다.

    응답 본문은 담지 않는다. 요청(음식명 등)이 되비쳐 `last_error` 에 남을 수 있다(규칙 6).
    """

    def __init__(self, status_code: int, path: str) -> None:
        super().__init__(f"AI 가 요청을 거부했다: {status_code} {path}")
        self.status_code = status_code
        self.path = path


class HttpAiClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = 45.0,
        stub_scenario: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {}
        if stub_scenario:
            headers["X-Stub-Scenario"] = stub_scenario
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_sec,
            headers=headers,
            transport=transport,
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(path, json=payload)
        # 4xx 는 재시도해도 같으니 바로 격리한다. 5xx·408·429 는 예외로 올려 큐의 재시도에 맡긴다.
        if response.is_client_error and response.status_code not in _TRANSIENT_CLIENT_ERRORS:
            raise AiRequestRejected(response.status_code, path)
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
