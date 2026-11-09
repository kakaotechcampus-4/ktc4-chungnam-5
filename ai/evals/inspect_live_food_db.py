"""Read-only DB evidence for meals recorded by live_food_matching_eval.

Run in a fresh BE-capable Python process. FOOD_MATCH_EVAL_DSN is read only from
the environment and is never emitted. No HTTP/model requests or DB writes occur.
The output reports pipeline consistency, not semantic food-recognition accuracy.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
import uuid

try:
    from .live_food_matching_eval import sanitize
except ImportError:  # direct script invocation, before importing the supplied BE
    from live_food_matching_eval import sanitize

NUTRIENTS = {"kcal", "proteinG", "fatG", "carbG", "fiberG", "sodiumMg"}
SOURCE_FILES = (
    "app/crud/food.py", "app/services/nutrition.py", "app/services/meal.py",
    "app/worker/jobs/analyze_meal.py", "app/models/food.py", "app/models/meal.py",
    "app/models/task.py", "app/models/enums.py", "app/schemas/nutrition.py",
)


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Evidence identifiers must be UUID strings")
    return str(uuid.UUID(value))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_scope(run_dir: Path) -> tuple[str, list[dict], dict[str, str]]:
    """Freeze the recorded identities before any database connection is opened."""
    hashes: dict[str, str] = {}

    def read(path: Path) -> Any:
        data = path.read_bytes()
        hashes[path.name] = _sha(data)
        return json.loads(data)

    identity = read(run_dir / "synthetic_user.json")
    if identity.get("synthetic") is not True:
        raise ValueError("Only explicitly synthetic test users can be inspected")
    user_id = _uuid(identity.get("user_id"))
    records = [read(path) for path in sorted(run_dir.glob("meal_*.json"))]
    if not records:
        raise ValueError("No recorded meal files")
    ids = set()
    for record in records:
        meal_id = _uuid(record.get("meal_id"))
        if meal_id in ids:
            raise ValueError("Duplicate meal in recorded scope")
        ids.add(meal_id)
        created = record.get("create_response", {})
        detail = record.get("final_detail", {})
        if (_uuid(created.get("mealId")) != meal_id or created.get("status") != "ANALYZING"
                or not isinstance(detail, dict) or _uuid(detail.get("mealId")) != meal_id
                or record.get("outcome") not in {"REVIEW_REQUIRED", "FAILED"}
                or detail.get("status") != record["outcome"]
                or record.get("pending_work_may_continue") is not False):
            raise ValueError("Recorded upload and final meal statuses are not frozen")
    analysis_path = run_dir / "analysis.json"
    if analysis_path.exists():
        analysis = read(analysis_path)
        if _uuid(analysis.get("synthetic_user_id")) != user_id or analysis.get("meals") != records:
            raise ValueError("Analysis differs from individual meal evidence")
    return user_id, records, hashes


def assert_owned_scope(user_id: str, meal_ids: set[str], rows: list[dict]) -> None:
    seen = set()
    for row in rows:
        meal_id = str(row["id"])
        if str(row["user_id"]) != user_id or meal_id not in meal_ids or meal_id in seen:
            raise ValueError("Database rows escaped the recorded user/meal scope")
        seen.add(meal_id)
    if seen != meal_ids:
        raise ValueError("A recorded meal is missing or belongs to another user")


def index_public_items(items: Any, expected_ids: set[str]) -> dict[str, dict]:
    if not isinstance(items, list):
        raise ValueError("Public detail items must be a list")
    by_id = {}
    for item in items:
        item_id = _uuid(item.get("itemId"))
        if item_id in by_id:
            raise ValueError("Duplicate item in public detail")
        by_id[item_id] = item
    if by_id.keys() != expected_ids:
        raise ValueError("Public detail has missing or unrelated item IDs")
    return by_id


def same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
        return a.is_finite() and b.is_finite() and a == b
    except (InvalidOperation, ValueError):
        return False


def compare_public_item(public: dict, *, display_name: str, amount: Any, unit: str | None,
                        confidence: Any, nutrition: dict | None,
                        nutrition_source: str | None, user_confirmed: bool) -> dict[str, bool]:
    actual_nutrition = public.get("nutrition")
    nutrition_equal = (actual_nutrition is None if nutrition is None else
                       isinstance(actual_nutrition, dict)
                       and actual_nutrition.keys() == NUTRIENTS
                       and all(same_number(actual_nutrition[key], nutrition[key]) for key in NUTRIENTS))
    required = {"itemId", "displayName", "amount", "unit", "confidence", "matched",
                "nutritionSource", "userConfirmed", "nutrition"}
    return {
        "public_fields_present": required <= public.keys(),
        "display_name": public.get("displayName") == display_name,
        "amount": same_number(public.get("amount"), amount),
        "unit": public.get("unit") == unit,
        "confidence": same_number(public.get("confidence"), confidence),
        "nutrition": nutrition_equal,
        # A valid stored DB link can still have no convertible nutrition.
        "matched_means_nutrition_available": public.get("matched") is (nutrition is not None),
        "nutrition_source": public.get("nutritionSource") == nutrition_source,
        "user_confirmed": public.get("userConfirmed") is user_confirmed,
    }


def _model(value: Any) -> dict | None:
    return None if value is None else value.model_dump(mode="json", by_alias=True)


def _number(value: Any) -> str | None:
    return None if value is None else str(value)


def _time(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def inspect(run_dir: Path, backend_root: Path, dsn: str) -> dict:
    user_id, records, evidence_hashes = load_scope(run_dir)
    if "app" in sys.modules:
        raise RuntimeError("Use a fresh Python process without the AI app imported")
    backend_root = backend_root.resolve()
    os.environ.setdefault("DB_URL", "127.0.0.1:1/unused_inspection_import")
    os.environ.setdefault("DB_USER", "unused_inspection_import")
    os.environ.setdefault("DB_PASSWORD", "unused_inspection_import")
    os.environ.setdefault("DB_ECHO", "false")
    sys.path.insert(0, str(backend_root))
    from sqlalchemy import String, cast, create_engine, select, text
    from sqlalchemy.orm import Session
    from app.crud import food as food_crud
    from app.models.meal import Meal, MealItem
    from app.models.task import Task
    from app.services.meal import item_nutrition, resolved_amount, to_grams
    from app.services.nutrition import resolve_by_food_ref_id, resolve_by_name

    metadata = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0, "db_mutations": 0,
        "backend_source_sha256": {name: _sha((backend_root / name).read_bytes()) for name in SOURCE_FILES},
        "backend_git_head": subprocess.check_output(
            ["git", "-C", str(backend_root), "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL).strip(),
        "input_sha256": evidence_hashes,
        "scope": {"synthetic_user_id": user_id,
                  "meal_ids": [record["meal_id"] for record in records]},
        "matched_definition": "Public API matched means nutrition available, not food_ref_id existence",
        "semantic_accuracy": "not_judged",
    }
    meal_ids = {record["meal_id"] for record in records}
    allowed_ids = [uuid.UUID(meal_id) for meal_id in meal_ids]
    engine = create_engine(dsn, echo=False, hide_parameters=True)
    results, task_results = [], []
    try:
        with Session(engine, autoflush=False) as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            settings = dict(db.execute(text(
                "SELECT current_setting('server_version') AS server_version, "
                "current_setting('transaction_isolation') AS transaction_isolation, "
                "current_setting('transaction_read_only') AS transaction_read_only, "
                "current_setting('server_encoding') AS server_encoding, datcollate, datctype "
                "FROM pg_database WHERE datname = current_database()"
            )).mappings().one())
            if settings["transaction_read_only"] != "on" or settings["transaction_isolation"] != "repeatable read":
                raise RuntimeError("Read-only repeatable-read transaction was not established")
            owned = db.execute(select(
                Meal.id, Meal.user_id, Meal.status, Meal.is_recalculation, Meal.deleted_at,
            ).where(Meal.id.in_(allowed_ids), Meal.user_id == uuid.UUID(user_id))).mappings().all()
            assert_owned_scope(user_id, meal_ids, [dict(row) for row in owned])
            owned_by_id = {str(row["id"]): row for row in owned}
            counts = db.execute(text(
                "SELECT category::text AS category, dataset_version, count(*) AS count FROM food_refs "
                "GROUP BY category, dataset_version ORDER BY category::text COLLATE \"C\" NULLS FIRST, "
                "dataset_version COLLATE \"C\""
            )).mappings().all()
            fingerprint, count = hashlib.sha256(), 0
            streamed = db.execute(text(
                "SELECT row_to_json(f)::text FROM food_refs AS f ORDER BY id COLLATE \"C\""
            ).execution_options(yield_per=1000))
            for serialized in streamed.scalars():
                fingerprint.update(serialized.encode("utf-8") + b"\n")
                count += 1
            streamed.close()
            if not count:
                raise ValueError("Food reference database is empty")
            metadata["database"] = {
                **settings, "counts_by_category_and_dataset": [dict(row) for row in counts],
                "alembic_revisions": list(db.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars()),
                "food_refs_rows": count, "food_refs_sha256": fingerprint.hexdigest(),
                "fingerprint_method": "SHA256 of UTF-8 row_to_json(f)::text + LF, all columns, id COLLATE C order",
            }
            # Joining the owner in every user-data query prevents scope widening.
            items = list(db.scalars(select(MealItem).join(Meal, Meal.id == MealItem.meal_id).where(
                Meal.id.in_(allowed_ids), Meal.user_id == uuid.UUID(user_id)
            ).order_by(MealItem.meal_id, MealItem.id)))
            candidates = {
                raw["item"]["candidateFoodRefId"] for item in items
                if isinstance((raw := item.raw_ai_result), dict) and isinstance(raw.get("item"), dict)
                and isinstance(raw["item"].get("candidateFoodRefId"), str)
            }
            known_candidates = food_crud.existing_ids(db, candidates)
            task_meal_id = Task.payload["mealId"].astext
            task_rows = db.execute(select(
                Task.id, Task.type, Task.status, Task.attempts, Task.created_at, Task.updated_at,
                Task.finished_at, task_meal_id.label("meal_id"),
            ).join(Meal, cast(Meal.id, String) == task_meal_id).where(
                Meal.id.in_(allowed_ids), Meal.user_id == uuid.UUID(user_id)
            ).order_by(Task.created_at, Task.id)).mappings().all()
            for task in task_rows:
                if task["meal_id"] not in meal_ids:
                    raise ValueError("Task escaped recorded meal scope")
                task_results.append({
                    "task_id": str(task["id"]), "meal_id": task["meal_id"], "type": task["type"],
                    "status": task["status"].value, "attempts": task["attempts"],
                    **{name: _time(task[name]) for name in ("created_at", "updated_at", "finished_at")},
                })
            for record in records:
                meal_id = record["meal_id"]
                meal = owned_by_id[meal_id]
                meal_items = [item for item in items if str(item.meal_id) == meal_id]
                public_by_id = index_public_items(record["final_detail"].get("items"),
                                                  {str(item.id) for item in meal_items})
                rows = []
                for item in meal_items:
                    stored_amount_g, amount, unit = resolved_amount(item)
                    amount_g = to_grams(amount, unit)
                    converted_estimate = to_grams(item.estimated_amount, item.estimated_unit)
                    top1 = resolve_by_name(db, name=item.original_food_name, amount_g=amount_g)
                    candidate_id = ((item.raw_ai_result or {}).get("item") or {}).get("candidateFoodRefId")
                    valid_candidate = candidate_id in known_candidates if isinstance(candidate_id, str) else False
                    expected_id = candidate_id if valid_candidate else top1.food_ref_id if top1 else None
                    food_ref = food_crud.get_by_id(db, item.food_ref_id) if item.food_ref_id else None
                    db_nutrition = resolve_by_food_ref_id(db, food_ref_id=item.food_ref_id,
                                                       amount_g=amount_g) if item.food_ref_id else None
                    nutrition, source = item_nutrition(item, db_nutrition)
                    expected_nutrition = _model(nutrition)
                    checks = compare_public_item(
                        public_by_id[str(item.id)], display_name=item.display_name, amount=amount,
                        unit=unit, confidence=item.confidence, nutrition=expected_nutrition,
                        nutrition_source=source, user_confirmed=item.confirmed_amount is not None,
                    )
                    checks.update(
                        stored_link_matches_worker_policy=item.food_ref_id == expected_id,
                        stored_estimated_grams=same_number(item.estimated_amount_g, converted_estimate),
                        stored_effective_grams=same_number(stored_amount_g, amount_g),
                        stored_display_matches_selected_food=item.display_name == (
                            food_ref.name if food_ref else item.original_food_name),
                        untouched_model_item=item.source.value == "MODEL" and item.confirmed_amount is None
                            and source != "USER_INPUT",
                    )
                    rows.append({
                        "item_id": str(item.id), "original_food_name": item.original_food_name,
                        "display_name": item.display_name, "stored_food_ref_id": item.food_ref_id,
                        "selected_food": None if food_ref is None else {
                            "id": food_ref.id, "name": food_ref.name,
                            "category": food_ref.category.value if food_ref.category else None,
                            "dataset_version": food_ref.dataset_version,
                            "serving_size_g": _number(food_ref.serving_size),
                        },
                        "estimated_amount": _number(item.estimated_amount), "estimated_unit": item.estimated_unit,
                        "estimated_amount_g": _number(item.estimated_amount_g),
                        "recomputed_estimated_amount_g": _number(converted_estimate),
                        "recomputed_effective_amount_g": _number(amount_g),
                        "confidence": _number(item.confidence), "raw_ai_result": sanitize(item.raw_ai_result),
                        "candidate_food_ref_id_valid": valid_candidate,
                        "name_search_top1_food_ref_id": top1.food_ref_id if top1 else None,
                        "stored_link_equals_name_search_top1": item.food_ref_id == (top1.food_ref_id if top1 else None),
                        "selection_source": "existing_ai_candidate_id" if valid_candidate else "name_search",
                        "authoritative_db_id_linked": food_ref is not None,
                        "public_api_matched": public_by_id[str(item.id)].get("matched"),
                        "recomputed_public_db_nutrition": _model(db_nutrition),
                        "effective_nutrition": expected_nutrition, "nutrition_source": source,
                        "public_api_item": sanitize(public_by_id[str(item.id)]), "checks": checks,
                    })
                relevant_tasks = [task for task in task_results if task["meal_id"] == meal_id]
                analyze_tasks = [task for task in relevant_tasks if task["type"] == "meal.analyze"]
                results.append({
                    "case_id": record["case_id"], "run": record["run"], "meal_id": meal_id,
                    "recorded_outcome": record["outcome"], "stored_status": meal["status"].value,
                    "checks": {
                        "frozen_status_matches_database": record["outcome"] == meal["status"].value,
                        "not_deleted_or_recalculated": meal["deleted_at"] is None and not meal["is_recalculation"],
                        "review_has_items": record["outcome"] != "REVIEW_REQUIRED" or bool(rows),
                        "one_analyze_task": len(analyze_tasks) == 1,
                        "no_feedback_or_other_tasks": len(relevant_tasks) == len(analyze_tasks),
                        "analyze_task_finished": len(analyze_tasks) == 1 and analyze_tasks[0]["status"] == "DONE",
                    },
                    "items": rows,
                })
            db.rollback()
    finally:
        engine.dispose()
    flat_items = [item for result in results for item in result["items"]]
    failed_checks = [
        {"meal_id": meal["meal_id"], "item_id": item.get("item_id"), "check": key}
        for meal in results for item in [meal, *meal["items"]]
        for key, passed in item["checks"].items() if not passed
    ]
    metadata["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return sanitize({
        "metadata": metadata,
        "summary": {"meals": len(results), "items": len(flat_items), "tasks": len(task_results),
                    "queue_attempts": sum(task["attempts"] for task in task_results),
                    "authoritative_db_id_linked": sum(item["authoritative_db_id_linked"] for item in flat_items),
                    "public_api_matched": sum(item["public_api_matched"] is True for item in flat_items),
                    "consistency_pass": not failed_checks, "failed_checks": failed_checks,
                    "model_calls": 0, "db_mutations": 0, "semantic_accuracy": "not_judged"},
        "meals": results, "tasks": task_results,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output exists; refusing to overwrite experiment evidence")
    dsn = os.environ.get("FOOD_MATCH_EVAL_DSN")
    if not dsn:
        parser.error("FOOD_MATCH_EVAL_DSN is required")
    logging.disable(logging.CRITICAL)
    try:
        result = inspect(args.run_dir, args.backend_root, dsn)
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
    except Exception as exc:
        print(f"Inspection failed ({type(exc).__name__}); details omitted to protect credentials.", file=sys.stderr)
        return 1
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0 if result["summary"]["consistency_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
