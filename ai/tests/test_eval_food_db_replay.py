"""Saved-response integrity and output protection; no database or model calls."""
from copy import deepcopy

import pytest

from evals import replay_food_db


def _baseline():
    response = {"items": [{"originalFoodName": "피자", "candidateFoodRefId": None}]}
    samples = [{"response": deepcopy(response)}, {"response": deepcopy(response)}]
    return {
        "results": {
            "pizza/amount_accuracy": {"n": 2, "samples": deepcopy(samples)},
            "pizza/confidence_calibration": {"n": 2, "samples": deepcopy(samples)},
            "pizza/unrelated_axis": {"n": 2, "samples": deepcopy(samples)},
        }
    }


def test_preserves_identical_repetitions_without_cross_axis_double_counting():
    records = replay_food_db.extract_responses(_baseline())

    assert len(records) == 2
    assert [record["run"] for record in records] == [1, 2]
    assert records[0]["response"] == records[1]["response"]
    assert [record["case"] for record in records] == ["pizza", "pizza"]


@pytest.mark.parametrize(
    "corruption",
    ["missing_axis", "missing_sample", "missing_both_samples", "different_response", "different_n", "missing_response"],
)
def test_rejects_incomplete_or_misaligned_saved_responses(corruption):
    document = _baseline()
    results = document["results"]
    amount = results["pizza/amount_accuracy"]
    confidence = results["pizza/confidence_calibration"]
    if corruption == "missing_axis":
        del results["pizza/confidence_calibration"]
    elif corruption == "missing_sample":
        confidence["samples"].pop()
    elif corruption == "missing_both_samples":
        amount["samples"].pop()
        confidence["samples"].pop()
    elif corruption == "different_response":
        confidence["samples"][0]["response"]["items"] = []
    elif corruption == "different_n":
        confidence["n"] = 3
    elif corruption == "missing_response":
        del amount["samples"][0]["response"]

    with pytest.raises((ValueError, KeyError)):
        replay_food_db.extract_responses(document)


def test_rejects_baseline_without_saved_amount_responses():
    with pytest.raises(ValueError, match="No complete saved photo responses"):
        replay_food_db.extract_responses({"results": {}})


def test_cli_refuses_overwrite_before_any_database_access(tmp_path, monkeypatch):
    destination = tmp_path / "existing.json"
    destination.write_text("existing evidence", encoding="utf-8")
    monkeypatch.delenv("FOOD_MATCH_EVAL_DSN", raising=False)

    def forbidden_replay(*_args, **_kwargs):
        pytest.fail("Existing output must be rejected before attempting replay")

    monkeypatch.setattr(replay_food_db, "replay", forbidden_replay)
    with pytest.raises(SystemExit) as raised:
        replay_food_db.main([
            "--baseline", str(tmp_path / "unused.json"),
            "--backend-root", str(tmp_path / "unused_backend"),
            "--output", str(destination),
        ])

    assert raised.value.code == 2
    assert destination.read_text(encoding="utf-8") == "existing evidence"
