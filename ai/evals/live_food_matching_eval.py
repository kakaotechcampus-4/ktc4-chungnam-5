"""Exercise the real local upload/queue/vision/DB path, without confirming meals.

Run from ``ai/`` with ``python -m evals.live_food_matching_eval --help``.
``--preflight`` performs GET requests only. ``--run`` creates one synthetic user
and the explicitly planned meals. It does not retry writes, edit foods, confirm
meals, call feedback, or infer food IDs from display names. Public API evidence
cannot expose original AI names, selected food IDs, or gateway attempt counts;
collect those separately, scoped to the recorded synthetic meal IDs.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "ai/evals/fixtures/food_name_matching/manifest.json"
KST = timezone(timedelta(hours=9))
REDACTED = "[REDACTED]"
TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}
# DrugName.WEGOVY is the Python member name; the public API enum value is Korean.
SYNTHETIC_MEDICATION = {"drugName": "위고비", "doseMg": 2.4}
UNOBSERVABLE = ["originalFoodName", "candidateFoodRefId", "selectedFoodRefId",
                "rawModelResponse", "modelVersion", "gatewayAttemptCount", "queueAttempts"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def local_base(value: str) -> str:
    """Allow only explicit loopback origins; never accept secrets in an URL."""
    parsed = urlsplit(value)
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        is_loopback = parsed.hostname == "localhost"
    if (parsed.scheme not in {"http", "https"} or not is_loopback
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Only a credential-free loopback HTTP origin is supported")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Invalid local port") from None
    return value.rstrip("/")


def sanitize(value: Any) -> Any:
    """Keep useful API bodies while removing nested URLs, credentials and headers.

    Never save HTTP request/response headers or exception messages. The generic
    string patterns also protect malformed/error bodies with embedded URLs.
    Frozen, public fixture provenance is saved separately and is not a response.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            sensitive = (any(word in normalized for word in (
                "authorization", "password", "secret", "apikey", "cookie", "token", "headers"))
                or normalized.endswith(("url", "uri"))
                or normalized in {"imagekey", "storagekey", "xuserid"})
            result[str(sanitize(str(key)))] = REDACTED if sensitive else sanitize(item)
        return result
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(?:[a-z][a-z0-9+.-]*://|data:|/media/)[^\s\"'<>]+", REDACTED, value)
        value = re.sub(r"(?i)\bBearer\s+\S+", REDACTED, value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", REDACTED, value)
        value = re.sub(r"(?i)\b(api[_-]?key|authorization|token|password|secret)\s*[:=]\s*\S+",
                       REDACTED, value)
    return value


def write_new(path: Path, value: Any) -> None:
    """Every evidence file is append-once, including failures and the protocol."""
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def progress(task: dict, outcome: str, elapsed: float = 0) -> None:
    """Flush bounded progress without identities, URLs or request bodies."""
    print(json.dumps({"case_id": task["case_id"], "run": task["run"], "outcome": outcome,
                      "elapsed_seconds": round(elapsed, 3)}, ensure_ascii=False), flush=True)


def prepare_plan(manifest_path: Path, *, root: Path = ROOT, only: list[str] | None = None,
                 n: int = 1, max_submissions: int = 6) -> tuple[dict, list[dict]]:
    if isinstance(n, bool) or not 1 <= n <= 10:
        raise ValueError("n must be between 1 and 10")
    if isinstance(max_submissions, bool) or not 1 <= max_submissions <= 60:
        raise ValueError("max_submissions must be between 1 and 60")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifest must contain cases")
    identifiers = [case.get("case_id") for case in cases]
    if (any(not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9_-]+", identifier)
            for identifier in identifiers) or len(set(identifiers)) != len(identifiers)):
        raise ValueError("Invalid or duplicate case IDs")
    selected = set(only or identifiers)
    if not selected.issubset(identifiers):
        raise ValueError("Unknown case in --only")
    chosen = [deepcopy(case) for case in cases if case["case_id"] in selected]
    if len(chosen) * n > max_submissions:
        raise ValueError("Plan exceeds --max-submissions; explicitly set a larger bound")
    tasks = []
    for case in chosen:
        if case.get("meal_type") not in {"BREAKFAST", "LUNCH", "DINNER", "SNACK"}:
            raise ValueError("Unsupported meal type")
        relative = Path(case["image_path"])
        image = (root / relative).resolve()
        if relative.is_absolute() or not image.is_relative_to(root.resolve()):
            raise ValueError("Fixture must be inside the repository")
        if image.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("Unsupported fixture format")
        data = image.read_bytes()
        if not data or len(data) > 10 * 1024 * 1024:
            raise ValueError("Fixture size violates the upload API contract")
        if sha(data) != case.get("image_sha256"):
            raise ValueError("Fixture hash differs from the frozen manifest")
        for run in range(1, n + 1):
            tasks.append({"case_id": case["case_id"], "run": run,
                          "meal_type": case["meal_type"], "image_path": case["image_path"],
                          "image_sha256": case["image_sha256"]})
    protocol = {"manifest_sha256": sha(raw), "frozen_manifest": manifest,
                "selected_cases": chosen, "repetitions": n, "planned_submissions": len(tasks),
                "max_submissions": max_submissions, "schedule": deepcopy(tasks)}
    return protocol, tasks


class EvidenceClient:
    def __init__(self, client: httpx.Client, output: Path):
        self.client = client
        self.output = output
        self.requests = 0

    def request(self, method: str, origin: str, path: str, *, label: str,
                timeout: float = 15, **kwargs) -> dict:
        self.requests += 1
        started = time.monotonic()
        record = {"sequence": self.requests, "operation": label, "method": method,
                  "path": path, "started_at": datetime.now(timezone.utc).isoformat()}
        try:
            response = self.client.request(method, origin + path, timeout=timeout, **kwargs)
            record["status_code"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = {"non_json_body": True, "byte_length": len(response.content)}
            record["body"] = sanitize(body)
        except httpx.RequestError as exc:
            record["transport_error"] = type(exc).__name__
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_new(self.output / f"request_{self.requests:04d}.json", record)
        return record


def payload(record: dict, statuses: set[int]) -> dict:
    body = record.get("body")
    if (record.get("status_code") not in statuses or not isinstance(body, dict)
            or body.get("success") is not True or not isinstance(body.get("data"), dict)):
        raise ValueError("API request failed; inspect the sanitized request evidence")
    return body["data"]


def preflight(client: EvidenceClient, api_base: str, ai_base: str) -> dict:
    checks = []
    for origin, path, label in [(api_base, "/health", "api_health"),
                                (api_base, "/health/ready", "database_readiness"),
                                (ai_base, "/health", "ai_health")]:
        record = client.request("GET", origin, path, label=label, timeout=5)
        body = record.get("body")
        passed = (record.get("status_code") == 200 and isinstance(body, dict)
                  and body.get("status") == "ok")
        checks.append({"check": label, "passed": passed, "request_sequence": record["sequence"]})
    return {"passed": all(check["passed"] for check in checks), "checks": checks,
            "model_calls": 0, "limitations": ["Does not prove worker or gateway connectivity",
                                              "DB readiness does not prove the nutrition seed is loaded"]}


def _valid_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("API identifier must be a UUID")
    return str(uuid.UUID(value))


def create_synthetic_user(client: EvidenceClient, api_base: str, today: str) -> str:
    body = {"nickname": f"food-eval-{uuid.uuid4().hex[:12]}", "heightCm": 170,
            "weightKg": 65, "baselineIntake": 600}
    user = payload(client.request("POST", api_base, "/api/v1/users/profile", label="create_synthetic_user",
                                  json=body), {201})
    user_id = _valid_uuid(user.get("userId"))
    # Save the new identity even if the following medication request fails.
    write_new(client.output / "synthetic_user.json", {"user_id": user_id, "synthetic": True})
    # This is a synthetic test fixture, not a real person's medication record.
    medication = payload(client.request("POST", api_base, "/api/v1/medications",
                                        label="create_synthetic_medication",
                                        headers={"X-User-Id": user_id},
                                        json={**SYNTHETIC_MEDICATION, "startedAt": today}), {200})
    if medication.get("stage") != "MAINTENANCE":
        raise ValueError("Backend did not derive the planned MAINTENANCE stage")
    return user_id


def poll_meal(client: EvidenceClient, api_base: str, user_id: str, meal_id: str, *,
              started: float, deadline_seconds: float, poll_interval: float, max_polls: int,
              advertised_timeout_ms: Any, clock=time.monotonic, sleep=time.sleep) -> dict:
    deadline = started + deadline_seconds
    observations = []
    final = None
    outcome = "POLL_TIMEOUT"
    for _ in range(max_polls):
        remaining = deadline - clock()
        if remaining <= 0:
            break
        record = client.request("GET", api_base, f"/api/v1/meals/{meal_id}",
                                label="poll_meal", headers={"X-User-Id": user_id},
                                timeout=min(15, remaining))
        observation = {"request_sequence": record["sequence"],
                       "elapsed_seconds": round(clock() - started, 3),
                       "status_code": record.get("status_code")}
        try:
            detail = payload(record, {200})
        except ValueError:
            observation["transport_error"] = record.get("transport_error")
            observations.append(observation)
            if not record.get("transport_error") and record.get("status_code") not in TRANSIENT_HTTP:
                outcome = "POLL_ERROR"
                break
        else:
            observation["status"] = detail.get("status")
            observations.append(observation)
            final = detail
            if detail.get("mealId") != meal_id:
                outcome = "INVALID_DETAIL"
                break
            if detail.get("status") in {"REVIEW_REQUIRED", "FAILED"}:
                outcome = detail["status"]
                break
            if detail.get("status") != "ANALYZING":
                outcome = "UNEXPECTED_STATUS"
                break
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(poll_interval, remaining))
    elapsed = round(clock() - started, 3)
    advertised = (advertised_timeout_ms / 1000 if isinstance(advertised_timeout_ms, (int, float))
                  and not isinstance(advertised_timeout_ms, bool) and advertised_timeout_ms > 0 else None)
    return {"outcome": outcome, "elapsed_seconds": elapsed, "polls": observations,
            "final_detail": final, "api_advertised_timeout_ms": advertised_timeout_ms,
            "ready_within_api_advertised_timeout": (
                outcome == "REVIEW_REQUIRED" and elapsed <= advertised if advertised is not None else None),
            "pending_work_may_continue": outcome not in {"REVIEW_REQUIRED", "FAILED"}}


def collect(client: EvidenceClient, api_base: str, tasks: list[dict], *, root: Path,
            output: Path, deadline_seconds: float, poll_interval: float, max_polls: int) -> dict:
    now = datetime.now(KST)
    user_id = create_synthetic_user(client, api_base, now.date().isoformat())
    results = []
    for index, task in enumerate(tasks, 1):
        result = {**task, "sequence": index, "observability": {field: "not_exposed_by_public_api"
                                                                  for field in UNOBSERVABLE}}
        image = root / task["image_path"]
        data = image.read_bytes()
        if sha(data) != task["image_sha256"]:
            result.update(outcome="FIXTURE_CHANGED", pending_work_may_continue=False)
            results.append(result)
            write_new(output / f"meal_{index:03d}.json", result)
            progress(task, result["outcome"])
            break
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp"}[image.suffix.lower()]
        request_fields = {"mealType": task["meal_type"], "eatenAt": now.isoformat(),
                          "satietyBeforePct": "50"}
        result["request"] = {**request_fields, "image_sha256": task["image_sha256"],
                             "image_bytes": len(data), "content_type": mime}
        progress(task, "SUBMITTING")
        started = time.monotonic()
        response = client.request("POST", api_base, "/api/v1/meals", label="upload_meal",
                                  headers={"X-User-Id": user_id}, data=request_fields,
                                  files={"image": (image.name, data, mime)})
        result["upload_request_sequence"] = response["sequence"]
        try:
            created = payload(response, {202})
            meal_id = _valid_uuid(created.get("mealId"))
            if created.get("status") != "ANALYZING":
                raise ValueError("Unexpected create status")
        except ValueError:
            # A failed/ambiguous POST is never replayed: it may already have enqueued work.
            result.update(outcome="UPLOAD_ERROR", pending_work_may_continue=True)
        else:
            result["meal_id"] = meal_id
            result["create_response"] = created
            # The API's advertised polling budget starts after upload acceptance.
            result.update(poll_meal(client, api_base, user_id, meal_id, started=time.monotonic(),
                                    deadline_seconds=deadline_seconds, poll_interval=poll_interval,
                                    max_polls=max_polls, advertised_timeout_ms=created.get("timeoutMs")))
        result["upload_to_observation_seconds"] = round(time.monotonic() - started, 3)
        results.append(result)
        write_new(output / f"meal_{index:03d}.json", result)
        progress(task, result["outcome"], result["upload_to_observation_seconds"])
        if result["pending_work_may_continue"]:
            break
    outcomes = {outcome: sum(result["outcome"] == outcome for result in results)
                for outcome in sorted({result["outcome"] for result in results})}
    return {"planned_meals": len(tasks), "observed_meals": len(results),
            "upload_attempts": sum("upload_request_sequence" in result for result in results),
            "unsubmitted_meals": len(tasks) - len(results), "outcomes": outcomes,
            "review_required_within_api_timeout": sum(
                result.get("ready_within_api_advertised_timeout") is True for result in results),
            "synthetic_user_id": user_id, "meals": results,
            "confirmed_meals": 0, "feedback_requests": 0,
            "gateway_attempt_count": None,
            "interpretation": "Completion and DB-derived nutrition are not food-matching accuracy. "
                              "Original model output and selected IDs need separate scoped evidence. "
                              "Long polling is diagnostic; inspect the API timeout metric separately."}


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    mode = command.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="GET-only checks, no users or model calls")
    mode.add_argument("--run", action="store_true", help="Create synthetic API data and invoke real vision")
    command.add_argument("--api-base", default="http://127.0.0.1:18000")
    command.add_argument("--ai-base", default="http://127.0.0.1:18002")
    command.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    command.add_argument("--only", action="append", help="Case ID; repeat or comma-separate to select a subset")
    command.add_argument("--n", type=int, default=1)
    command.add_argument("--max-submissions", type=int, default=6)
    command.add_argument("--poll-deadline", type=float, default=300)
    command.add_argument("--poll-interval", type=float, default=1.5)
    command.add_argument("--max-polls", type=int, default=200)
    command.add_argument("--output", "--out", type=Path, required=True,
                         help="New evidence directory; existing directories are rejected")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        api_base, ai_base = local_base(args.api_base), local_base(args.ai_base)
        if not math.isfinite(args.poll_deadline) or not 1 <= args.poll_deadline <= 600:
            raise ValueError("poll-deadline must be between 1 and 600 seconds")
        if not math.isfinite(args.poll_interval) or not .1 <= args.poll_interval <= 30:
            raise ValueError("poll-interval must be between 0.1 and 30 seconds")
        if not 1 <= args.max_polls <= 1000:
            raise ValueError("max-polls must be between 1 and 1000")
        only = [part for group in args.only or [] for part in group.split(",")]
        protocol, tasks = prepare_plan(args.manifest, only=only, n=args.n,
                                       max_submissions=args.max_submissions)
        protocol.update(created_at=datetime.now(timezone.utc).isoformat(),
                        mode="run" if args.run else "preflight", environment="isolated_local",
                        stop_at="REVIEW_REQUIRED", poll_deadline_seconds=args.poll_deadline,
                        poll_interval_seconds=args.poll_interval, max_polls_per_meal=args.max_polls,
                        maximum_client_http_requests=(3 + (2 + len(tasks) * (1 + args.max_polls)
                                                           if args.run else 0)),
                        synthetic_profile={"heightCm": 170, "weightKg": 65, "baselineIntake": 600},
                        synthetic_medication=deepcopy(SYNTHETIC_MEDICATION),
                        intended_stage="MAINTENANCE", satiety_before_pct=50,
                        polling_clock_starts="after the POST /meals response",
                        timing_note="Observed completion is bounded by GET intervals; the 30s metric "
                                    "uses the API budget, not the FE's backoff/overshoot schedule",
                        confirmed_meals=0, feedback_requests=0,
                        public_api_unobservable=UNOBSERVABLE,
                        model_budget_note="Upload count is not gateway call count; queue and AI retries apply")
        args.output.mkdir(parents=True, exist_ok=False)
        write_new(args.output / "protocol.json", protocol)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Preparation rejected: {type(exc).__name__}")
        return 2
    try:
        with httpx.Client(follow_redirects=False, trust_env=False) as http:
            client = EvidenceClient(http, args.output)
            health = preflight(client, api_base, ai_base)
            write_new(args.output / "preflight.json", health)
            if not health["passed"]:
                print("Preflight failed; no synthetic user or meal was submitted")
                return 1
            if args.preflight:
                print("Preflight passed; 0 model calls, worker/gateway remain unproven")
                return 0
            result = collect(client, api_base, tasks, root=ROOT, output=args.output,
                             deadline_seconds=args.poll_deadline, poll_interval=args.poll_interval,
                             max_polls=args.max_polls)
            result["client_http_requests"] = client.requests
            write_new(args.output / "analysis.json", result)
            print(json.dumps({"planned_meals": result["planned_meals"], "outcomes": result["outcomes"],
                              "unsubmitted_meals": result["unsubmitted_meals"],
                              "gateway_attempt_count": None}, ensure_ascii=False))
            return 0 if result["outcomes"] == {"REVIEW_REQUIRED": len(tasks)} else 1
    except Exception as exc:
        write_new(args.output / "aborted.json", {"error_type": type(exc).__name__,
                                                "pending_work_may_continue": args.run})
        print(f"Run stopped: {type(exc).__name__}; inspect sanitized evidence")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
