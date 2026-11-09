"""Scope and comparison regression tests; no DB, containers or model calls."""

from copy import deepcopy
import json
import uuid

import pytest

from evals import inspect_live_food_db as evaluation

USER = "92dd0b0b-c638-4e0a-a5a7-63c85ab6b0ea"
MEAL = "b7ad0834-fd28-4c30-9667-a07ac33527b5"
ITEM = "f797a74b-30d5-4624-a786-17c7ad334c17"


def frozen_run(tmp_path):
    identity = {"user_id": USER, "synthetic": True}
    record = {"meal_id": MEAL, "case_id": "test", "run": 1,
              "create_response": {"mealId": MEAL, "status": "ANALYZING"},
              "final_detail": {"mealId": MEAL, "status": "REVIEW_REQUIRED", "items": []},
              "outcome": "REVIEW_REQUIRED", "pending_work_may_continue": False}
    (tmp_path / "synthetic_user.json").write_text(json.dumps(identity))
    (tmp_path / "meal_001.json").write_text(json.dumps(record))
    (tmp_path / "analysis.json").write_text(json.dumps({"synthetic_user_id": USER, "meals": [record]}))
    return record


def test_scope_freezes_all_evidence_hashes(tmp_path):
    record = frozen_run(tmp_path)
    user, records, hashes = evaluation.load_scope(tmp_path)
    assert user == USER and records == [record]
    assert set(hashes) == {"synthetic_user.json", "meal_001.json", "analysis.json"}
    assert all(len(value) == 64 for value in hashes.values())


@pytest.mark.parametrize("mutation", ["nonsynthetic", "bad_uuid", "duplicate", "not_frozen",
                                     "wrong_final_id", "wrong_create_status", "summary_mismatch"])
def test_scope_rejects_ambiguous_or_unrelated_evidence(tmp_path, mutation):
    record = frozen_run(tmp_path)
    if mutation == "nonsynthetic":
        (tmp_path / "synthetic_user.json").write_text(json.dumps({"user_id": USER, "synthetic": False}))
    elif mutation == "bad_uuid":
        record["meal_id"] = "not-a-uuid"
    elif mutation == "duplicate":
        (tmp_path / "meal_002.json").write_text(json.dumps(record))
    elif mutation == "not_frozen":
        record["pending_work_may_continue"] = True
    elif mutation == "wrong_final_id":
        record["final_detail"]["mealId"] = str(uuid.uuid4())
    elif mutation == "wrong_create_status":
        record["create_response"]["status"] = "EVALUATED"
    else:
        (tmp_path / "analysis.json").write_text(json.dumps({"synthetic_user_id": USER, "meals": []}))
    (tmp_path / "meal_001.json").write_text(json.dumps(record))
    with pytest.raises(ValueError):
        evaluation.load_scope(tmp_path)


def test_owned_scope_accepts_uuid_objects():
    evaluation.assert_owned_scope(USER, {MEAL}, [{"id": uuid.UUID(MEAL), "user_id": uuid.UUID(USER)}])


@pytest.mark.parametrize("rows", [[], [{"id": MEAL, "user_id": MEAL}],
    [{"id": USER, "user_id": USER}], [{"id": MEAL, "user_id": USER}, {"id": MEAL, "user_id": USER}]])
def test_owned_scope_rejects_missing_other_owner_or_duplicate(rows):
    with pytest.raises(ValueError):
        evaluation.assert_owned_scope(USER, {MEAL}, rows)


def test_public_ids_must_match_database_exactly():
    assert evaluation.index_public_items([{"itemId": ITEM}], {ITEM}) == {ITEM: {"itemId": ITEM}}
    for public in ([], [{"itemId": USER}], [{"itemId": ITEM}, {"itemId": ITEM}]):
        with pytest.raises(ValueError):
            evaluation.index_public_items(public, {ITEM})


def public_item(nutrition=None):
    return {"itemId": ITEM, "displayName": "달걀_부침", "amount": 100.0, "unit": "g",
            "confidence": .9, "matched": nutrition is not None,
            "nutritionSource": "PUBLIC_DB" if nutrition is not None else None,
            "userConfirmed": False, "nutrition": nutrition}


def compare(public, nutrition=None):
    return evaluation.compare_public_item(public, display_name="달걀_부침", amount="100.00", unit="g",
        confidence="0.900", nutrition=nutrition, nutrition_source="PUBLIC_DB" if nutrition is not None else None,
        user_confirmed=False)


def test_link_without_convertible_nutrition_is_correctly_unmatched():
    assert all(compare(public_item()).values())
    wrong = public_item()
    wrong["matched"] = True
    assert compare(wrong)["matched_means_nutrition_available"] is False


def test_null_nutrient_is_not_zero_and_decimal_format_does_not_matter():
    expected = {"kcal": 100, "proteinG": 1.2, "fatG": 0, "carbG": 0,
                "fiberG": None, "sodiumMg": 15}
    public = public_item(deepcopy(expected))
    public["nutrition"]["proteinG"] = 1.20
    assert all(compare(public, expected).values())
    public["nutrition"]["fiberG"] = 0
    assert compare(public, expected)["nutrition"] is False


@pytest.mark.parametrize("key,value", [("displayName", "다른 음식"), ("amount", 99.99),
    ("unit", "개"), ("confidence", .91), ("nutritionSource", "USER_INPUT"), ("userConfirmed", True)])
def test_public_mutations_are_detected(key, value):
    public = public_item()
    public[key] = value
    assert not all(compare(public).values())


@pytest.mark.parametrize("left,right", [(None, 0), (True, 1), (float("nan"), "NaN"),
                                       ("Infinity", "Infinity"), ("invalid", 0)])
def test_numbers_reject_false_equivalences(left, right):
    assert evaluation.same_number(left, right) is False


def test_missing_public_field_is_not_silently_accepted_as_null():
    public = public_item()
    del public["nutrition"]
    assert compare(public)["public_fields_present"] is False


def test_raw_evidence_redaction_keeps_names_and_nutrients():
    raw = {"modelVersion": "real-model", "item": {"originalFoodName": "달걀_부침", "estimatedAmount": 100,
           "imageUrl": "https://private/photo", "apiKey": "secret"}, "headers": {"secret": "secret"}}
    clean = evaluation.sanitize(raw)
    assert clean["item"]["originalFoodName"] == "달걀_부침"
    assert "secret" not in json.dumps(clean) and "https://" not in json.dumps(clean)
