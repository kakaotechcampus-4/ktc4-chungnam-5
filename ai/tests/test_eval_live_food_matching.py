"""HTTP boundary tests for the opt-in live runner; these never call a model."""

import ast
from datetime import date
from decimal import Decimal
import enum
import json
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import pytest

from evals import live_food_matching_eval as evaluation


USER_ID = "92dd0b0b-c638-4e0a-a5a7-63c85ab6b0ea"
MEAL_ID = "b7ad0834-fd28-4c30-9667-a07ac33527b5"
API = "http://127.0.0.1:18000"


def manifest(tmp_path, count=1):
    cases = []
    for index in range(count):
        image = tmp_path / f"fixture_{index}.jpg"
        image.write_bytes(b"\xff\xd8\xfftest image" + bytes([index]))
        cases.append({"case_id": f"case_{index}", "split": "holdout", "meal_type": "LUNCH",
                      "image_path": image.name, "image_sha256": evaluation.sha(image.read_bytes()),
                      "targets": [{"label": "known dish", "acceptable_food_ref_ids": ["KNOWN"]}]})
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps({"cases": cases}))
    return source


def ok(data, status=200):
    return httpx.Response(status, json={"success": True, "data": data, "error": None})


def backend_medication_contract():
    """Execute the real schema/rule definitions without importing BE's DB stack.

    AI's dependencies intentionally exclude SQLAlchemy. Like test_eval_be_sync,
    read the repository's actual definitions, fail when missing, and never copy
    the enum values or stage boundaries into a test double.
    """
    selected = {
        "models/enums.py": {"DrugName", "MedicationStage"},
        "schemas/base.py": {"to_camel", "CamelModel"},
        "schemas/medication.py": {"MedicationRegisterRequest"},
        "services/medication.py": {"DOSE_LADDERS", "ESCALATION_DOSES", "MAINTENANCE_STREAK",
                                  "judge_stage"},
    }
    namespace = {"enum": enum, "BaseModel": BaseModel, "ConfigDict": ConfigDict,
                 "Field": Field, "Decimal": Decimal, "date": date, "Final": Final}
    for relative, names in selected.items():
        path = evaluation.ROOT / "backend/app" / relative
        nodes = []
        for node in ast.parse(path.read_text()).body:
            name = (node.name if isinstance(node, (ast.ClassDef, ast.FunctionDef)) else
                    node.target.id if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                    else None)
            if name in names:
                nodes.append(node)
        assert len(nodes) == len(names), f"Missing BE definitions: {relative}"
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["MedicationRegisterRequest"], namespace["judge_stage"]


def test_synthetic_medication_uses_actual_be_schema_and_first_dose_stage():
    schema, judge_stage = backend_medication_contract()
    body = {**evaluation.SYNTHETIC_MEDICATION, "startedAt": "2026-11-09"}
    parsed = schema.model_validate(body)
    assert parsed.drug_name.value == body["drugName"]
    assert judge_stage(parsed.drug_name, parsed.dose_mg,
                       previous_different_dose_mg=None, same_dose_streak=1).value == "MAINTENANCE"
    # run1 failed before any model call because the enum member name is not a wire value.
    with pytest.raises(ValidationError):
        schema.model_validate({**body, "drugName": parsed.drug_name.name})


class Clock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


@pytest.mark.parametrize("origin", ["http://127.0.0.1:18000", "http://localhost", "https://[::1]:123"])
def test_local_base_allows_only_loopback(origin):
    assert evaluation.local_base(origin + "/") == origin


@pytest.mark.parametrize("origin", ["https://example.com", "http://127.0.0.1.example.com",
    "http://0.0.0.0:8000", "http://192.168.1.2:8000", "http://user:secret@localhost",
    "http://localhost?api_key=secret", "http://localhost/#secret", "http://localhost/api/v1",
    "file:///tmp/server", "http://localhost:bad"])
def test_local_base_rejects_external_and_credential_origins(origin):
    with pytest.raises(ValueError):
        evaluation.local_base(origin)


def test_manifest_subset_labels_hash_and_bounds_are_frozen(tmp_path):
    source = manifest(tmp_path, 3)
    before = source.read_bytes()
    protocol, tasks = evaluation.prepare_plan(source, root=tmp_path, only=["case_1"], n=2)
    assert len(tasks) == 2
    assert [task["run"] for task in tasks] == [1, 2]
    assert protocol["selected_cases"][0]["targets"][0]["acceptable_food_ref_ids"] == ["KNOWN"]
    assert protocol["manifest_sha256"] == evaluation.sha(before)
    assert source.read_bytes() == before
    tasks[0]["case_id"] = "changed"
    assert protocol["schedule"][0]["case_id"] == "case_1"


@pytest.mark.parametrize("option", [{"n": 0}, {"n": 11}, {"n": True}, {"max_submissions": 0},
                                   {"max_submissions": 61}, {"n": 7}, {"only": ["missing"]}])
def test_reject_implicit_unbounded_or_unknown_scope(tmp_path, option):
    with pytest.raises(ValueError):
        evaluation.prepare_plan(manifest(tmp_path), root=tmp_path, **option)


def test_reject_changed_photo_and_path_escape(tmp_path):
    source = manifest(tmp_path)
    (tmp_path / "fixture_0.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        evaluation.prepare_plan(source, root=tmp_path)
    value = json.loads(source.read_text())
    value["cases"][0]["image_path"] = "../outside.jpg"
    source.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="inside"):
        evaluation.prepare_plan(source, root=tmp_path)


def test_recursive_response_redaction_preserves_nutrition():
    value = {"data": {"imageUrl": "http://private/image?token=secret", "imageKey": "meals/private",
                       "headers": {"X-User-Id": "secret"}, "api_key": "secret",
                       "items": [{"displayName": "달걀후라이", "nutrition": {"fiberG": None,
                                                                                     "sodiumMg": 20}}]},
             "error": "request http://private/key?q=secret failed; Bearer secret sk-secret",
             "nested": [{"refresh_token": "secret", "message": "password=secret"}]}
    redacted = evaluation.sanitize(value)
    assert "secret" not in json.dumps(redacted)
    assert "http://" not in json.dumps(redacted)
    assert redacted["data"]["items"] == value["data"]["items"]
    assert value["data"]["imageKey"] == "meals/private"


def test_write_new_refuses_overwrite(tmp_path):
    target = tmp_path / "evidence.json"
    evaluation.write_new(target, {"first": True})
    with pytest.raises(FileExistsError):
        evaluation.write_new(target, {"second": True})
    assert json.loads(target.read_text()) == {"first": True}


def test_preflight_is_get_only_and_no_worker_claim(tmp_path):
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "ok"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = evaluation.EvidenceClient(http, tmp_path)
        result = evaluation.preflight(client, API, "http://127.0.0.1:18002")
    assert result["passed"] is True and result["model_calls"] == 0
    assert calls == [("GET", "/health"), ("GET", "/health/ready"), ("GET", "/health")]
    assert any("worker" in note for note in result["limitations"])


def test_transport_errors_never_log_exception_url_or_headers(tmp_path):
    def handle(request):
        raise httpx.ConnectError("private http://private?token=secret", request=request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = evaluation.EvidenceClient(http, tmp_path)
        record = client.request("POST", API, "/api/v1/meals", label="upload",
                                headers={"X-User-Id": "secret"})
    assert record["transport_error"] == "ConnectError"
    assert "secret" not in (tmp_path / "request_0001.json").read_text()
    assert "private" not in (tmp_path / "request_0001.json").read_text()


def test_non_json_error_body_is_not_saved(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500, text="secret"))) as http:
        record = evaluation.EvidenceClient(http, tmp_path).request("GET", API, "/health", label="health")
    assert record["body"] == {"non_json_body": True, "byte_length": 6}


@pytest.mark.parametrize("final_status", ["REVIEW_REQUIRED", "FAILED"])
def test_poll_records_terminal_state_without_confirming(tmp_path, final_status):
    calls = []
    clock = Clock()
    def handle(request):
        calls.append(request)
        return ok({"mealId": MEAL_ID, "status": "ANALYZING" if len(calls) == 1 else final_status,
                   "imageUrl": "/media/private", "items": []})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = evaluation.poll_meal(evaluation.EvidenceClient(http, tmp_path), API, USER_ID, MEAL_ID,
                                      started=0, deadline_seconds=300, poll_interval=1.5, max_polls=200,
                                      advertised_timeout_ms=30000, clock=clock.now, sleep=clock.sleep)
    assert result["outcome"] == final_status
    assert len(calls) == 2
    assert all(request.method == "GET" for request in calls)
    assert result["final_detail"]["imageUrl"] == evaluation.REDACTED
    assert result["pending_work_may_continue"] is False
    assert result["ready_within_api_advertised_timeout"] is (final_status == "REVIEW_REQUIRED")


def test_diagnostic_success_after_api_deadline_is_not_fast_success(tmp_path):
    clock = Clock()
    def handle(request):
        clock.value = 40
        return ok({"mealId": MEAL_ID, "status": "REVIEW_REQUIRED", "items": []})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = evaluation.poll_meal(evaluation.EvidenceClient(http, tmp_path), API, USER_ID, MEAL_ID,
                                      started=0, deadline_seconds=300, poll_interval=1.5, max_polls=200,
                                      advertised_timeout_ms=30000, clock=clock.now, sleep=clock.sleep)
    assert result["outcome"] == "REVIEW_REQUIRED"
    assert result["ready_within_api_advertised_timeout"] is False


@pytest.mark.parametrize("cap,deadline,expected_calls", [(2, 300, 2), (200, 3, 2)])
def test_poll_has_both_request_and_time_bounds(tmp_path, cap, deadline, expected_calls):
    clock = Clock()
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: ok({"mealId": MEAL_ID, "status": "ANALYZING"}))) as http:
        client = evaluation.EvidenceClient(http, tmp_path)
        result = evaluation.poll_meal(client, API, USER_ID, MEAL_ID, started=0, deadline_seconds=deadline,
                                      poll_interval=1.5, max_polls=cap, advertised_timeout_ms=30000,
                                      clock=clock.now, sleep=clock.sleep)
    assert client.requests == expected_calls
    assert result["outcome"] == "POLL_TIMEOUT" and result["pending_work_may_continue"] is True


@pytest.mark.parametrize("first_status,expected_calls", [(503, 2), (429, 2), (401, 1), (404, 1)])
def test_poll_retries_only_transient_read_errors(tmp_path, first_status, expected_calls):
    counter = 0
    clock = Clock()
    def handle(request):
        nonlocal counter
        counter += 1
        return (httpx.Response(first_status, json={"success": False}) if counter == 1 else
                ok({"mealId": MEAL_ID, "status": "REVIEW_REQUIRED", "items": []}))
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = evaluation.poll_meal(evaluation.EvidenceClient(http, tmp_path), API, USER_ID, MEAL_ID,
                                      started=0, deadline_seconds=300, poll_interval=1.5, max_polls=200,
                                      advertised_timeout_ms=30000, clock=clock.now, sleep=clock.sleep)
    assert counter == expected_calls
    assert result["outcome"] == ("REVIEW_REQUIRED" if expected_calls == 2 else "POLL_ERROR")


def test_live_flow_uses_actual_upload_fields_and_keeps_missing_nutrients(tmp_path, capsys):
    source = manifest(tmp_path)
    _, tasks = evaluation.prepare_plan(source, root=tmp_path)
    calls = []
    def handle(request):
        calls.append(request)
        if request.url.path == "/api/v1/users/profile":
            assert json.loads(request.content)["weightKg"] == 65
            return ok({"userId": USER_ID}, 201)
        assert request.headers["X-User-Id"] == USER_ID
        if request.url.path == "/api/v1/medications":
            schema, judge_stage = backend_medication_contract()
            parsed = schema.model_validate_json(request.content)
            assert judge_stage(parsed.drug_name, parsed.dose_mg,
                               previous_different_dose_mg=None, same_dose_streak=1).value == "MAINTENANCE"
            return ok({"stage": "MAINTENANCE"})
        if request.method == "POST":
            assert request.url.path == "/api/v1/meals"
            assert request.headers["Content-Type"].startswith("multipart/form-data")
            assert b'name="image"' in request.content and b'name="mealType"' in request.content
            return ok({"mealId": MEAL_ID, "status": "ANALYZING", "timeoutMs": 30000,
                       "pollIntervalMs": 1500}, 202)
        return ok({"mealId": MEAL_ID, "status": "REVIEW_REQUIRED", "imageUrl": "/media/private",
                   "items": [{"displayName": "달걀후라이", "amount": 100, "unit": "g",
                              "confidence": .9, "matched": True, "nutritionSource": "PUBLIC_DB",
                              "nutrition": {"proteinG": 12, "fiberG": None}}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = evaluation.collect(evaluation.EvidenceClient(http, tmp_path), API, tasks,
                                     root=tmp_path, output=tmp_path, deadline_seconds=300,
                                     poll_interval=1.5, max_polls=200)
    assert len(calls) == 4
    assert result["outcomes"] == {"REVIEW_REQUIRED": 1}
    assert result["confirmed_meals"] == result["feedback_requests"] == 0
    assert result["gateway_attempt_count"] is None
    meal = result["meals"][0]
    assert meal["final_detail"]["items"][0]["nutrition"]["fiberG"] is None
    assert meal["observability"]["originalFoodName"] == "not_exposed_by_public_api"
    assert meal["observability"]["selectedFoodRefId"] == "not_exposed_by_public_api"
    assert "accuracy" not in result
    progress = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["outcome"] for line in progress] == ["SUBMITTING", "REVIEW_REQUIRED"]
    assert all(set(line) == {"case_id", "run", "outcome", "elapsed_seconds"} for line in progress)


def test_ambiguous_upload_is_not_retried_and_stops_remaining_photos(tmp_path):
    source = manifest(tmp_path, 2)
    _, tasks = evaluation.prepare_plan(source, root=tmp_path)
    paths = []
    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v1/users/profile":
            return ok({"userId": USER_ID}, 201)
        if request.url.path == "/api/v1/medications":
            return ok({"stage": "MAINTENANCE"})
        raise httpx.ReadTimeout("private secret", request=request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = evaluation.collect(evaluation.EvidenceClient(http, tmp_path), API, tasks,
                                     root=tmp_path, output=tmp_path, deadline_seconds=300,
                                     poll_interval=1.5, max_polls=200)
    assert paths.count("/api/v1/meals") == 1
    assert result["outcomes"] == {"UPLOAD_ERROR": 1}
    assert result["unsubmitted_meals"] == 1


def test_main_requires_explicit_mode_and_new_output(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        evaluation.main(["--output", str(tmp_path / "new")])
    def no_network(*args, **kwargs):
        pytest.fail("Existing output should be rejected before any network request")
    monkeypatch.setattr(httpx, "Client", no_network)
    assert evaluation.main(["--preflight", "--output", str(tmp_path)]) == 2


def test_protocol_is_written_before_any_request(tmp_path, monkeypatch):
    output = tmp_path / "run"
    original = httpx.Client
    def handle(request):
        assert (output / "protocol.json").is_file()
        protocol = json.loads((output / "protocol.json").read_text())
        assert protocol["planned_submissions"] == 6
        return httpx.Response(200, json={"status": "ok"})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle)))
    assert evaluation.main(["--preflight", "--output", str(output)]) == 0
    assert not (output / "synthetic_user.json").exists()
