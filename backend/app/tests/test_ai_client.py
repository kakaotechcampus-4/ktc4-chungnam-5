"""AI HTTP 클라이언트 — 어떤 실패를 재시도할지.

틀리기 쉬운 것:
  - 4xx(계약 위반·인증 실패)까지 재시도하는 것. 같은 요청은 몇 번을 보내도 같은 4xx 다.
    실제 AI 에서는 시도마다 LLM 비용이 든다
  - 반대로 408·429 처럼 "잠시 뒤엔 될" 4xx 를 영구 실패로 격리하는 것
"""

from __future__ import annotations

import httpx
import pytest

from app.infra.ai import AiRequestRejected, HttpAiClient
from app.infra.queue import NonRetryableError


def _client(status_code: int) -> HttpAiClient:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "본문"})

    return HttpAiClient("http://ai.test", transport=httpx.MockTransport(respond))


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_client_error_is_not_retryable(status_code):
    with pytest.raises(AiRequestRejected) as exc_info:
        _client(status_code).analyze_meal({"mealId": "m1"})

    assert isinstance(exc_info.value, NonRetryableError)
    assert exc_info.value.status_code == status_code


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503])
def test_transient_error_is_retryable(status_code):
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        _client(status_code).short_feedback({"scope": "MEAL"})

    assert not isinstance(exc_info.value, NonRetryableError)


def test_rejection_message_does_not_carry_the_response_body():
    """AI 가 돌려준 본문에 요청(음식명 등)이 되비칠 수 있다. `last_error` 에 남기지 않는다(규칙 6)."""
    with pytest.raises(AiRequestRejected) as exc_info:
        _client(422).long_feedback({})

    message = str(exc_info.value)
    assert "422" in message
    assert "/long-feedback" in message
    assert "본문" not in message


def test_success_returns_the_json_body():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = HttpAiClient("http://ai.test", transport=httpx.MockTransport(respond))

    assert client.analyze_meal({}) == {"ok": True}
