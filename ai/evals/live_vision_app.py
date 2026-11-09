"""Evaluation-only observer around the unchanged production ASGI app.

Run ``uvicorn evals.live_vision_app:app`` with ``AI_LIVE_CAPTURE_DIR`` pointing
at a private writable evidence directory. No gateway proxy, request rewrite,
extra model call, or production log of response text is introduced. JSONL
start/end events preserve attempted calls even when the AI deadline cancels one.
The observer adds local file I/O, so timings include that small instrumentation
overhead. This module is mounted only into the isolated evaluation container.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count
import json
import logging
import os
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import httpx

from app.config import get_settings
from app.main import app as production_app
from evals.food_name_ab import gateway_attempt_evidence

logger = logging.getLogger(__name__)
_active: ContextVar[_Request | None] = ContextVar("live_vision_request", default=None)
_MAX_REQUEST_METADATA_BYTES = 65536


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Request:
    owner: LiveVisionCapture
    request_id: str
    meal_id: str | None = None
    image_url: str = ""
    attempts: int = 0

    def identity(self) -> dict:
        return {"process_id": self.owner.process_id, "request_id": self.request_id,
                "meal_id": self.meal_id}


class LiveVisionCapture:
    """Pure ASGI wrapper: context remains isolated across concurrent requests."""

    def __init__(self, wrapped, *, capture_dir: Path | None = None,
                 settings_factory=get_settings):
        self.wrapped = wrapped
        self.capture_dir = capture_dir
        self.settings_factory = settings_factory
        self.process_id = str(uuid4())
        self.sequence = count(1)

    def write(self, event: dict) -> None:
        """Append private evidence, never let a capture failure change AI behavior."""
        if self.capture_dir is None:
            return
        descriptor = None
        try:
            self.capture_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode()
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.capture_dir / "vision_events.jsonl", flags, 0o600)
            os.fchmod(descriptor, 0o600)
            # One append per event: the evaluation server runs one process/event loop.
            if os.write(descriptor, payload) != len(payload):
                raise OSError("incomplete capture write")
        except Exception:
            logger.warning("live_vision_capture_write_failed")
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    logger.warning("live_vision_capture_write_failed")

    def install(self) -> None:
        """Install once per process; inactive requests delegate without observation."""
        original = httpx.AsyncClient.post
        if getattr(original, "_live_vision_observer", False):
            return

        async def observed_post(client, *args, **kwargs):
            context = _active.get()
            if context is None:
                return await original(client, *args, **kwargs)
            try:
                settings = context.owner.settings_factory()
                endpoint = f"{settings.LLM_VISION_URL}/v1/chat/completions"
                url = args[0] if args else kwargs.get("url")
                matches = bool(settings.LLM_VISION_URL) and str(url) == endpoint
                secrets = [settings.LLM_API_KEY.get_secret_value(), settings.LLM_VISION_URL,
                           settings.LLM_REASONING_URL, context.image_url]

                def redact(value):
                    for secret in sorted(filter(None, secrets), key=len, reverse=True):
                        value = value.replace(secret, "[REDACTED]")
                    return value

            except Exception:
                logger.warning("live_vision_capture_setup_failed")
                return await original(client, *args, **kwargs)
            if not matches:
                return await original(client, *args, **kwargs)
            context.attempts += 1
            event = {**context.identity(), "attempt": context.attempts,
                     "gateway_sequence": next(context.owner.sequence),
                     "started_at_utc": _utc()}
            context.owner.write({**event, "event": "gateway_attempt_started"})
            started = perf_counter()
            try:
                response = await original(client, *args, **kwargs)
                event["status_code"] = response.status_code
                try:
                    body = response.json()
                except ValueError:
                    body = None
                    event["non_json_response"] = True
                try:
                    event.update(gateway_attempt_evidence(body, redact))
                except Exception:
                    logger.warning("live_vision_capture_metadata_failed")
                return response
            except BaseException as exc:
                # Cancellation is also an attempted POST; never persist exception text.
                event["error_type"] = type(exc).__name__
                raise
            finally:
                context.owner.write({**event, "event": "gateway_attempt_finished",
                                     "ended_at_utc": _utc(),
                                     "elapsed_seconds": round(perf_counter() - started, 6)})

        observed_post._live_vision_observer = True
        httpx.AsyncClient.post = observed_post

    async def __call__(self, scope, receive, send):
        if self.capture_dir is None:
            return await self.wrapped(scope, receive, send)
        self.install()
        if scope.get("type") != "http" or scope.get("path") != "/analyze-meal":
            return await self.wrapped(scope, receive, send)
        context = _Request(self, str(uuid4()))
        token = _active.set(context)
        chunks = bytearray()
        overflow = False
        status_code = None
        error_type = None
        started = perf_counter()
        self.write({**context.identity(), "event": "request_started", "started_at_utc": _utc()})

        async def observed_receive():
            nonlocal overflow
            message = await receive()
            if message.get("type") == "http.request" and not overflow:
                body = message.get("body", b"")
                if len(chunks) + len(body) > _MAX_REQUEST_METADATA_BYTES:
                    overflow = True
                    chunks.clear()
                else:
                    chunks.extend(body)
                    if not message.get("more_body", False):
                        try:
                            payload = json.loads(chunks)
                            image_url = payload.get("imageUrl")
                            context.image_url = image_url if isinstance(image_url, str) else ""
                            # BE identifiers are UUIDs; arbitrary user text is not an ID.
                            context.meal_id = str(UUID(payload.get("mealId", "")))
                        except (ValueError, TypeError, AttributeError):
                            pass
                        chunks.clear()
            return message

        async def observed_send(message):
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = message.get("status")
            await send(message)

        try:
            return await self.wrapped(scope, observed_receive, observed_send)
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            self.write({**context.identity(), "event": "request_finished",
                        "ended_at_utc": _utc(), "status_code": status_code,
                        "gateway_attempts": context.attempts, "error_type": error_type,
                        "elapsed_seconds": round(perf_counter() - started, 6)})
            _active.reset(token)


_capture = os.getenv("AI_LIVE_CAPTURE_DIR")
app = LiveVisionCapture(production_app, capture_dir=Path(_capture) if _capture else None)
