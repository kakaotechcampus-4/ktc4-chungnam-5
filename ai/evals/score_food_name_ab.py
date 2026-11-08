"""Offline target-hit scoring; errors and missing calls stay in planned denominators.

Target hits are not overall food accuracy. Extra dishes are outside the scored
targets, not automatically false positives. No network or database access occurs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def comparison_parity(raw: dict, baseline_db: dict, candidate_db: dict):
    left, right = baseline_db.get('metadata'), candidate_db.get('metadata')
    if (left is None) != (right is None):
        raise ValueError('Replay metadata is missing from one arm')
    db_status = 'unverified: no replay metadata'
    if left is not None:
        for key in ('backend_source_sha256',):
            if not left.get(key) or left[key] != right.get(key):
                raise ValueError('BE source fingerprints differ or are missing')
        first, second = left.get('database', {}), right.get('database', {})
        if not first.get('food_refs_sha256') or first['food_refs_sha256'] != second.get('food_refs_sha256'):
            raise ValueError('DB fingerprints differ or are missing')
        for key in ('food_refs_rows', 'counts_by_category_and_dataset'):
            if first.get(key) != second.get(key):
                raise ValueError('DB dataset metadata differs')
        db_status = 'verified: identical DB fingerprint and BE sources'
    models = {arm: set() for arm in ('baseline', 'candidate')}
    for record in raw['records']:
        for attempt in record.get('gateway_attempts', []):
            model = attempt.get('model')
            if isinstance(model, str) and model:
                models[record['arm']].add(model)
    if len(set().union(*models.values())) > 1:
        raise ValueError('Provider model names differ across calls or arms')
    known = all(len(values) == 1 for values in models.values())
    return {'database_and_backend': db_status,
            'provider_models_by_arm': {arm: sorted(values) for arm, values in models.items()},
            'provider_model_status': 'same reported model in both arms' if known else
            'unverified: provider metadata absent for at least one arm; gateway alias alone does not prove model version'}


def image_parity(manifest: dict, protocol: dict):
    expected = {case['case_id']: case.get('image_sha256') for case in manifest['cases']}
    if not all(expected.values()) or expected != protocol.get('image_sha256'):
        raise ValueError('Image fingerprints differ from frozen manifest')


def validate_manifest(manifest: dict, requested_n: int | None = None):
    declared = manifest.get('protocol', {}).get('repetitions', manifest.get('repetitions'))
    if requested_n is not None and declared is not None and requested_n != declared:
        raise ValueError('Requested n differs from frozen repetitions')
    n = requested_n if requested_n is not None else declared
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError('Positive planned repetitions are required')
    cases, seen, target_count = manifest.get('cases', []), set(), 0
    if not cases:
        raise ValueError('No manifest cases')
    for case in cases:
        name = case['case_id']
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_-]+', name) or name in seen:
            raise ValueError('Invalid or duplicate case_id')
        seen.add(name)
        if case['split'] not in {'development', 'holdout', 'regression'}:
            raise ValueError('Unknown split')
        targets = case.get('targets', [])
        if not targets and not (case['split'] == 'regression' and case.get('baseline_case')):
            raise ValueError('Scored case requires confirmed targets')
        target_ids, food_ids = set(), set()
        for target in targets:
            identifier, accepted = target['target_id'], target.get('acceptable_food_ref_ids')
            if not isinstance(identifier, str) or not identifier or identifier in target_ids:
                raise ValueError('Invalid or duplicate target_id')
            target_ids.add(identifier)
            if target.get('label_status') != 'confirmed':
                raise ValueError('Pending or unconfirmed target labels cannot be scored')
            if not isinstance(accepted, list) or not accepted:
                raise ValueError('Target requires acceptable food IDs')
            aliases = target.get('recognized_names')
            if aliases is not None and (not isinstance(aliases, list) or not aliases or
                    any(not isinstance(alias, str) or not alias.strip() for alias in aliases)):
                raise ValueError('recognized_names must contain nonempty names')
            for food_id in accepted:
                if not isinstance(food_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}', food_id):
                    raise ValueError('Invalid acceptable food ID')
                if food_id in food_ids:
                    raise ValueError('Food ID duplicated or shared across targets')
                food_ids.add(food_id)
            target_count += 1
    if not target_count:
        raise ValueError('No confirmed DB targets to measure')
    return cases, n


def map_replay(records: list[dict], arm: str, replay: dict):
    """Replay run indexes enumerate successful samples; recover actual source runs."""
    successful = {}
    for record in records:
        if record['arm'] == arm and record['error'] is None:
            successful.setdefault(record['case_id'], []).append(record)
    mapped, seen = {}, set()
    for row in replay['items']:
        case, ordinal, index = row['case'], row['run'], row['item_index']
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in (ordinal, index)):
            raise ValueError('Invalid replay coordinate')
        runs = successful.get(case, [])
        if ordinal > len(runs):
            raise ValueError('Replay refers to an absent successful response')
        source = runs[ordinal-1]
        source_items = source['response']['items']
        if index > len(source_items) or row['original_item'] != source_items[index-1]:
            raise ValueError('Replay item differs from saved response')
        coordinate = (case, source['run'], index)
        if coordinate in seen:
            raise ValueError('Duplicate replay item')
        seen.add(coordinate)
        selected = row['selected']
        if selected is not None and (not isinstance(selected, dict) or not isinstance(selected.get('food_ref_id'), str)):
            raise ValueError('Invalid selected DB item')
        mapped.setdefault((case, source['run']), []).append(row)
    expected = sum(len(r['response']['items']) for runs in successful.values() for r in runs)
    if len(seen) != expected:
        raise ValueError('DB replay is incomplete')
    return mapped


def regression_checks(case: dict, records: dict, arm: str, n: int):
    if not case.get('baseline_case'):
        return None
    from evals import analyze_meal_eval, harness
    name = case['baseline_case']
    name = name if name in analyze_meal_eval.LABELS else f'{name}.jpg'
    if name not in analyze_meal_eval.LABELS:
        raise ValueError('Unknown baseline_case')
    original = analyze_meal_eval.build_cases({name: analyze_meal_eval.LABELS[name]}, n=n)[0]
    iterator = iter(range(1, n+1))

    def saved_post(_path, _request):
        record = records.get((arm, case['case_id'], next(iterator)))
        if record is None or record['error'] is not None:
            raise RuntimeError('SavedCallUnavailable')
        return harness.Observation(status_code=200, payload=record['response'])

    results = harness.run_case(original, saved_post, n=n)
    passed, reasons = harness.gate(results)
    return {'scope': 'Unchanged existing checks on evaluation envelopes; not full API or safety_guard execution.',
            'baseline_case': name, 'gate_passed': passed, 'gate_reasons': reasons, 'axes': results}


def score(manifest: dict, raw: dict, baseline_db: dict, candidate_db: dict, n=None):
    cases, n = validate_manifest(manifest, n)
    parity = comparison_parity(raw, baseline_db, candidate_db)
    names = {c['case_id']: c for c in cases}
    planned = len(cases)*n*2
    if raw.get('planned_calls', planned) != planned:
        raise ValueError('Planned call count differs from frozen denominator')
    records = {}
    for record in raw['records']:
        key = (record['arm'], record['case_id'], record['run'])
        if key in records or key[0] not in {'baseline', 'candidate'} or key[1] not in names:
            raise ValueError('Unknown or duplicate response coordinate')
        if isinstance(key[2], bool) or not isinstance(key[2], int) or not 1 <= key[2] <= n:
            raise ValueError('Response run exceeds planned repetitions')
        if record.get('split', names[key[1]]['split']) != names[key[1]]['split']:
            raise ValueError('Response split differs from manifest')
        if record['error'] is None:
            response = record.get('response')
            if not isinstance(response, dict) or not isinstance(response.get('items'), list):
                raise ValueError('Successful response lacks parsed items')
        records[key] = record
    outputs = {}
    for arm, db in [('baseline', baseline_db), ('candidate', candidate_db)]:
        mapped = map_replay(raw['records'], arm, db)
        case_results, splits = [], {}
        for case in cases:
            name, split = case['case_id'], case['split']
            rows = [row for run in range(1, n+1) for row in mapped.get((name, run), [])]
            supplied = [records.get((arm, name, run)) for run in range(1, n+1)]
            failed = [run for run, r in enumerate(supplied, 1) if r and r['error'] is not None]
            missing = [run for run, r in enumerate(supplied, 1) if r is None]
            allowed = set(food_id for t in case.get('targets', []) for food_id in t['acceptable_food_ref_ids'])
            targets = []
            for target in case.get('targets', []):
                hits = [run for run in range(1, n+1) if any(
                    row['selected'] and row['selected']['food_ref_id'] in target['acceptable_food_ref_ids']
                    for row in mapped.get((name, run), []))]
                target_result = {'target_id': target['target_id'], 'n': n, 'hits': len(hits),
                                 'hit_runs': hits, 'miss_runs': [r for r in range(1, n+1) if r not in hits]}
                if target.get('recognized_names'):
                    aliases = {''.join(alias.split()) for alias in target['recognized_names']}
                    observed = [run for run, record in enumerate(supplied, 1)
                                if record and record['error'] is None and any(
                                    ''.join(item.get('originalFoodName', '').split()) in aliases
                                    for item in record['response']['items'])]
                    parsed = [run for run, record in enumerate(supplied, 1) if record and record['error'] is None]
                    target_result['name_diagnostic'] = {
                        'recognized_name_runs': observed,
                        'name_present_but_id_not_hit_runs': [run for run in observed if run not in hits],
                        'recognition_review_needed_runs': [run for run in parsed if run not in observed],
                        'note': 'Exact whitespace-normalized alias observation only; absent alias does not prove food omission.'}
                targets.append(target_result)
            counts = Counter(row['selected']['food_ref_id'] for row in rows if row['selected'])
            result = {'case_id': name, 'split': split, 'planned_responses': n,
                      'parsed_responses': n-len(failed)-len(missing), 'failed_runs': failed, 'missing_runs': missing,
                      'targets': targets, 'linked_items': sum(counts.values()), 'unlinked_items': len(rows)-sum(counts.values()),
                      'selected_id_counts': dict(counts),
                      'outside_scored_targets': {key: value for key, value in counts.items() if allowed and key not in allowed},
                      'regression': regression_checks(case, records, arm, n)}
            case_results.append(result)
            total = splits.setdefault(split, Counter())
            total.update({'planned_responses': n, 'parsed_responses': result['parsed_responses'],
                          'failed_responses': len(failed), 'missing_responses': len(missing),
                          'target_trials': len(targets)*n, 'target_hits': sum(t['hits'] for t in targets),
                          'linked_items': result['linked_items'], 'unlinked_items': result['unlinked_items']})
        outputs[arm] = {'cases': case_results, 'by_split': {k: dict(v) for k, v in splits.items()}}
    comparison = []
    for before, after in zip(outputs['baseline']['cases'], outputs['candidate']['cases']):
        for first, second in zip(before['targets'], after['targets']):
            comparison.append({'case_id': before['case_id'], 'split': before['split'],
                               'target_id': first['target_id'], 'n': n,
                               'baseline_hits': first['hits'], 'candidate_hits': second['hits'],
                               'hit_delta': second['hits']-first['hits']})
    return {'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'metric': 'Per-response hit of each preconfirmed DB target; not overall food accuracy',
            'denominator': 'Planned n, including failed and missing calls. Duplicate items cannot increase a target hit.',
            'outside_target_note': 'Additional selected IDs are observations, not automatically false positives.',
            'label_limitation': 'Confirmed label status is required; DB ID existence must be verified during P0.',
            'comparison_parity': parity, 'n': n, 'arms': outputs, 'comparison': comparison}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'raw-responses', 'baseline-db-replay', 'candidate-db-replay', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--n', type=int)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Output exists; refusing overwrite')
    paths = {'manifest': args.manifest, 'raw': args.raw_responses,
             'baseline_db': args.baseline_db_replay, 'candidate_db': args.candidate_db_replay}
    try:
        blobs = {name: path.read_bytes() for name, path in paths.items()}
        documents = {name: json.loads(blob) for name, blob in blobs.items()}
        hashes = {name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()}
        protocol_path = args.raw_responses.parent/'protocol.json'
        if protocol_path.exists():
            protocol = json.loads(protocol_path.read_text())
            if protocol['manifest_sha256'] != hashes['manifest']:
                raise ValueError('Manifest differs from pre-call protocol snapshot')
            if args.n is not None and args.n != protocol['n']:
                raise ValueError('n differs from pre-call protocol snapshot')
            image_parity(documents['manifest'], protocol)
            n = protocol['n']
        else:
            n = args.n
        result = score(**documents, n=n)
        result['source_sha256'] = hashes
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
    except (KeyError, ValueError, OSError, TypeError) as exc:
        print(f'Scoring refused: {type(exc).__name__}: {exc}')
        return 1
    print(json.dumps(result['comparison'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
