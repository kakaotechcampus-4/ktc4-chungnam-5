"""Target scoring must not hide missing calls or count duplicate bowls as gains."""
import pytest

from evals import score_food_name_ab as scorer


def manifest(n=3):
    return {'protocol': {'repetitions': n}, 'cases': [
        {'case_id': 'eggs', 'split': 'holdout', 'targets': [
            {'target_id': 'fried_egg', 'label_status': 'confirmed', 'acceptable_food_ref_ids': ['EGG-1']}]}]}


def record(arm, run, names=('달걀프라이',), error=None):
    return {'case_id': 'eggs', 'split': 'holdout', 'arm': arm, 'run': run, 'error': error,
            'response': None if error else {'items': [{'originalFoodName': n} for n in names]}}


def replay(records, arm, ids=None):
    items = []
    successful = [r for r in records if r['arm'] == arm and r['error'] is None]
    for run, rec in enumerate(successful, 1):
        for index, original in enumerate(rec['response']['items'], 1):
            selected = ids[index-1] if ids is not None else 'EGG-1'
            items.append({'case': 'eggs', 'run': run, 'item_index': index,
                          'original_item': original, 'selected': {'food_ref_id': selected} if selected else None})
    return {'items': items}


def test_error_and_missing_call_remain_in_denominator_and_source_run_mapping():
    records = [record('baseline', 1, error='TimeoutError'), record('baseline', 2),
               record('candidate', 1), record('candidate', 2), record('candidate', 3)]
    out = scorer.score(manifest(), {'records': records, 'planned_calls': 6},
                       replay(records, 'baseline'), replay(records, 'candidate'))
    before = out['arms']['baseline']['cases'][0]
    assert before['targets'][0] == {'target_id': 'fried_egg', 'n': 3, 'hits': 1, 'hit_runs': [2], 'miss_runs': [1, 3]}
    assert before['failed_runs'] == [1]
    assert before['missing_runs'] == [3]
    assert out['comparison'][0]['candidate_hits'] == 3


def test_duplicate_egg_items_count_once_per_response():
    records = [record(arm, 1, ('달걀프라이', '달걀프라이')) for arm in ('baseline', 'candidate')]
    out = scorer.score(manifest(1), {'records': records}, replay(records, 'baseline'), replay(records, 'candidate'))
    case = out['arms']['candidate']['cases'][0]
    assert case['linked_items'] == 2
    assert case['targets'][0]['hits'] == 1


def test_other_food_does_not_fill_missing_target_and_is_not_called_false_positive():
    records = [record(arm, 1, ('빵',)) for arm in ('baseline', 'candidate')]
    out = scorer.score(manifest(1), {'records': records}, replay(records, 'baseline', ['BREAD']), replay(records, 'candidate', ['BREAD']))
    case = out['arms']['candidate']['cases'][0]
    assert case['targets'][0]['hits'] == 0
    assert case['outside_scored_targets'] == {'BREAD': 1}
    assert 'false_positives' not in case


@pytest.mark.parametrize('ids', [[], [''], [12], [None], [' ID '], ['x'*65], ['A', 'A']])
def test_invalid_label_ids_are_rejected(ids):
    doc = manifest()
    doc['cases'][0]['targets'][0]['acceptable_food_ref_ids'] = ids
    with pytest.raises(ValueError):
        scorer.validate_manifest(doc)


@pytest.mark.parametrize('status', ['pending', 'provisional', None])
def test_unconfirmed_labels_are_rejected(status):
    doc = manifest()
    doc['cases'][0]['targets'][0]['label_status'] = status
    with pytest.raises(ValueError, match='unconfirmed'):
        scorer.validate_manifest(doc)


def test_incomplete_or_mismatched_db_replay_is_rejected():
    records = [record('baseline', 1)]
    with pytest.raises(ValueError, match='incomplete'):
        scorer.map_replay(records, 'baseline', {'items': []})
    db = replay(records, 'baseline')
    db['items'][0]['original_item'] = {'originalFoodName': 'different'}
    with pytest.raises(ValueError, match='differs'):
        scorer.map_replay(records, 'baseline', db)


def test_cli_refuses_overwrite_before_reading_inputs(tmp_path):
    out = tmp_path/'existing.json'
    out.write_text('keep')
    arguments = [word for flag in ('manifest', 'raw-responses', 'baseline-db-replay', 'candidate-db-replay')
                 for word in ('--'+flag, str(tmp_path/'missing.json'))]
    with pytest.raises(SystemExit) as exc:
        scorer.main([*arguments, '--output', str(out)])
    assert exc.value.code == 2
    assert out.read_text() == 'keep'


def test_name_diagnostic_does_not_turn_unknown_alias_into_certain_omission():
    doc = manifest(1)
    doc['cases'][0]['targets'][0]['recognized_names'] = ['달걀 프라이']
    records = [record('baseline', 1, ('달걀프라이',)), record('candidate', 1, ('계란후라이',))]
    out = scorer.score(doc, {'records': records}, replay(records, 'baseline', ['OTHER']), replay(records, 'candidate'))
    before = out['arms']['baseline']['cases'][0]['targets'][0]
    after = out['arms']['candidate']['cases'][0]['targets'][0]
    assert before['name_diagnostic']['recognized_name_runs'] == [1]
    assert before['name_diagnostic']['name_present_but_id_not_hit_runs'] == [1]
    assert after['hits'] == 1
    assert after['name_diagnostic']['recognition_review_needed_runs'] == [1]


def test_existing_regression_checks_keep_gram_rule_and_gate():
    case = {'case_id': 'pizza', 'baseline_case': 'piece_of_pizza'}
    response = {'mealId': 'eval', 'modelVersion': 'gateway', 'safetyStatus': 'SAFE', 'items': [
        {'originalFoodName': '포테이토피자', 'estimatedAmount': 100, 'unit': 'g', 'confidence': 0.9}]}
    records = {('baseline', 'pizza', 1): {'error': None, 'response': response}}
    result = scorer.regression_checks(case, records, 'baseline', 1)
    assert result['gate_passed'] is True
    response['items'][0]['unit'] = '개'
    result = scorer.regression_checks(case, records, 'baseline', 1)
    assert result['gate_passed'] is False
    assert any('gram_unit_default' in reason for reason in result['gate_reasons'])


@pytest.mark.parametrize('change', ['database', 'backend', 'missing_metadata', 'model'])
def test_comparison_parity_rejects_mismatched_environment(change):
    left = {'metadata': {'database': {'food_refs_sha256': 'db'}, 'backend_source_sha256': {'food.py': 'source'}}}
    right = {'metadata': {'database': {'food_refs_sha256': 'db'}, 'backend_source_sha256': {'food.py': 'source'}}}
    raw = {'records': [{'arm': arm, 'gateway_attempts': [{'model': 'model-a'}]} for arm in ('baseline', 'candidate')]}
    if change == 'database':
        right['metadata']['database']['food_refs_sha256'] = 'different'
    elif change == 'backend':
        right['metadata']['backend_source_sha256']['food.py'] = 'different'
    elif change == 'missing_metadata':
        right.clear()
    else:
        raw['records'][1]['gateway_attempts'][0]['model'] = 'model-b'
    with pytest.raises(ValueError):
        scorer.comparison_parity(raw, left, right)


def test_comparison_parity_accepts_matching_sources_and_marks_missing_model_unknown():
    db = {'metadata': {'database': {'food_refs_sha256': 'db'}, 'backend_source_sha256': {'food.py': 'source'}}}
    raw = {'records': [{'arm': arm, 'gateway_attempts': [{'model': 'model-a'}]} for arm in ('baseline', 'candidate')]}
    result = scorer.comparison_parity(raw, db, db)
    assert result['provider_model_status'] == 'same reported model in both arms'
    assert 'unverified' in scorer.comparison_parity({'records': []}, db, db)['provider_model_status']


def test_protocol_image_parity_rejects_changed_hash():
    doc = {'cases': [{'case_id': 'eggs', 'image_sha256': 'frozen'}]}
    scorer.image_parity(doc, {'image_sha256': {'eggs': 'frozen'}})
    with pytest.raises(ValueError):
        scorer.image_parity(doc, {'image_sha256': {'eggs': 'changed'}})
