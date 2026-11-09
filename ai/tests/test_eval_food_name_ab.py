"""Experiment integrity without network or model calls."""
import asyncio
import json
import httpx
import pytest

from evals import food_name_ab as ab


def test_historical_prompt_extraction_does_not_run_module_code():
    build = ab.baseline_builder('raise RuntimeError("must not execute")\ndef _build_prompt(meal_type: str) -> str:\n    return f"meal={meal_type}"\n')
    assert build('LUNCH') == 'meal=LUNCH'


def test_schedule_preserves_pairs_and_alternates_order():
    cases = [{'case_id': 'a'}, {'case_id': 'b'}]
    rows = [(c['case_id'], r, a) for c, r, a in ab.schedule(cases, 2)]
    assert rows == [('a', 1, 'baseline'), ('a', 1, 'candidate'),
                    ('a', 2, 'candidate'), ('a', 2, 'baseline'),
                    ('b', 1, 'candidate'), ('b', 1, 'baseline'),
                    ('b', 2, 'baseline'), ('b', 2, 'candidate')]


def test_manifest_rejects_outside_image_and_duplicate_case(tmp_path):
    image = tmp_path/'meal.jpg'
    image.write_bytes(b'photo')
    case = {'case_id': 'meal', 'image_path': 'meal.jpg', 'meal_type': 'LUNCH', 'split': 'holdout'}
    assert ab.load_cases({'cases': [case]}, tmp_path) == [case]
    with pytest.raises(ValueError):
        ab.load_cases({'cases': [case, case]}, tmp_path)
    with pytest.raises(ValueError):
        ab.load_cases({'cases': [{**case, 'image_path': '../meal.jpg'}]}, tmp_path)


def test_replay_excludes_failures_but_keeps_source_run_numbers():
    records = [{'case_id': 'meal', 'arm': 'baseline', 'run': 1, 'error': 'TimeoutError', 'response': None},
               {'case_id': 'meal', 'arm': 'baseline', 'run': 2, 'error': None, 'response': {'items': []}}]
    document = ab.replay_documents(records, {'baseline': 'b', 'candidate': 'c'})['baseline']
    assert document['successful_source_runs'] == {'meal': [2]}
    assert document['results']['meal/amount_accuracy']['n'] == 1
    assert document['results']['meal/amount_accuracy'] == document['results']['meal/confidence_calibration']
    assert document['not_an_accuracy_evaluation'] is True


def test_collect_preserves_raw_parse_failure_and_stops_after_two_failures(tmp_path):
    calls = []
    async def call_model(url, prompt):
        calls.append(prompt)
        return 'malformed JSON'
    def parser(raw):
        raise ValueError('raw cannot parse')
    cases = [{'case_id': 'meal', 'split': 'development', 'image_path': 'meal.jpg', 'meal_type': 'LUNCH'}]
    result = asyncio.run(ab.collect(cases, 3, {'baseline': {'LUNCH': 'old'}, 'candidate': {'LUNCH': 'new'}},
                                   'http://local', tmp_path, call_model, parser, [], lambda s:s,
                                   {'baseline':'b','candidate':'c'}))
    assert calls == ['old', 'new']
    assert result['planned_calls'] == 6
    assert result['completed_calls'] == result['failed_calls'] == 2
    assert result['stopped_reason'] == 'two_consecutive_failed_calls'
    assert result['records'][0]['raw_text'] == 'malformed JSON'
    assert json.loads((tmp_path/'baseline_replay.json').read_text())['results'] == {}


def test_collect_keeps_identical_successful_repetitions_and_empty_items(tmp_path):
    async def call_model(url, prompt):
        return '{"items": []}'
    cases = [{'case_id': 'meal', 'split': 'regression', 'image_path': 'meal.jpg', 'meal_type': 'LUNCH'}]
    result = asyncio.run(ab.collect(cases, 2, {'baseline': {'LUNCH': 'old'}, 'candidate': {'LUNCH': 'new'}},
                                   'http://local', tmp_path, call_model, lambda raw: [], [], lambda s:s,
                                   {'baseline':'b','candidate':'c'}))
    assert result['completed_calls'] == 4
    assert result['failed_calls'] == 0
    assert all(r['response']['items'] == [] for r in result['records'])
    assert 'passed' not in result
    document = json.loads((tmp_path/'candidate_replay.json').read_text())
    assert document['results']['meal/amount_accuracy']['n'] == 2


def test_cli_refuses_existing_output_before_credentials(tmp_path):
    out = tmp_path/'already_exists'
    out.mkdir()
    with pytest.raises(SystemExit) as exc:
        ab.main(['--manifest', 'missing.json', '--baseline-ref', 'unused', '--output-dir', str(out)])
    assert exc.value.code == 2
    assert list(out.iterdir()) == []


def test_parsed_nested_strings_are_redacted_without_altering_other_values(tmp_path):
    class Item:
        def model_dump(self, **kwargs):
            return {'originalFoodName': 'sensitive-token', 'confidence': 0.5,
                    'nested': ['ordinary', {'sensitive-token': 'sensitive-token'}]}
    async def call_model(url, prompt):
        return 'sensitive-token'
    cases = [{'case_id': 'meal', 'split': 'holdout', 'image_path': 'meal.jpg', 'meal_type': 'LUNCH'}]
    state = asyncio.run(ab.collect(cases, 1, {'baseline': {'LUNCH': 'old'}, 'candidate': {'LUNCH': 'new'}},
                                  'http://local', tmp_path, call_model, lambda raw: [Item()], [],
                                  lambda s: s.replace('sensitive-token', '[REDACTED]'),
                                  {'baseline': 'b', 'candidate': 'c'}))
    assert 'sensitive-token' not in json.dumps(state)
    assert state['records'][0]['response']['items'][0]['confidence'] == 0.5
    assert state['records'][0]['response']['items'][0]['nested'][0] == 'ordinary'


@pytest.mark.parametrize('body', [None, [], 'not an envelope', {}, {'choices': []},
                                {'choices': {}}, {'choices': [None]},
                                {'choices': [{'message': {'content': ['not text']}}]}])
def test_attempt_evidence_handles_malformed_or_nontext_content(body):
    assert ab.gateway_attempt_evidence(body, lambda value: value) == {
        'raw_text': None, 'response_chars': None, 'finish_reason': None,
    }


@pytest.mark.parametrize('reason,expected', [
    ('stop', 'stop'), ('length', 'length'), ('content_filter', 'content_filter'),
    ('tool_calls', 'tool_calls'), ('function_call', 'function_call'),
    (None, None), ('private-key', 'other'), ({'private-key': True}, 'other'),
])
def test_attempt_finish_reason_is_allowlisted(reason, expected):
    evidence = ab.gateway_attempt_evidence(
        {'choices': [{'finish_reason': reason, 'message': {'content': ''}}]},
        lambda value: value.replace('private-key', '[REDACTED]'))
    assert evidence == {'raw_text': '', 'response_chars': 0, 'finish_reason': expected}


def test_attempt_redacts_raw_model_and_usage_keys_without_mutating_source():
    secret = 'https://private-gateway.invalid/key'
    body = {'model': secret, 'usage': {secret: 9, 'completion_tokens': 100,
                                    'bool': True, 'nan': float('nan'), 'string': secret},
            'choices': [{'message': {'content': '{"value":"' + secret + '"'},
                         'finish_reason': 'length'}]}
    original_raw = body['choices'][0]['message']['content']
    evidence = ab.gateway_attempt_evidence(body, lambda value: value.replace(secret, '[REDACTED]'))
    assert secret not in json.dumps(evidence, allow_nan=False)
    assert evidence['raw_text'] == '{"value":"[REDACTED]"'
    assert evidence['response_chars'] == len(original_raw)
    assert evidence['model'] == '[REDACTED]'
    assert evidence['usage'] == {'[REDACTED]': 9, 'completion_tokens': 100}
    assert body['choices'][0]['message']['content'] == original_raw


def test_collect_preserves_every_rejected_and_successful_attempt(tmp_path):
    bodies = iter([
        {'choices': [{'finish_reason': 'length', 'message': {'content': 'bad private-key'}}]},
        {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"items": []}'}}]},
        {'choices': [{'finish_reason': 'length', 'message': {'content': 'bad private-key'}}]},
        {'choices': [{'finish_reason': 'stop', 'message': {'content': 'still bad private-key'}}]},
    ])
    attempts = []
    async def original_post(client, *args, **kwargs):
        return httpx.Response(200, json=next(bodies))
    redact = lambda value: value.replace('private-key', '[REDACTED]')
    tracked = ab.track_gateway_posts(original_post, attempts, redact)
    async def call_model(url, prompt):
        # Emulate a shared two-attempt client; rejected content never returns to collect.
        for _ in range(2):
            response = await tracked(None, 'unused')
            raw = response.json()['choices'][0]['message']['content']
            if raw == '{"items": []}':
                return raw
        raise ValueError('private-key must not be recorded from exception details')
    cases = [{'case_id': 'meal', 'split': 'development', 'image_path': 'meal.jpg', 'meal_type': 'LUNCH'}]
    state = asyncio.run(ab.collect(cases, 1, {'baseline': {'LUNCH': 'old'}, 'candidate': {'LUNCH': 'new'}},
                                  'http://local', tmp_path, call_model, lambda raw: [], attempts, redact,
                                  {'baseline': 'b', 'candidate': 'c'}))
    assert state['actual_gateway_attempts'] == 4
    assert state['completed_calls'] == 2 and state['failed_calls'] == 1
    first, second = state['records']
    assert [a['index'] for a in first['gateway_attempts']] == [1, 2]
    assert [a['index'] for a in second['gateway_attempts']] == [3, 4]
    assert first['raw_text'] == '{"items": []}'
    assert second['raw_text'] is None
    assert second['gateway_attempts'][1]['raw_text'] == 'still bad [REDACTED]'
    assert 'private-key' not in (tmp_path/'raw_responses.json').read_text()


def test_tracking_keeps_nonjson_and_transport_attempts_without_exception_details():
    attempts = []
    calls = 0
    async def original_post(client, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502, text='private-key unparseable upstream body')
        raise httpx.ConnectError('private-key transport error')
    tracked = ab.track_gateway_posts(original_post, attempts, lambda value: value.replace('private-key', '[REDACTED]'))
    async def run():
        response = await tracked(None)
        assert response.status_code == 502
        with pytest.raises(httpx.ConnectError):
            await tracked(None)
    asyncio.run(run())
    assert attempts[0] == {'index': 1, 'status_code': 502, 'raw_text': None,
                           'response_chars': None, 'finish_reason': None}
    assert attempts[1] == {'index': 2, 'error': 'ConnectError'}
    assert 'private-key' not in json.dumps(attempts)
