"""P2 experiments must preserve raw evidence and reject confounded comparisons."""
from copy import deepcopy
import json

import pytest

from evals import food_name_normalization_eval as evaluation


def source_document(name='양송이스프'):
    response = {'mealId': 'saved', 'safetyStatus': 'SAFE', 'modelVersion': 'saved-model',
                'items': [{'originalFoodName': name, 'estimatedAmount': 200, 'unit': 'g',
                           'confidence': .8, 'candidateFoodRefId': None, 'clarifyQuestion': None}]}
    samples = [{'response': deepcopy(response), 'source_run': 2}, {'response': deepcopy(response), 'source_run': 3}]
    return {'prompt_hash': 'saved-prompt', 'rule_version': 'saved-rule', 'successful_source_runs': {'soup': [2, 3]},
            'results': {f'soup/{axis}': {'n': 2, 'samples': deepcopy(samples)} for axis in evaluation.AXES}}


def prepared():
    source = source_document()
    candidate, changes = evaluation.transform_document(source, lambda name: name.replace('스프', '수프'))
    protocol = {'source_sha256': 'source-hash', 'candidate_sha256': 'candidate-hash',
                'source_prompt_hash': 'saved-prompt', 'source_rule_version': 'saved-rule',
                'saved_responses': 2, 'item_occurrences': 2, 'transformations': changes}
    return source, candidate, protocol


def db_replay(document, digest, selected_id='SOUP', nutrition=None):
    rows = []
    for record in evaluation.extract_responses(document):
        for index, item in enumerate(record['response']['items'], 1):
            rows.append({'case': record['case'], 'run': record['run'], 'item_index': index,
                         'original_item': deepcopy(item), 'worker_amount': '200', 'worker_unit': 'g', 'amount_g': '200',
                         'after_129_linked': selected_id is not None,
                         'selected': {'food_ref_id': selected_id, 'name': '스프_양송이 수프', 'category': 'GENERAL'} if selected_id else None,
                         'nutrition': nutrition, 'nutrition_conversion_status': 'calculated_with_missing_nutrients'})
    return {'metadata': {'backend_source_sha256': {'food.py': 'source'},
                         'database': {'food_refs_sha256': 'db', 'food_refs_rows': 10, 'transaction_read_only': 'on'},
                         'baseline_sha256': digest, 'baseline_prompt_hash': 'saved-prompt',
                         'baseline_rule_version': 'saved-rule', 'model_calls': 0},
            'summary': {'food_items': len(rows), 'saved_responses': 2,
                        'after_129_linked': len(rows) if selected_id else 0}, 'items': rows}


def comparison_inputs():
    source, candidate, protocol = prepared()
    before = db_replay(source, 'source-hash', 'OLD', {'kcal': 100, 'fiberG': None})
    after = db_replay(candidate, 'candidate-hash', 'NEW', {'kcal': 110, 'fiberG': 0})
    return source, candidate, protocol, before, after


def test_transformation_preserves_fields_raw_document_and_repetitions():
    source = source_document()
    frozen = deepcopy(source)
    candidate, changes = evaluation.transform_document(source, lambda name: name.replace('스프', '수프'))
    assert source == frozen
    assert [change['run'] for change in changes] == [1, 2]
    assert candidate['successful_source_runs'] == {'soup': [2, 3]}
    pairs, validated_changes = evaluation.input_pairs(source, candidate)
    assert len(pairs) == 2 and validated_changes == changes
    assert candidate['prompt_hash'] == source['prompt_hash']
    for old, new in pairs.values():
        assert new == {**old, 'originalFoodName': '양송이수프'}


def test_prepare_preserves_source_bytes_and_noop_still_has_full_denominator(tmp_path):
    source = tmp_path/'source.json'
    raw = json.dumps(source_document('피자'), ensure_ascii=False).encode() + b'  \n'
    source.write_bytes(raw)
    normalizer = tmp_path/'food_names.py'
    normalizer.write_text('test normalizer')
    protocol = evaluation.prepare(source, tmp_path/'prepared', lambda name: name, normalizer)
    assert source.read_bytes() == raw
    assert (tmp_path/'prepared/source.json').read_bytes() == raw
    assert protocol['changed_item_occurrences'] == 0
    assert protocol['item_occurrences'] == 2
    assert protocol['normalizer_source_sha256'] == evaluation.sha(normalizer.read_bytes())
    assert protocol['source_sha256'] == evaluation.sha(raw)


@pytest.mark.parametrize('change', ['missing_axis', 'different_axis_response', 'missing_sample'])
def test_prepare_rejects_incomplete_or_inconsistent_source(change):
    source = source_document()
    if change == 'missing_axis':
        del source['results']['soup/confidence_calibration']
    elif change == 'different_axis_response':
        source['results']['soup/confidence_calibration']['samples'][0]['response']['items'][0]['confidence'] = .3
    else:
        source['results']['soup/amount_accuracy']['samples'].pop()
    with pytest.raises(ValueError):
        evaluation.transform_document(source, lambda name: name)


@pytest.mark.parametrize('output', ['', ' ', None, 0])
def test_prepare_rejects_invalid_normalizer_output(output):
    with pytest.raises(ValueError, match='Normalizer'):
        evaluation.transform_document(source_document(), lambda _name: output)


def test_comparison_reports_observed_changes_without_calling_them_accuracy():
    result = evaluation.compare(*comparison_inputs())
    assert result['summary']['item_occurrences'] == 2
    assert result['summary']['changed_name_occurrences'] == 2
    assert result['summary']['changed_selected_ids'] == 2
    assert result['summary']['linked_before'] == result['summary']['linked_after'] == 2
    assert result['model_calls'] == 0
    assert 'accuracy' not in result['summary']
    change = result['changes'][0]
    nutrients = {row['field']: row for row in change['nutrient_changes']}
    assert nutrients['kcal']['numeric_delta'] == 10
    assert nutrients['fiberG']['before'] is None
    assert nutrients['fiberG']['after'] == 0
    assert nutrients['fiberG']['numeric_delta'] is None


def test_missing_nutrition_is_never_imputed_to_zero():
    changes = evaluation.nutrient_changes(None, {'kcal': 100, 'fiberG': None})
    assert all(row['numeric_delta'] is None and row['before_present'] is False for row in changes)
    assert evaluation.nutrient_changes({'fiberG': None}, {'fiberG': None}) == []
    assert evaluation.nutrient_changes({'fiberG': None}, {}) == [
        {'field': 'fiberG', 'before': None, 'after': None, 'before_present': True,
         'after_present': False, 'numeric_delta': None}]


@pytest.mark.parametrize('change', ['db', 'backend', 'missing_metadata', 'source_hash', 'prompt', 'rule', 'model_call', 'db_not_readonly'])
def test_comparison_rejects_environment_or_source_mismatch(change):
    inputs = list(comparison_inputs())
    meta = inputs[4]['metadata']
    if change == 'db':
        meta['database']['food_refs_sha256'] = 'different'
    elif change == 'backend':
        meta['backend_source_sha256']['food.py'] = 'different'
    elif change == 'missing_metadata':
        inputs[4].pop('metadata')
    elif change == 'source_hash':
        meta['baseline_sha256'] = 'different'
    elif change == 'prompt':
        meta['baseline_prompt_hash'] = 'different'
    elif change == 'rule':
        meta['baseline_rule_version'] = 'different'
    elif change == 'model_call':
        meta['model_calls'] = 1
    else:
        meta['database']['transaction_read_only'] = 'off'
    with pytest.raises(ValueError):
        evaluation.compare(*inputs)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'wrong_coordinate', 'item_field', 'amount', 'link_flag', 'item_summary', 'link_summary', 'response_summary'])
def test_comparison_rejects_incomplete_or_changed_replay(change):
    inputs = list(comparison_inputs())
    after = inputs[4]
    if change == 'missing':
        after['items'].pop()
    elif change == 'duplicate':
        after['items'].append(deepcopy(after['items'][0]))
    elif change == 'wrong_coordinate':
        after['items'][0]['run'] = 7
    elif change == 'item_field':
        after['items'][0]['original_item']['confidence'] = .3
    elif change == 'amount':
        after['items'][0]['amount_g'] = '300'
    elif change == 'link_flag':
        after['items'][0]['after_129_linked'] = False
    elif change == 'item_summary':
        after['summary']['food_items'] = 3
    elif change == 'link_summary':
        after['summary']['after_129_linked'] = 0
    else:
        after['summary']['saved_responses'] = 3
    with pytest.raises(ValueError):
        evaluation.compare(*inputs)


@pytest.mark.parametrize('field', ['estimatedAmount', 'unit', 'candidateFoodRefId', 'confidence', 'clarifyQuestion'])
def test_comparison_rejects_prepared_non_name_item_changes(field):
    source, candidate, _protocol = prepared()
    for axis in evaluation.AXES:
        candidate['results'][f'soup/{axis}']['samples'][0]['response']['items'][0][field] = 'changed'
    with pytest.raises(ValueError, match='non-name'):
        evaluation.input_pairs(source, candidate)


def test_comparison_rejects_envelope_or_unrelated_metric_changes():
    source, candidate, _protocol = prepared()
    candidate['prompt_hash'] = 'changed'
    with pytest.raises(ValueError, match='outside allowed'):
        evaluation.input_pairs(source, candidate)


@pytest.mark.parametrize('command', ['prepare', 'compare'])
def test_cli_refuses_overwrite_before_reading_or_importing_normalizer(tmp_path, command):
    destination = tmp_path/'existing'
    destination.write_text('keep')
    arguments = ['prepare', '--source', str(tmp_path/'missing'), '--output-dir', str(destination)] if command == 'prepare' else [
        'compare', '--prepared-dir', str(tmp_path/'missing'), '--before-db', str(tmp_path/'missing'),
        '--after-db', str(tmp_path/'missing'), '--output', str(destination)]
    with pytest.raises(SystemExit) as exc:
        evaluation.main(arguments)
    assert exc.value.code == 2
    assert destination.read_text() == 'keep'


def test_noop_comparison_does_not_invent_improvement():
    source = source_document('피자')
    candidate = deepcopy(source)
    protocol = {'source_sha256': 'source-hash', 'candidate_sha256': 'candidate-hash',
                'source_prompt_hash': 'saved-prompt', 'source_rule_version': 'saved-rule',
                'saved_responses': 2, 'item_occurrences': 2, 'transformations': []}
    before = db_replay(source, 'source-hash')
    after = db_replay(candidate, 'candidate-hash')
    result = evaluation.compare(source, candidate, protocol, before, after)
    assert result['changes'] == []
    assert result['summary']['changed_name_occurrences'] == result['summary']['changed_selected_ids'] == 0


@pytest.mark.parametrize('tamper', [None, 'source', 'candidate'])
def test_compare_cli_uses_frozen_files_and_rejects_hash_tampering(tmp_path, tamper):
    source_path = tmp_path/'input.json'
    source_path.write_bytes(evaluation.encoded(source_document()))
    normalizer_path = tmp_path/'food_names.py'
    normalizer_path.write_text('test-only source fingerprint')
    folder = tmp_path/'prepared'
    protocol = evaluation.prepare(source_path, folder, lambda name: name.replace('스프', '수프'), normalizer_path)
    source = json.loads((folder/'source.json').read_bytes())
    candidate = json.loads((folder/'candidate.json').read_bytes())
    before_path, after_path = tmp_path/'before.json', tmp_path/'after.json'
    before_path.write_bytes(evaluation.encoded(db_replay(source, protocol['source_sha256'], 'OLD')))
    after_path.write_bytes(evaluation.encoded(db_replay(candidate, protocol['candidate_sha256'], 'NEW')))
    if tamper:
        path = folder/f'{tamper}.json'
        # Even semantically unchanged bytes must not silently replace evidence.
        path.write_bytes(path.read_bytes() + b' ')
    output = tmp_path/'comparison.json'
    status = evaluation.main(['compare', '--prepared-dir', str(folder), '--before-db', str(before_path),
                              '--after-db', str(after_path), '--output', str(output)])
    if tamper:
        assert status == 1 and not output.exists()
    else:
        assert status == 0
        result = json.loads(output.read_bytes())
        assert result['summary']['changed_selected_ids'] == 2
        assert result['source_sha256']['source'] == protocol['source_sha256']
        assert source_path.read_bytes() == (folder/'source.json').read_bytes()
