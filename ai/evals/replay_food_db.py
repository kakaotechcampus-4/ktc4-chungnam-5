"""Replay saved photo responses against unchanged BE matching in a read-only snapshot.

Run with the BE Python environment; this command makes no model calls. Set
FOOD_MATCH_EVAL_DSN in the environment. Credentials are never written to output.
The JSON contains observations, not semantic correctness labels.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter


def extract_responses(document: dict) -> list[dict]:
    """Keep repetitions, removing only the cross-axis copies by selecting one axis."""
    results = document["results"]
    records = []
    for key, result in results.items():
        if not key.endswith("/amount_accuracy"):
            continue
        case = key.rsplit("/", 1)[0]
        samples = result.get("samples", [])
        other = results.get(f"{case}/confidence_calibration")
        if other is None or len(samples) != len(other.get("samples", [])):
            raise ValueError("Saved observation axes do not align")
        if len(samples) != result["n"] or other["n"] != result["n"]:
            raise ValueError("Baseline does not contain every repetition")
        for run, (sample, comparison) in enumerate(zip(samples, other["samples"]), 1):
            if sample["response"] != comparison["response"]:
                raise ValueError("Saved observation responses differ between axes")
            records.append({"case": case, "run": run, "response": sample["response"]})
    if not records:
        raise ValueError("No complete saved photo responses")
    return records


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments], stderr=subprocess.DEVNULL, text=True
    ).strip()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump_model(model):
    return None if model is None else model.model_dump(mode="json", by_alias=True)


def replay(baseline: Path, backend_root: Path, dsn: str) -> dict:
    # Import the supplied BE, not ai/app, even when run as an ai.evals module.
    backend_root = backend_root.resolve()
    if "app" in sys.modules:
        raise RuntimeError("Run replay in a fresh Python process without app imported")
    # A BE import creates its global engine. It is never used here; all reads use
    # the explicit replay Session. Supply inert import defaults if unset.
    os.environ.setdefault("DB_URL", "127.0.0.1:1/unused_replay_import")
    os.environ.setdefault("DB_USER", "unused_replay_import")
    os.environ.setdefault("DB_PASSWORD", "unused_replay_import")
    os.environ.setdefault("DB_ECHO", "false")
    sys.path.insert(0, str(backend_root))
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session
    from app.crud import food as food_crud
    from app.services.meal import to_grams
    from app.services.nutrition import (
        resolve_by_food_ref_id, resolve_by_name, search_candidates,
    )
    from app.worker.jobs.analyze_meal import _parse_item

    raw = baseline.read_bytes()
    document = json.loads(raw)
    responses = extract_responses(document)
    source_files = (
        "app/crud/food.py", "app/services/nutrition.py", "app/services/meal.py",
        "app/worker/jobs/analyze_meal.py", "app/models/food.py", "app/models/enums.py",
        "app/schemas/nutrition.py",
    )
    sources = {path: _sha((backend_root / path).read_bytes()) for path in source_files}
    metadata = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_file": baseline.name,
        "baseline_sha256": _sha(raw),
        "baseline_rule_version": document.get("rule_version"),
        "baseline_prompt_hash": document.get("prompt_hash"),
        "backend_git_head": _git(backend_root, "rev-parse", "HEAD"),
        "backend_source_sha256": sources,
        "model_calls": 0,
        "scope": "Saved amount_accuracy responses; confidence_calibration copies verified",
        "timing_scope": "Diagnostic local read-only replay; not a production latency benchmark",
        "before_129_comparison": {
            "method": "Inferred from prior worker's valid-candidate-ID-only condition",
            "historical_code_executed": False,
            "historical_database_available": False,
            "note": "Candidate existence is checked in this snapshot. This is not an observed historical rate.",
        },
    }
    engine = create_engine(dsn, echo=False, hide_parameters=True)
    statement_counts = {"metadata": 0, "replay": 0}
    phase = ["metadata"]

    @event.listens_for(engine, "before_cursor_execute")
    def count_statement(*_args):
        statement_counts[phase[0]] += 1

    rows, query_cache, queries = [], {}, []
    try:
        with Session(engine, autoflush=False) as db:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            settings = db.execute(text(
                "SELECT current_setting('server_version') AS server_version, "
                "current_setting('transaction_isolation') AS transaction_isolation, "
                "current_setting('transaction_read_only') AS transaction_read_only, "
                "current_setting('server_encoding') AS server_encoding, "
                "datcollate, datctype FROM pg_database WHERE datname = current_database()"
            )).mappings().one()
            counts = db.execute(text(
                "SELECT category::text AS category, dataset_version, count(*) AS count "
                "FROM food_refs GROUP BY category, dataset_version "
                "ORDER BY category::text COLLATE \"C\" NULLS FIRST, dataset_version COLLATE \"C\""
            )).mappings().all()
            fingerprint = hashlib.sha256()
            fingerprint_rows = 0
            # All DB columns, stable ID order and explicit UTF-8 newline boundaries.
            # Stream rather than building a potentially large string_agg in memory.
            fingerprint_result = db.execute(text(
                "SELECT row_to_json(f)::text FROM food_refs AS f ORDER BY id COLLATE \"C\""
            ).execution_options(yield_per=1000))
            for serialized in fingerprint_result.scalars():
                fingerprint.update(serialized.encode("utf-8") + b"\n")
                fingerprint_rows += 1
            fingerprint_result.close()
            if fingerprint_rows == 0:
                raise ValueError("food_refs is empty")
            metadata["database"] = {
                **dict(settings),
                "counts_by_category_and_dataset": [dict(row) for row in counts],
                "food_refs_rows": fingerprint_rows,
                "food_refs_sha256": fingerprint.hexdigest(),
                "fingerprint_method": "SHA256 of UTF-8 row_to_json(f)::text + LF, all columns, id COLLATE C order",
            }
            phase[0] = "replay"
            supplied_ids = {
                item["candidateFoodRefId"]
                for record in responses for item in record["response"]["items"]
                if isinstance(item.get("candidateFoodRefId"), str)
            }
            known_ids = food_crud.existing_ids(db, supplied_ids)

            def search(name):
                if name in query_cache:
                    return query_cache[name], True
                start = perf_counter()
                match = resolve_by_name(db, name=name, amount_g=None)
                resolve_ms = (perf_counter() - start) * 1000
                start = perf_counter()
                try:
                    candidates = search_candidates(db, query=name, limit=5)
                    search_status = "ok"
                except ValueError:
                    candidates, search_status = [], "invalid_query"
                search_ms = (perf_counter() - start) * 1000
                selected_id = None if match is None else match.food_ref_id
                if selected_id != (candidates[0].food_ref_id if candidates else None):
                    raise ValueError("Automatic match differs from candidate-list first result")
                value = {
                    "query_id": len(queries) + 1,
                    "name": name,
                    "name_match_food_ref_id": selected_id,
                    "candidate_search_status": search_status,
                    "top5_candidates": [_dump_model(candidate) for candidate in candidates],
                    "resolve_by_name_ms": round(resolve_ms, 3),
                    "search_candidates_ms": round(search_ms, 3),
                }
                query_cache[name] = value
                queries.append(value)
                return value, False

            for record in responses:
                response = record["response"]
                for index, original in enumerate(response["items"], 1):
                    item = _parse_item(
                        original, model_version=response["modelVersion"],
                        safety_status=response["safetyStatus"],
                    )
                    # Query every name for diagnosis, but preserve worker ID priority.
                    query, cache_hit = search(item.name)
                    before_linked = item.food_ref_id in known_ids
                    selected_id = item.food_ref_id if before_linked else query["name_match_food_ref_id"]
                    selected = food_crud.get_by_id(db, selected_id) if selected_id else None
                    amount_g = to_grams(item.amount, item.unit)
                    nutrition = resolve_by_food_ref_id(
                        db, food_ref_id=selected_id, amount_g=amount_g
                    ) if selected_id else None
                    if selected_id is None:
                        conversion_status = "unmatched"
                    elif amount_g is None:
                        conversion_status = "amount_not_convertible"
                    elif nutrition is None:
                        conversion_status = "db_serving_size_unavailable"
                    elif any(value is None for value in nutrition.model_dump().values()):
                        conversion_status = "calculated_with_missing_nutrients"
                    else:
                        conversion_status = "calculated_complete"
                    rows.append({
                        "case": record["case"], "run": record["run"], "item_index": index,
                        "original_item": original,
                        "worker_food_name": item.name,
                        "worker_amount": str(item.amount),
                        "worker_unit": item.unit,
                        "amount_g": None if amount_g is None else str(amount_g),
                        "query_id": query["query_id"], "query_cache_hit": cache_hit,
                        "top5_candidates": query["top5_candidates"],
                        "selected": None if selected is None else {
                            "food_ref_id": selected.id, "name": selected.name,
                            "category": selected.category.value if selected.category is not None else None,
                            "dataset_version": selected.dataset_version,
                        },
                        "selection_source": "existing_ai_candidate_id" if before_linked else "name_search",
                        "before_129_linked_inferred": before_linked,
                        "after_129_linked": selected_id is not None,
                        "nutrition_conversion_status": conversion_status,
                        "nutrition": _dump_model(nutrition),
                    })
            db.rollback()
    finally:
        engine.dispose()
    metadata["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return {
        "metadata": metadata,
        "summary": {
            "saved_responses": len(responses), "food_items": len(rows),
            "unique_name_queries": len(queries), "name_query_cache_hits": sum(row["query_cache_hit"] for row in rows),
            "resolve_by_name_calls": len(queries), "search_candidates_calls": len(queries),
            "sql_statement_counts": statement_counts,
            "before_129_linked_inferred": sum(row["before_129_linked_inferred"] for row in rows),
            "after_129_linked": sum(row["after_129_linked"] for row in rows),
            "nutrition_conversion_counts": {
                status: sum(row["nutrition_conversion_status"] == status for row in rows)
                for status in sorted({row["nutrition_conversion_status"] for row in rows})
            },
            "semantic_accuracy": "not_judged",
        },
        "queries": queries,
        "items": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output already exists; refusing overwrite")
    dsn = os.environ.get("FOOD_MATCH_EVAL_DSN")
    if not dsn:
        parser.error("FOOD_MATCH_EVAL_DSN is required")
    # DB exceptions can embed DSNs or parameters; do not emit exception messages.
    logging.disable(logging.CRITICAL)
    try:
        result = replay(args.baseline, args.backend_root, dsn)
        serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    except Exception as exc:
        print(f"Replay failed ({type(exc).__name__}); no exception detail emitted to protect credentials.", file=sys.stderr)
        return 1
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
