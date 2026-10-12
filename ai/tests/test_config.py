"""`app/config.py` 단위 테스트."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app import config
from app.config import Settings

_VALID_KWARGS = {
    "LLM_API_KEY": "test-key",
    "LLM_BASE_URL": "https://llm.example.com",
    "LLM_MODEL_VISION": "test-vision-model",
    "LLM_MODEL_REASONING": "test-reasoning-model",
}


@pytest.fixture(autouse=True)
def _whitelist(monkeypatch):
    """실제 ALLOWED_MODELS 는 아직 빈 플레이스홀더라, 테스트용 값으로 채운다."""
    monkeypatch.setattr(
        config, "ALLOWED_MODELS", frozenset({"test-vision-model", "test-reasoning-model"})
    )


def test_accepts_valid_settings():
    settings = Settings(**_VALID_KWARGS)
    assert settings.LLM_MODEL_VISION == "test-vision-model"


def test_default_timeout_and_retries():
    settings = Settings(**_VALID_KWARGS)
    assert settings.LLM_TIMEOUT_SEC == 45.0
    assert settings.LLM_MAX_RETRIES == 1


def test_api_key_hidden_in_repr():
    settings = Settings(**_VALID_KWARGS)
    assert "test-key" not in repr(settings)


def test_base_url_hidden_in_repr():
    settings = Settings(**_VALID_KWARGS)
    assert "llm.example.com" not in repr(settings)


def test_rejects_non_https_base_url():
    with pytest.raises(ValidationError):
        Settings(**{**_VALID_KWARGS, "LLM_BASE_URL": "http://llm.example.com"})


@pytest.mark.parametrize("field", ["LLM_MODEL_VISION", "LLM_MODEL_REASONING"])
def test_rejects_model_outside_whitelist(field):
    with pytest.raises(ValidationError):
        Settings(**{**_VALID_KWARGS, field: "not-allowed-model"})
