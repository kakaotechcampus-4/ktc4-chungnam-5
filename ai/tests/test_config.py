"""`app/config.py` 단위 테스트."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

_VALID_KWARGS = {
    "LLM_API_KEY": "test-key",
    "LLM_VISION_URL": "https://mlapi.run/vision-uuid",
    "LLM_REASONING_URL": "https://mlapi.run/reasoning-uuid",
}


def test_accepts_valid_settings():
    settings = Settings(**_VALID_KWARGS)
    assert settings.LLM_VISION_URL == "https://mlapi.run/vision-uuid"


def test_default_timeout_and_retries():
    settings = Settings(**_VALID_KWARGS)
    assert settings.LLM_TIMEOUT_SEC == 45.0
    assert settings.LLM_MAX_RETRIES == 1


def test_api_key_hidden_in_repr():
    settings = Settings(**_VALID_KWARGS)
    assert "test-key" not in repr(settings)


def test_vision_url_hidden_in_repr():
    settings = Settings(**_VALID_KWARGS)
    assert "vision-uuid" not in repr(settings)


def test_reasoning_url_hidden_in_repr():
    settings = Settings(**_VALID_KWARGS)
    assert "reasoning-uuid" not in repr(settings)


@pytest.mark.parametrize("field", ["LLM_VISION_URL", "LLM_REASONING_URL"])
def test_rejects_non_gateway_host(field):
    with pytest.raises(ValidationError):
        Settings(**{**_VALID_KWARGS, field: "https://evil.example.com/vision-uuid"})


@pytest.mark.parametrize("field", ["LLM_VISION_URL", "LLM_REASONING_URL"])
def test_rejects_non_https(field):
    with pytest.raises(ValidationError):
        Settings(**{**_VALID_KWARGS, field: "http://mlapi.run/vision-uuid"})
