"""코치 설정은 실제 .env 및 프로세스 환경과 격리한다."""
import pytest
from pydantic import ValidationError

from app.config import CoachSettings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in ("LLM_API_KEY", "LLM_COACH_URL", "LLM_VISION_URL", "LLM_REASONING_URL"):
        monkeypatch.delenv(name, raising=False)


def test_coach_requires_only_its_own_endpoint():
    settings = CoachSettings(_env_file=None, LLM_API_KEY="secret-key",
                             LLM_COACH_URL="https://mlapi.run/secret-endpoint")
    assert settings.LLM_COACH_URL.endswith("secret-endpoint")
    assert "secret-key" not in repr(settings)
    assert "secret-endpoint" not in repr(settings)


def test_reasoning_endpoint_does_not_replace_missing_coach_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_URL", "https://mlapi.run/reasoning")
    with pytest.raises(ValidationError) as caught:
        CoachSettings(_env_file=None, LLM_API_KEY="test-key")
    assert any(e["loc"] == ("LLM_COACH_URL",) and e["type"] == "missing"
               for e in caught.value.errors())


@pytest.mark.parametrize("url", ["", "http://mlapi.run/id", "https://other.example/id",
                                 "https://mlapi.run.evil.example/id", "https://mlapi.run@evil.example/id"])
def test_invalid_coach_endpoints(url):
    with pytest.raises(ValidationError):
        CoachSettings(_env_file=None, LLM_API_KEY="test-key", LLM_COACH_URL=url)


def test_loads_coach_endpoint_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_COACH_URL", "https://mlapi.run/coach")
    assert CoachSettings(_env_file=None).LLM_COACH_URL == "https://mlapi.run/coach"
