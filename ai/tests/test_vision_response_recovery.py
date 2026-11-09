"""비전 HTTP 응답부터 API까지, 저장된 파싱 실패와 재시도 상한을 검증한다.

모델 전송만 MockTransport로 바꾸고 실제 응답 파서·정규화·라우터를 사용한다.
실제 모델 호출 없이도 부분 응답을 성공으로 내보내거나 중첩 재시도하지 않는지
확인할 수 있다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import food_analyzer
from app.main import app


_KEY = "test-vision-key-never-log"
_GATEWAY = "https://mlapi.run/private-vision-token"
_IMAGE = "https://image.example/private-meal.jpg?signature=private-image-token"
_REQUEST = {
    "mealId": "recovery-meal", "mealType": "LUNCH",
    "eatenAt": "2026-11-09T12:00:00+09:00", "stage": "MAINTENANCE",
    "imageUrl": _IMAGE,
}
_VALID = json.dumps({"items": [{
    "originalFoodName": "계란 프라이", "estimatedAmount": 80,
    "unit": "g", "confidence": 0.8,
}]}, ensure_ascii=False)


def _reply(content, *, finish_reason="stop"):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"completion_tokens": 21},
    })


@pytest.fixture
def truncated_response():
    fixture = Path(__file__).parent / "fixtures/vision_truncated_manybanchan.json"
    return json.loads(fixture.read_text())["raw_text"]


def _install_transport(monkeypatch, outcomes, *, retries=1, image_outcome=None, timeout=5):
    """Count actual gateway requests independently of production retry internals."""
    gets, posts = [], []
    monkeypatch.setattr(food_analyzer, "get_settings", lambda: SimpleNamespace(
        LLM_VISION_URL=_GATEWAY, LLM_TIMEOUT_SEC=timeout,
        LLM_MAX_RETRIES=retries,
        LLM_API_KEY=SimpleNamespace(get_secret_value=lambda: _KEY),
    ))

    def handler(request):
        if request.method == "GET":
            gets.append(request)
            assert str(request.url) == _IMAGE
            if image_outcome is not None:
                return image_outcome(request)
            return httpx.Response(200, content=b"unchanged-photo-bytes",
                                  headers={"content-type": "image/jpeg"})
        assert request.method == "POST"
        assert str(request.url) == _GATEWAY + "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer " + _KEY
        posts.append(request)
        assert len(posts) <= len(outcomes), "Gateway exceeded the expected shared attempt budget"
        outcome = outcomes[len(posts)-1]
        return outcome(request) if callable(outcome) else outcome

    factory = httpx.AsyncClient
    monkeypatch.setattr(food_analyzer.httpx, "AsyncClient",
                        lambda **kwargs: factory(transport=httpx.MockTransport(handler), **kwargs))
    return gets, posts


def _api():
    return TestClient(app).post("/analyze-meal", json=_REQUEST)


def test_saved_truncation_then_valid_response_recovers_without_partial_items(monkeypatch, truncated_response):
    gets, posts = _install_transport(monkeypatch, [
        _reply(truncated_response, finish_reason=None), _reply(_VALID),
    ])
    response = _api()
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["originalFoodName"] == "달걀후라이"
    assert items[0]["estimatedAmount"] == 80
    assert items[0]["unit"] == "g"
    assert items[0]["confidence"] == 0.8
    assert items[0]["candidateFoodRefId"] is None
    assert len(gets) == 1 and len(posts) == 2
    # Retry the same photo and prompt rather than adding repair instructions.
    assert posts[0].content == posts[1].content


@pytest.mark.parametrize("retries", [0, 1])
def test_saved_truncation_exhaustion_returns_502_not_empty_success(monkeypatch, truncated_response, retries):
    gets, posts = _install_transport(monkeypatch,
        [_reply(truncated_response, finish_reason=None) for _ in range(retries+1)], retries=retries)
    response = _api()
    assert response.status_code == 502
    assert "items" not in response.json()
    assert set(response.json()) == {"detail"}
    assert isinstance(response.json()["detail"], str)
    assert truncated_response not in response.text
    assert len(gets) == 1 and len(posts) == retries+1


def test_http_and_parsing_failures_share_one_attempt_budget(monkeypatch, truncated_response):
    gets, posts = _install_transport(monkeypatch, [
        httpx.Response(503, text="private-upstream-error"), _reply(truncated_response),
    ])
    response = _api()
    assert response.status_code == 502
    assert len(gets) == 1 and len(posts) == 2


@pytest.mark.parametrize("status", [401, 403, 501])
def test_nonretryable_http_failure_is_not_retried(monkeypatch, status):
    gets, posts = _install_transport(monkeypatch, [httpx.Response(status, text=_KEY)])
    response = _api()
    assert response.status_code == 502
    assert _KEY not in response.text
    assert _GATEWAY not in response.text
    assert len(gets) == 1 and len(posts) == 1


@pytest.mark.parametrize("final_valid", [True, False])
def test_configured_three_attempt_budget_is_shared_across_failure_types(monkeypatch, final_valid):
    gets, posts = _install_transport(monkeypatch, [
        httpx.Response(503), _reply('{"items": {}}'),
        _reply(_VALID if final_valid else "still-invalid"),
    ], retries=2)
    response = _api()
    assert response.status_code == (200 if final_valid else 502)
    assert len(gets) == 1 and len(posts) == 3


def test_missing_vision_configuration_returns_503_without_transport(monkeypatch):
    monkeypatch.setattr(food_analyzer, "get_settings", lambda: SimpleNamespace(LLM_VISION_URL=""))
    def forbidden_client(**_kwargs):
        pytest.fail("Missing model configuration must not create an HTTP client")
    monkeypatch.setattr(food_analyzer.httpx, "AsyncClient", forbidden_client)
    response = _api()
    assert response.status_code == 503
    assert set(response.json()) == {"detail"}


@pytest.mark.parametrize("status", [429, 503])
def test_retryable_http_failure_can_recover(monkeypatch, status):
    gets, posts = _install_transport(monkeypatch, [httpx.Response(status), _reply(_VALID)])
    response = _api()
    assert response.status_code == 200
    assert len(gets) == 1 and len(posts) == 2


@pytest.mark.parametrize("invalid", [
    {}, {"choices": []}, {"choices": [None]},
    {"choices": [{"message": None}]},
    {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": [{"type": "text", "text": _VALID}]}}]},
    {"choices": [{"message": {"content": ""}}]},
    {"choices": [{"message": {"content": "not-json"}}]},
    {"choices": [{"message": {"content": '{"items": {}}'}}]},
    {"choices": [{"message": {"content": '{"items": [{"originalFoodName": "밥"}]}'}}]},
    {"choices": [{"message": {"content": '{"items": [{"originalFoodName": "밥", "estimatedAmount": -1, "unit": "g", "confidence": 0.8}]}'}}]},
], ids=["missing-choices", "empty-choices", "invalid-choice", "invalid-message",
        "null-content", "nontext-content", "empty-content", "invalid-json",
        "wrong-items-type", "incomplete-item", "invalid-nutrient-amount"])
def test_invalid_envelopes_and_item_schemas_retry_then_fail_cleanly(monkeypatch, invalid):
    gets, posts = _install_transport(monkeypatch, [httpx.Response(200, json=invalid) for _ in range(2)])
    response = _api()
    assert response.status_code == 502
    assert set(response.json()) == {"detail"}
    assert len(gets) == 1 and len(posts) == 2


@pytest.mark.parametrize("payload", ["not gateway JSON", "[]", "null"])
def test_invalid_gateway_json_does_not_escape_as_uncaught_exception(monkeypatch, payload):
    gets, posts = _install_transport(monkeypatch,
        [httpx.Response(200, text=payload) for _ in range(2)])
    response = _api()
    assert response.status_code == 502
    assert len(gets) == 1 and len(posts) == 2


@pytest.mark.parametrize("content", ['{"items": []}', '```json\n{"items": []}\n```'])
def test_valid_empty_food_result_remains_success_without_retry(monkeypatch, content):
    gets, posts = _install_transport(monkeypatch, [_reply(content)])
    response = _api()
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert len(gets) == 1 and len(posts) == 1


def test_gateway_helper_returns_original_validated_text(monkeypatch):
    raw = "```json\n" + _VALID + "\n```"
    _install_transport(monkeypatch, [_reply(raw)])
    assert asyncio.run(food_analyzer._call_vision_model(_IMAGE, "unchanged-prompt")) == raw


def test_length_finish_reason_retries_even_when_json_is_structurally_valid(monkeypatch):
    gets, posts = _install_transport(monkeypatch, [
        _reply('{"items": []}', finish_reason="length"), _reply(_VALID),
    ])
    response = _api()
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert len(gets) == 1 and len(posts) == 2


@pytest.mark.parametrize("failure", ["content_filter", "refusal"])
def test_provider_refusal_is_not_retried_or_returned_as_empty_success(monkeypatch, failure):
    body = {"choices": [{"message": {"content": '{"items": []}'}, "finish_reason": "stop"}]}
    if failure == "content_filter":
        body["choices"][0]["finish_reason"] = "content_filter"
    else:
        body["choices"][0]["message"]["refusal"] = "private-refusal-text"
    gets, posts = _install_transport(monkeypatch, [httpx.Response(200, json=body)])
    response = _api()
    assert response.status_code == 502
    assert "private-refusal-text" not in response.text
    assert len(gets) == 1 and len(posts) == 1


@pytest.mark.parametrize("finish_reason", [None, "unknown-provider-reason"])
def test_valid_content_without_known_finish_reason_remains_compatible(monkeypatch, finish_reason):
    gets, posts = _install_transport(monkeypatch, [_reply(_VALID, finish_reason=finish_reason)])
    response = _api()
    assert response.status_code == 200
    assert len(gets) == 1 and len(posts) == 1


@pytest.mark.parametrize("finish_reason", ["tool_calls", "function_call"])
def test_unsupported_tool_completion_is_retried(monkeypatch, finish_reason):
    gets, posts = _install_transport(monkeypatch, [_reply(_VALID, finish_reason=finish_reason), _reply(_VALID)])
    response = _api()
    assert response.status_code == 200
    assert len(gets) == 1 and len(posts) == 2


@pytest.mark.parametrize("error_type, expected_status", [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502)])
def test_gateway_transport_exhaustion_is_sanitized(monkeypatch, error_type, expected_status):
    def failed(request):
        raise error_type(_KEY + " " + _GATEWAY, request=request)
    gets, posts = _install_transport(monkeypatch, [failed, failed])
    response = _api()
    assert response.status_code == expected_status
    assert _KEY not in response.text and _GATEWAY not in response.text
    assert len(gets) == 1 and len(posts) == 2


def test_image_download_failure_does_not_start_gateway_calls(monkeypatch):
    def failed_image(request):
        raise httpx.ReadTimeout(_IMAGE, request=request)
    gets, posts = _install_transport(monkeypatch, [], image_outcome=failed_image)
    response = _api()
    assert response.status_code == 504
    assert _IMAGE not in response.text
    assert len(gets) == 1 and posts == []


def test_expired_image_url_does_not_trigger_model_retries(monkeypatch):
    gets, posts = _install_transport(monkeypatch, [],
        image_outcome=lambda _request: httpx.Response(403, text="private-image-response"))
    response = _api()
    assert response.status_code == 502
    assert "private-image-response" not in response.text and _IMAGE not in response.text
    assert len(gets) == 1 and posts == []


def test_all_gateway_attempts_share_one_deadline(monkeypatch):
    async def delayed_invalid(_request):
        await asyncio.sleep(0.08)
        return _reply("incomplete")

    async def delayed_valid(_request):
        await asyncio.sleep(0.08)
        return _reply(_VALID)

    gets, posts = _install_transport(monkeypatch, [delayed_invalid, delayed_valid], timeout=0.12)
    response = _api()
    assert response.status_code == 504
    assert len(gets) == 1
    assert 1 <= len(posts) <= 2


def test_image_download_uses_the_same_request_deadline(monkeypatch):
    async def stalled_image(_request):
        await asyncio.sleep(0.08)
        return httpx.Response(200, content=b"image")

    gets, posts = _install_transport(monkeypatch, [], image_outcome=stalled_image, timeout=0.01)
    response = _api()
    assert response.status_code == 504
    assert len(gets) == 1 and posts == []


def test_failure_observations_do_not_log_content_urls_or_arbitrary_finish_reason(monkeypatch, caplog):
    secret = "private-finish-reason-and-food-description"
    raw = '{"items": [{"originalFoodName": "' + _KEY + secret
    gets, posts = _install_transport(monkeypatch, [
        _reply(raw, finish_reason=secret), _reply(_VALID),
    ])
    with caplog.at_level(logging.INFO, logger="app.food_analyzer"):
        response = _api()
    assert response.status_code == 200
    records = [r for r in caplog.records if r.name == "app.food_analyzer"]
    assert records, "Retry observations must be available without sensitive response bodies"
    attempts = [r for r in records if hasattr(r, "attempt")]
    assert [(r.attempt, r.max_attempts) for r in attempts] == [(1, 2), (2, 2)]
    assert [r.response_chars for r in attempts] == [len(raw), len(_VALID)]
    assert [r.finish_reason for r in attempts] == ["other", "stop"]
    logged = "\n".join(r.getMessage() + repr({k: v for k, v in vars(r).items()
                                            if k not in {"args", "msg"}}) for r in records)
    for private in [_KEY, _GATEWAY, _IMAGE, secret, raw]:
        assert private not in logged
    assert len(gets) == 1 and len(posts) == 2


@pytest.mark.parametrize("failure, expected_outcome", [
    ("saved_truncated_json", "invalid_json"),
    ("wrong_item_schema", "invalid_schema"),
])
def test_log_distinguishes_json_syntax_from_item_schema(monkeypatch, caplog, truncated_response,
                                                      failure, expected_outcome):
    content = truncated_response if failure == "saved_truncated_json" else '{"items": {}}'
    _install_transport(monkeypatch, [_reply(content, finish_reason=None)], retries=0)
    with caplog.at_level(logging.WARNING, logger="app.food_analyzer"):
        response = _api()
    assert response.status_code == 502
    attempts = [r for r in caplog.records if r.name == "app.food_analyzer" and hasattr(r, "attempt")]
    assert len(attempts) == 1
    assert attempts[0].outcome == expected_outcome
    assert expected_outcome in attempts[0].getMessage()
    assert attempts[0].finish_reason == "unknown"
    assert content not in attempts[0].getMessage()
