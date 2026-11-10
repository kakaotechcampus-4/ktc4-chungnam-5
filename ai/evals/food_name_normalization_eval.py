"""Prepare and compare a name-only offline replay; never call a model or DB.

``prepare`` preserves the source bytes and transforms the two saved observation
axes used by replay_food_db. ``compare`` verifies those prepared inputs against
both BE replay outputs. Changes in linking or nutrition are observations, not
proof that a meal was recognized more accurately.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from evals.replay_food_db import extract_responses


AXES = ('amount_accuracy', 'confidence_calibration')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(document) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def transform_document(source: dict, normalize) -> tuple[dict, list[dict]]:
    """Change only food names; repeated observations remain repeated observations."""
    records = extract_responses(source)
    candidate, changes = deepcopy(source), []
    for record in records:
        case, run = record['case'], record['run']
        for index, item in enumerate(record['response']['items'], 1):
            name = item.get('originalFoodName')
            if not isinstance(name, str) or not name.strip():
                raise ValueError('Saved food name is missing or invalid')
            normalized = normalize(name)
            if not isinstance(normalized, str) or not normalized.strip():
                raise ValueError('Normalizer returned an empty or invalid name')
            for axis in AXES:
                candidate['results'][f'{case}/{axis}']['samples'][run-1]['response']['items'][index-1]['originalFoodName'] = normalized
            if name != normalized:
                changes.append({'case': case, 'run': run, 'item_index': index,
                                'before_name': name, 'after_name': normalized})
    extract_responses(candidate)
    return candidate, changes


def prepare(source_path: Path, output_dir: Path, normalize, normalizer_source: Path) -> dict:
    if output_dir.exists():
        raise ValueError('Output directory exists; refusing overwrite')
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    candidate, changes = transform_document(source, normalize)
    candidate_bytes = encoded(candidate)
    records = extract_responses(source)
    protocol = {
        'version': 'food-name-normalization-v1',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_file': source_path.name,
        'source_sha256': sha(source_bytes), 'candidate_sha256': sha(candidate_bytes),
        'normalizer_source_file': normalizer_source.name,
        'normalizer_source_sha256': sha(normalizer_source.read_bytes()),
        'source_rule_version': source.get('rule_version'),
        'source_prompt_hash': source.get('prompt_hash'),
        'model_calls': 0, 'saved_responses': len(records),
        'item_occurrences': sum(len(record['response']['items']) for record in records),
        'changed_item_occurrences': len(changes), 'transformations': changes,
        'scope': 'Only originalFoodName in amount_accuracy and confidence_calibration observations is changed. Other fields and historical evaluation results are preserved, not re-evaluated.',
        'limitation': 'Input is previously parsed saved responses. No omitted food, parser failure, photo identity, or model improvement is measured.',
    }
    # Compute and validate everything before creating any new evidence files.
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir/'source.json').write_bytes(source_bytes)
    (output_dir/'candidate.json').write_bytes(candidate_bytes)
    (output_dir/'protocol.json').write_bytes(encoded(protocol))
    return protocol


def input_pairs(source: dict, candidate: dict) -> tuple[dict, list[dict]]:
    """Validate complete name-only transformation including response envelopes."""
    before, after = extract_responses(source), extract_responses(candidate)
    if len(before) != len(after):
        raise ValueError('Prepared response counts differ')
    expected, pairs, changes = deepcopy(source), {}, []
    for first, second in zip(before, after):
        if (first['case'], first['run']) != (second['case'], second['run']):
            raise ValueError('Prepared response coordinates differ')
        case, run = first['case'], first['run']
        if len(first['response']['items']) != len(second['response']['items']):
            raise ValueError('Prepared item counts differ')
        for index, (old, new) in enumerate(zip(first['response']['items'], second['response']['items']), 1):
            old_rest, new_rest = deepcopy(old), deepcopy(new)
            old_name, new_name = old_rest.pop('originalFoodName'), new_rest.pop('originalFoodName')
            if not all(isinstance(name, str) and name.strip() for name in (old_name, new_name)):
                raise ValueError('Prepared food name is invalid')
            if old_rest != new_rest:
                raise ValueError('A non-name item field was changed')
            pairs[(case, run, index)] = (old, new)
            for axis in AXES:
                expected['results'][f'{case}/{axis}']['samples'][run-1]['response']['items'][index-1]['originalFoodName'] = new_name
            if old_name != new_name:
                changes.append({'case': case, 'run': run, 'item_index': index,
                                'before_name': old_name, 'after_name': new_name})
    if candidate != expected:
        raise ValueError('Prepared document differs outside allowed food names')
    return pairs, changes


def replay_rows(replay: dict, expected: dict, side: int) -> dict:
    mapped = {}
    for row in replay['items']:
        coordinate = (row['case'], row['run'], row['item_index'])
        if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in coordinate[1:]):
            raise ValueError('Invalid replay coordinate')
        if coordinate in mapped or coordinate not in expected:
            raise ValueError('Duplicate or unexpected replay coordinate')
        if row['original_item'] != expected[coordinate][side]:
            raise ValueError('Replay item differs from prepared input')
        selected = row['selected']
        if selected is not None and (not isinstance(selected, dict) or not isinstance(selected.get('food_ref_id'), str)):
            raise ValueError('Invalid selected DB item')
        if row.get('after_129_linked') is not (selected is not None):
            raise ValueError('Replay link observation contradicts selected DB item')
        mapped[coordinate] = row
    if set(mapped) != set(expected):
        raise ValueError('Incomplete replay')
    if replay['summary']['food_items'] != len(mapped):
        raise ValueError('Replay item summary differs')
    if replay['summary']['after_129_linked'] != sum(row['selected'] is not None for row in mapped.values()):
        raise ValueError('Replay link summary differs')
    return mapped


def check_environment(before: dict, after: dict, protocol: dict) -> None:
    first, second = before.get('metadata', {}), after.get('metadata', {})
    if not first.get('backend_source_sha256') or first['backend_source_sha256'] != second.get('backend_source_sha256'):
        raise ValueError('BE source fingerprints differ or are missing')
    database = first.get('database', {})
    if not database.get('food_refs_sha256') or database != second.get('database'):
        raise ValueError('DB snapshot or environment differs or is missing')
    if not isinstance(database.get('food_refs_rows'), int) or database['food_refs_rows'] <= 0:
        raise ValueError('DB row count is missing or empty')
    for metadata, key in ((first, 'source_sha256'), (second, 'candidate_sha256')):
        if metadata.get('baseline_sha256') != protocol[key]:
            raise ValueError('DB replay source hash differs from prepared input')
        if metadata.get('baseline_prompt_hash') != protocol['source_prompt_hash']:
            raise ValueError('Prompt metadata differs from prepared input')
        if metadata.get('baseline_rule_version') != protocol['source_rule_version']:
            raise ValueError('Rule metadata differs from prepared input')
        if metadata.get('model_calls') != 0:
            raise ValueError('Expected offline replay metadata')
        if metadata.get('database', {}).get('transaction_read_only') != 'on':
            raise ValueError('Expected read-only DB replay')


def nutrient_changes(before: dict | None, after: dict | None) -> list[dict]:
    """Missing data remain missing; do not turn absent nutrition into zero."""
    if before is not None and not isinstance(before, dict) or after is not None and not isinstance(after, dict):
        raise ValueError('Invalid nutrition observation')
    left, right = before or {}, after or {}
    changes = []
    for field in sorted(left.keys() | right.keys()):
        old, new = left.get(field), right.get(field)
        if old == new and (field in left) == (field in right):
            continue
        comparable = (isinstance(old, (int, float)) and not isinstance(old, bool)
                      and isinstance(new, (int, float)) and not isinstance(new, bool))
        delta = Decimal(str(new)) - Decimal(str(old)) if comparable else None
        if delta is not None and not delta.is_finite():
            raise ValueError('Nonfinite nutrition observation')
        changes.append({'field': field, 'before': old, 'after': new,
                        'before_present': field in left, 'after_present': field in right,
                        'numeric_delta': float(delta) if delta is not None else None})
    return changes


def compare(source: dict, candidate: dict, protocol: dict, before: dict, after: dict) -> dict:
    pairs, transformations = input_pairs(source, candidate)
    if transformations != protocol['transformations'] or len(pairs) != protocol['item_occurrences']:
        raise ValueError('Prepared transformation audit differs')
    check_environment(before, after, protocol)
    old_rows, new_rows = replay_rows(before, pairs, 0), replay_rows(after, pairs, 1)
    for replay in (before, after):
        if replay['summary']['saved_responses'] != protocol['saved_responses']:
            raise ValueError('Replay response count differs')
    changes, transitions = [], Counter()
    link_gains = link_losses = same_query_changes = nutrition_changed = changed_ids = 0
    for coordinate in pairs:
        old, new = old_rows[coordinate], new_rows[coordinate]
        for field in ('worker_amount', 'worker_unit', 'amount_g'):
            if old[field] != new[field]:
                raise ValueError('Replay changed an amount or unit')
        selected_before, selected_after = old['selected'], new['selected']
        old_id = selected_before['food_ref_id'] if selected_before else None
        new_id = selected_after['food_ref_id'] if selected_after else None
        name_changed = old['original_item']['originalFoodName'] != new['original_item']['originalFoodName']
        id_changed = old_id != new_id
        selection_changed = selected_before != selected_after
        link_gains += selected_before is None and selected_after is not None
        link_losses += selected_before is not None and selected_after is None
        changed_ids += id_changed
        same_query_changes += not name_changed and selection_changed
        nutrition_diff = nutrient_changes(old['nutrition'], new['nutrition'])
        nutrition_changed += old['nutrition'] != new['nutrition']
        if id_changed:
            category_before = selected_before.get('category') if selected_before else 'UNLINKED'
            category_after = selected_after.get('category') if selected_after else 'UNLINKED'
            transitions[f'{category_before} -> {category_after}'] += 1
        if name_changed or selection_changed or old['nutrition'] != new['nutrition'] or old['nutrition_conversion_status'] != new['nutrition_conversion_status']:
            changes.append({'case': coordinate[0], 'run': coordinate[1], 'item_index': coordinate[2],
                            'before_name': old['original_item']['originalFoodName'],
                            'after_name': new['original_item']['originalFoodName'],
                            'name_changed': name_changed, 'selected_id_changed': id_changed,
                            'before_selected': selected_before, 'after_selected': selected_after,
                            'amount_g': old['amount_g'],
                            'before_nutrition': old['nutrition'], 'after_nutrition': new['nutrition'],
                            'nutrient_changes': nutrition_diff,
                            'before_conversion_status': old['nutrition_conversion_status'],
                            'after_conversion_status': new['nutrition_conversion_status']})
    linked_before = sum(row['selected'] is not None for row in old_rows.values())
    linked_after = sum(row['selected'] is not None for row in new_rows.values())
    return {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'model_calls': 0,
            'scope': 'Name-only offline replay with identical BE sources and public DB snapshot.',
            'metric_warning': 'DB links, selected-ID changes, and nutrient changes are not semantic accuracy or nutrition ground truth. Source recognition errors are unchanged.',
            'environment_verified': True, 'summary': {
                'saved_responses': protocol['saved_responses'], 'item_occurrences': len(pairs),
                'changed_name_occurrences': len(transformations), 'changed_selected_ids': changed_ids,
                'linked_before': linked_before, 'linked_after': linked_after,
                'new_links': link_gains, 'lost_links': link_losses,
                'nutrition_changed_occurrences': nutrition_changed,
                'same_query_selection_changes': same_query_changes,
                'category_transitions': dict(transitions)},
            'changes': changes}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    preparation = commands.add_parser('prepare')
    preparation.add_argument('--source', type=Path, required=True)
    preparation.add_argument('--output-dir', type=Path, required=True)
    comparison = commands.add_parser('compare')
    for flag in ('prepared-dir', 'before-db', 'after-db', 'output'):
        comparison.add_argument('--'+flag, type=Path, required=True)
    args = parser.parse_args(argv)
    destination = args.output_dir if args.command == 'prepare' else args.output
    if destination.exists():
        parser.error('Output exists; refusing overwrite')
    try:
        if args.command == 'prepare':
            # Import only at preparation time. Comparison remains independent of
            # the currently installed normalizer or a later rule revision.
            from app import food_names
            result = prepare(args.source, args.output_dir, food_names.normalize_food_name,
                             Path(food_names.__file__))
            summary = {key: result[key] for key in ('model_calls', 'saved_responses', 'item_occurrences', 'changed_item_occurrences')}
        else:
            protocol_bytes = (args.prepared_dir/'protocol.json').read_bytes()
            protocol = json.loads(protocol_bytes)
            blobs = {name: (args.prepared_dir/f'{name}.json').read_bytes() for name in ('source', 'candidate')}
            for name, blob in blobs.items():
                if sha(blob) != protocol[f'{name}_sha256']:
                    raise ValueError('Prepared input hash differs from protocol')
            before_bytes, after_bytes = args.before_db.read_bytes(), args.after_db.read_bytes()
            result = compare(json.loads(blobs['source']), json.loads(blobs['candidate']), protocol,
                             json.loads(before_bytes), json.loads(after_bytes))
            result['source_sha256'] = {'protocol': sha(protocol_bytes),
                                       'source': sha(blobs['source']), 'candidate': sha(blobs['candidate']),
                                       'before_db': sha(before_bytes), 'after_db': sha(after_bytes)}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('xb') as handle:
                handle.write(encoded(result))
            summary = result['summary']
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(f'Normalization evaluation failed ({type(exc).__name__}); no input details emitted.')
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
