import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import ai_coach
from app.main import app
from app.schemas import ChatRequest, ChatResponse

client = TestClient(app)
REQUEST = {
    "userId": "u1", "conversationId": "c1", "text": "다른 음식 추천해줘",
    "history": [{"role": "USER", "text": "첫 질문"}, {"role": "USER", "text": "다음 질문"}],
    "context": {"stage": "MAINTENANCE", "today": {"date": "2026-10-30", "meals": []}},
}


def test_messages_preserve_history_and_null_context():
    messages = ai_coach.build_messages(ChatRequest.model_validate(REQUEST))
    data = json.loads(messages[1]["content"])
    assert data["history"] == REQUEST["history"]
    assert data["context"]["baseMeal"] is None
    assert "userId" not in data
    assert data["text"] == REQUEST["text"]


def test_safe_route_filters_cards_and_discards_unverified_data(monkeypatch):
    async def fake(messages):
        return {"safetyStatus": "SAFE", "text": " 두부는 어떤가요? ", "foodCards": [
            None, {}, {"foodName": " " , "advice": "이유"},
            {"foodName": "a" * 256, "advice": "이유"},
            {"foodName": "두부", "advice": "단백질을 곁들여요", "candidateFoodRefId": "invented", "nutrients": {"kcal": 100}},
        ]}
    monkeypatch.setattr(ai_coach, "call_model", fake)
    response = client.post("/chat", json=REQUEST)
    assert response.status_code == 200
    assert response.json() == {"text": "두부는 어떤가요?", "modelVersion": "chat-v1", "safetyStatus": "SAFE",
                               "foodCards": [{"foodName": "두부", "advice": "단백질을 곁들여요", "candidateFoodRefId": None}]}


def test_input_block_does_not_call_model(monkeypatch):
    async def fail(messages):
        pytest.fail("blocked input must not call model")
    monkeypatch.setattr(ai_coach, "call_model", fail)
    response = client.post("/chat", json={**REQUEST, "text": "약 용량 늘려도 돼?"})
    assert response.status_code == 200
    assert response.json()["text"] is None
    assert response.json()["safetyStatus"] == "BLOCKED"
    assert response.json()["foodCards"] == []


@pytest.mark.parametrize("status", ["BLOCKED", "REVIEW_REQUIRED"])
def test_non_safe_clears_content(status):
    result = ai_coach.parse_response({"safetyStatus": status, "text": "버릴 본문", "foodCards": [1]})
    assert result.text is None and result.food_cards == []
    assert result.safety_status.value == status


@pytest.mark.parametrize("raw", ['[]', '{', {}, {"safetyStatus": "UNKNOWN"},
                                     {"safetyStatus": "SAFE", "text": " "}])
def test_bad_responses(raw):
    with pytest.raises(ai_coach.InvalidCoachResponse):
        ai_coach.parse_response(raw)


def test_output_medical_guard():
    result = ai_coach.parse_response({"safetyStatus": "SAFE", "text": "약 용량을 늘리면 돼요"})
    assert result.safety_status.value == "BLOCKED"


def test_unavailable_model_is_503(monkeypatch):
    async def unavailable(messages):
        raise ai_coach.CoachUnavailable()
    monkeypatch.setattr(ai_coach, "call_model", unavailable)
    assert client.post("/chat", json=REQUEST).status_code == 503


@pytest.mark.parametrize("failure,code", [(ai_coach.InvalidCoachResponse, 502), (TimeoutError, 504)])
def test_failures_have_sanitized_errors(monkeypatch, failure, code):
    async def fake(messages):
        if failure is ai_coach.InvalidCoachResponse:
            return "invalid json with private content"
        raise failure("private content")
    monkeypatch.setattr(ai_coach, "call_model", fake)
    response = client.post("/chat", json=REQUEST)
    assert response.status_code == code
    assert "private" not in response.text


def test_timeout_does_not_retry(monkeypatch):
    calls = []
    async def slow(messages):
        calls.append(1)
        await asyncio.sleep(1)
    monkeypatch.setattr(ai_coach, "call_model", slow)
    monkeypatch.setattr(ai_coach, "CHAT_TIMEOUT_SEC", 0.001)
    assert client.post("/chat", json=REQUEST).status_code == 504
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["history", "context", "text"])
def test_required_fields(field):
    body = {key: value for key, value in REQUEST.items() if key != field}
    assert client.post("/chat", json=body).status_code == 422


def test_response_schema_rejects_empty_safe_text():
    with pytest.raises(ValueError):
        ChatResponse(text=" ", modelVersion="chat-v1", safetyStatus="SAFE")


@pytest.mark.parametrize("scenario,expected", [("success", 200), ("http_error", 503),
                                               ("timeout", 504), ("bad_json", 502)])
def test_gateway_transport(monkeypatch, scenario, expected):
    import httpx
    from app.config import CoachSettings
    settings = CoachSettings(_env_file=None, LLM_API_KEY="test-key",
                             LLM_COACH_URL="https://mlapi.run/test-reasoning")
    monkeypatch.setattr(ai_coach, "get_coach_settings", lambda: settings)
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.path == "/test-reasoning/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["max_completion_tokens"] == 2000
        assert payload["messages"][0]["role"] == "system"
        if scenario == "timeout":
            raise httpx.ReadTimeout("private endpoint", request=request)
        if scenario == "http_error":
            return httpx.Response(429, text="private upstream error")
        if scenario == "bad_json":
            return httpx.Response(200, json={"choices": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"safetyStatus": "SAFE", "text": "두부는 어떤가요?", "foodCards": []})}}]})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(ai_coach.httpx, "AsyncClient",
                        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    response = client.post("/chat", json=REQUEST)
    assert response.status_code == expected
    assert len(requests) == 1
    assert "private" not in response.text
