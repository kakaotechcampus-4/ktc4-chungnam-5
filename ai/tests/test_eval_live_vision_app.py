"""The live observer adds evidence without changing requests or model behavior."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request, Response

from evals.live_vision_app import LiveVisionCapture, _active


VISION = "https://mlapi.run/private-vision"
REASONING = "https://mlapi.run/private-reasoning"
IMAGE = "http://api:8000/media/private-image"
KEY = "private-api-key"
ENDPOINT = VISION + "/v1/chat/completions"


@pytest.fixture(autouse=True)
def restore_httpx(monkeypatch):
    # Wrappers are deliberately process-local; never leak one to other tests.
    monkeypatch.setattr(httpx.AsyncClient, "post", httpx.AsyncClient.post)
    assert _active.get() is None
    yield
    assert _active.get() is None


def settings():
    return SimpleNamespace(LLM_VISION_URL=VISION, LLM_REASONING_URL=REASONING,
                           LLM_API_KEY=SimpleNamespace(get_secret_value=lambda: KEY))


def events(directory):
    return [json.loads(line) for line in (directory / "vision_events.jsonl").read_text().splitlines()]


def request_payload(meal_id=None):
    return {"mealId": meal_id or str(uuid4()), "imageUrl": IMAGE}


def observe(directory, handler, *, url=ENDPOINT):
    api = FastAPI()

    @api.post("/analyze-meal")
    @api.post("/short-feedback")
    async def analyze(request: Request):
        body = await request.json()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await client.post(url, json=body, headers={"Authorization": "Bearer " + KEY})
        return Response(response.content, status_code=response.status_code, media_type="application/json")

    return LiveVisionCapture(api, capture_dir=directory, settings_factory=settings)


async def invoke(app, payload=None, *, path="/analyze-meal"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=payload or request_payload())


def test_response_request_and_private_raw_capture_are_unchanged(tmp_path):
    raw = " ".join(["메뉴", KEY, VISION, REASONING, IMAGE])
    envelope = {"model": "gemini-flash", "usage": {"completion_tokens": 7},
                "choices": [{"finish_reason": "stop", "message": {"content": raw}}]}
    body = json.dumps(envelope, ensure_ascii=False).encode()
    payload = request_payload()
    received = []

    def handler(request):
        received.append(request)
        return httpx.Response(200, content=body)

    result = asyncio.run(invoke(observe(tmp_path, handler), payload))
    assert result.content == body
    assert json.loads(received[0].content) == payload
    assert received[0].headers["Authorization"] == "Bearer " + KEY
    rows = events(tmp_path)
    assert [r["event"] for r in rows] == ["request_started", "gateway_attempt_started",
                                         "gateway_attempt_finished", "request_finished"]
    assert all(r["meal_id"] == payload["mealId"] for r in rows[1:])
    attempt = rows[2]
    assert attempt["raw_text"] == "메뉴 " + " ".join(["[REDACTED]"] * 4)
    assert attempt["response_chars"] == len(raw)
    assert attempt["model"] == "gemini-flash"
    assert attempt["usage"] == {"completion_tokens": 7}
    assert rows[-1]["gateway_attempts"] == 1 and rows[-1]["status_code"] == 200
    stored = (tmp_path / "vision_events.jsonl").read_text()
    assert not any(secret in stored for secret in [KEY, VISION, REASONING, IMAGE])
    assert (tmp_path / "vision_events.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("status", [401, 429, 502])
def test_http_and_nonjson_responses_remain_identical(tmp_path, status):
    body = ("sensitive upstream error " + KEY).encode()
    result = asyncio.run(invoke(observe(tmp_path, lambda request: httpx.Response(status, content=body))))
    assert result.status_code == status and result.content == body
    record = events(tmp_path)[2]
    assert record["status_code"] == status and record["non_json_response"] is True
    assert record["raw_text"] is None
    assert KEY not in (tmp_path / "vision_events.jsonl").read_text()


@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadTimeout, asyncio.CancelledError])
def test_transport_errors_and_cancellation_remain_the_same(tmp_path, exception):
    error = exception(KEY)

    def handler(request):
        raise error

    with pytest.raises(exception) as captured:
        asyncio.run(invoke(observe(tmp_path, handler)))
    assert captured.value is error
    rows = events(tmp_path)
    assert rows[2]["error_type"] == exception.__name__
    assert rows[3]["gateway_attempts"] == 1
    assert KEY not in (tmp_path / "vision_events.jsonl").read_text()


@pytest.mark.parametrize("path,url", [("/short-feedback", ENDPOINT),
                                     ("/analyze-meal", REASONING + "/v1/chat/completions"),
                                     ("/analyze-meal", ENDPOINT + "?other=true")])
def test_only_exact_vision_posts_inside_analyze_are_captured(tmp_path, path, url):
    observed = observe(tmp_path, lambda req: httpx.Response(200, json={"ok": True}), url=url)
    assert asyncio.run(invoke(observed, path=path)).json() == {"ok": True}
    if path == "/short-feedback":
        assert not (tmp_path / "vision_events.jsonl").exists()
    else:
        assert [r["event"] for r in events(tmp_path)] == ["request_started", "request_finished"]
        assert events(tmp_path)[-1]["gateway_attempts"] == 0


def test_unobserved_post_error_is_never_retried_by_observer(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError(KEY)

    with pytest.raises(httpx.ConnectError):
        asyncio.run(invoke(observe(tmp_path, handler, url=REASONING)))
    assert len(calls) == 1
    assert events(tmp_path)[-1]["gateway_attempts"] == 0


def test_capture_failure_is_fixed_warning_and_does_not_change_response(tmp_path, caplog):
    directory = tmp_path / "not-a-directory"
    directory.write_text(KEY)
    result = asyncio.run(invoke(observe(directory, lambda req: httpx.Response(200, json={"ok": True}))))
    assert result.status_code == 200 and result.json() == {"ok": True}
    assert "live_vision_capture_write_failed" in caplog.text
    assert KEY not in caplog.text and str(directory) not in caplog.text


def test_concurrent_requests_keep_meal_context_and_per_request_attempts(tmp_path):
    async def handler(request):
        payload = json.loads(request.content)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"choices": [{"message": {"content": payload["mealId"]}}]})

    observed = observe(tmp_path, handler)
    payloads = [request_payload() for _ in range(4)]

    async def run():
        return await asyncio.gather(*(invoke(observed, payload) for payload in payloads))

    results = asyncio.run(run())
    assert all(result.status_code == 200 for result in results)
    rows = events(tmp_path)
    finishes = [row for row in rows if row["event"] == "gateway_attempt_finished"]
    assert len(finishes) == 4
    assert {row["gateway_sequence"] for row in finishes} == {1, 2, 3, 4}
    assert len({row["request_id"] for row in finishes}) == 4
    assert all(row["raw_text"] == row["meal_id"] and row["attempt"] == 1 for row in finishes)


def test_same_meal_multiple_be_requests_have_different_request_ids(tmp_path):
    observed = observe(tmp_path, lambda req: httpx.Response(200, json={"choices": []}))
    payload = request_payload()

    async def run():
        await invoke(observed, payload)
        await invoke(observed, payload)

    asyncio.run(run())
    starts = [r for r in events(tmp_path) if r["event"] == "gateway_attempt_started"]
    assert [r["gateway_sequence"] for r in starts] == [1, 2]
    assert all(r["meal_id"] == payload["mealId"] and r["attempt"] == 1 for r in starts)
    assert len({r["request_id"] for r in starts}) == 2


def test_uuid_validation_does_not_prevent_image_redaction(tmp_path):
    result = asyncio.run(invoke(observe(tmp_path, lambda req: httpx.Response(
        200, json={"choices": [{"message": {"content": IMAGE}}]})), request_payload("arbitrary name")))
    assert result.status_code == 200
    assert events(tmp_path)[2]["meal_id"] is None
    assert events(tmp_path)[2]["raw_text"] == "[REDACTED]"


def test_actual_production_app_is_wrapped_without_changing_its_middleware():
    from app.main import app as production
    from evals.live_vision_app import app
    assert app.wrapped is production
    assert not any("LiveVision" in str(middleware) for middleware in production.user_middleware)


@pytest.mark.parametrize("second_valid,status", [(True, 200), (False, 502)])
def test_production_parser_retry_and_normalization_are_unchanged(tmp_path, monkeypatch,
                                                               second_valid, status):
    from app import food_analyzer
    from app.main import app as production
    configured = settings()
    configured.LLM_MAX_RETRIES = 1
    configured.LLM_TIMEOUT_SEC = 5
    monkeypatch.setattr(food_analyzer, "get_settings", lambda: configured)
    raw = json.dumps({"items": [{"originalFoodName": "계란 프라이", "estimatedAmount": 80,
                               "unit": "g", "confidence": 0.8}]}, ensure_ascii=False)
    posts = []

    def handler(request):
        if request.method == "GET":
            assert str(request.url) == IMAGE
            return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})
        posts.append(request)
        assert str(request.url) == ENDPOINT
        valid = len(posts) == 2 and second_valid
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop" if valid else "length",
                                                     "message": {"content": raw if valid else "{"}}]})

    original_init = httpx.AsyncClient.__init__

    def transport_init(self, *args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", transport_init)
    wrapped = LiveVisionCapture(production, capture_dir=tmp_path, settings_factory=lambda: configured)
    result = asyncio.run(invoke(wrapped, {**request_payload(), "mealType": "LUNCH",
                                         "stage": "MAINTENANCE", "eatenAt": "2026-11-09T12:00:00+09:00"}))
    assert result.status_code == status
    assert len(posts) == 2 and posts[0].content == posts[1].content
    rows = events(tmp_path)
    attempts = [r for r in rows if r["event"] == "gateway_attempt_finished"]
    assert [r["attempt"] for r in attempts] == [1, 2]
    assert attempts[0]["raw_text"] == "{"
    assert rows[-1]["gateway_attempts"] == 2 and rows[-1]["status_code"] == status
    if second_valid:
        assert attempts[1]["raw_text"] == raw
        assert result.json()["items"][0]["originalFoodName"] == "달걀후라이"


def test_missing_capture_directory_does_not_install_observer():
    original = httpx.AsyncClient.post
    observed = observe(None, lambda req: httpx.Response(200, json={"ok": True}))
    assert asyncio.run(invoke(observed)).json() == {"ok": True}
    assert httpx.AsyncClient.post is original


def test_capture_does_not_follow_existing_symlink(tmp_path, caplog):
    target = tmp_path / "private-file"
    target.write_text("untouched")
    (tmp_path / "vision_events.jsonl").symlink_to(target)
    assert asyncio.run(invoke(observe(tmp_path, lambda req: httpx.Response(200, json={})))).status_code == 200
    assert target.read_text() == "untouched"
    assert "live_vision_capture_write_failed" in caplog.text
