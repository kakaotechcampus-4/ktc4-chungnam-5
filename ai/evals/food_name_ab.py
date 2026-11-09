"""Frozen paired prompt experiment using the existing vision client; no DB writes.

Run from ai/ with ``python -m evals.food_name_ab --help``. Both arms use one
settings snapshot. Outputs contain raw responses and errors, never a quality gate.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import subprocess
from time import perf_counter
from urllib.parse import quote


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def baseline_builder(source: str):
    """Execute only the repository's prompt function, never historical module code."""
    functions = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == '_build_prompt']
    if len(functions) != 1:
        raise ValueError('Expected one baseline prompt function')
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<baseline-prompt>', 'exec'), namespace)
    return namespace['_build_prompt']


def load_cases(manifest: dict, root: Path) -> list[dict]:
    cases = manifest.get('cases', [])
    if not cases:
        raise ValueError('Manifest has no cases')
    seen = set()
    for case in cases:
        identifier = case['case_id']
        if not re.fullmatch(r'[a-z0-9_-]+', identifier) or identifier in seen:
            raise ValueError('Invalid or duplicate case_id')
        seen.add(identifier)
        if case['split'] not in {'development', 'holdout', 'regression'}:
            raise ValueError('Unknown case split')
        if case['meal_type'] not in {'BREAKFAST', 'LUNCH', 'DINNER', 'SNACK'}:
            raise ValueError('Unknown meal type')
        relative = Path(case['image_path'])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root.resolve()):
            raise ValueError('Image must be inside repository')
        if path.suffix not in {'.jpg', '.jpeg', '.png'} or not path.is_file():
            raise ValueError('Image is missing or unsupported')
    return cases


def schedule(cases: list[dict], n: int):
    for case_index, case in enumerate(cases):
        for run in range(1, n + 1):
            arms = ('baseline', 'candidate') if (case_index + run) % 2 else ('candidate', 'baseline')
            for arm in arms:
                yield case, run, arm


def write_json(path: Path, data) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def redact_values(value, redact):
    """Apply the same credential protection to raw and parsed response strings."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_values(item, redact) for item in value]
    if isinstance(value, dict):
        return {redact_values(key, redact): redact_values(item, redact) for key, item in value.items()}
    return value


def gateway_attempt_evidence(body, redact) -> dict:
    """Keep safe attempt evidence even when the client rejects the envelope.

    Raw content is evaluation-only and redacted before storage. Length describes
    the provider's original string, before redaction. Missing/non-text content
    remains None; an arbitrary finish reason is never copied into evidence.
    """
    evidence = {'raw_text': None, 'response_chars': None, 'finish_reason': None}
    if not isinstance(body, dict):
        return evidence
    if isinstance(body.get('model'), str):
        evidence['model'] = redact(body['model'])[:200]
    if isinstance(body.get('usage'), dict):
        evidence['usage'] = {
            redact(key): value for key, value in body['usage'].items()
            if isinstance(key, str) and isinstance(value, (int, float))
            and not isinstance(value, bool) and (isinstance(value, int) or math.isfinite(value))
        }
    choices = body.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return evidence
    choice = choices[0]
    reason = choice.get('finish_reason')
    allowed = {'stop', 'length', 'content_filter', 'tool_calls', 'function_call'}
    evidence['finish_reason'] = reason if isinstance(reason, str) and reason in allowed else (
        None if reason is None else 'other')
    message = choice.get('message')
    if isinstance(message, dict) and isinstance(message.get('content'), str):
        raw = message['content']
        evidence['raw_text'] = redact(raw)
        evidence['response_chars'] = len(raw)
    return evidence


def track_gateway_posts(original_post, attempts: list[dict], redact):
    """Wrap HTTP attempts, including rejected responses and transport failures."""
    async def tracked_post(client, *positional, **keyword):
        attempt = {'index': len(attempts) + 1}
        attempts.append(attempt)
        try:
            response = await original_post(client, *positional, **keyword)
            attempt['status_code'] = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = None
            attempt.update(gateway_attempt_evidence(body, redact))
            return response
        except Exception as exc:
            attempt['error'] = type(exc).__name__
            raise
    return tracked_post


def replay_documents(records: list[dict], prompt_hashes: dict) -> dict:
    """Only parsed responses go to DB replay; failed/missing runs remain explicit."""
    documents = {}
    for arm in ('baseline', 'candidate'):
        results, run_numbers = {}, {}
        case_ids = dict.fromkeys(record['case_id'] for record in records if record['arm'] == arm)
        for case in case_ids:
            successful = [r for r in records if r['arm'] == arm and r['case_id'] == case and r['error'] is None]
            if not successful:
                continue
            samples = [{'response': r['response'], 'source_run': r['run']} for r in successful]
            run_numbers[case] = [r['run'] for r in successful]
            for axis in ('amount_accuracy', 'confidence_calibration'):
                results[f'{case}/{axis}'] = {'n': len(samples), 'samples': samples}
        documents[arm] = {
            'rule_version': 'food-name-ab-observations-v1', 'prompt_hash': prompt_hashes[arm],
            'not_an_accuracy_evaluation': True,
            'warning': 'DB replay contains successful parses only; use raw_responses.json for all failures and denominators.',
            'successful_source_runs': run_numbers, 'results': results,
        }
    return documents


async def collect(cases, n, prompts, base_url, out, call_model, parse_response,
                  attempts, redact, prompt_hashes):
    records, consecutive_errors, stopped = [], 0, None
    for case, run, arm in schedule(cases, n):
        start, attempt_start = perf_counter(), len(attempts)
        record = {'case_id': case['case_id'], 'split': case['split'], 'run': run, 'arm': arm,
                  'started_at_utc': datetime.now(timezone.utc).isoformat(), 'error': None,
                  'raw_text': None, 'response': None}
        try:
            raw = await call_model(base_url + '/' + quote(case['image_path'], safe='/'),
                                   prompts[arm][case['meal_type']])
            record['raw_text'] = redact(raw)
            items = parse_response(raw)
            record['response'] = redact_values({'mealId': f"ab-{case['case_id']}-{run}",
                                  'modelVersion': 'gemini-flash-gateway', 'safetyStatus': 'SAFE',
                                  'items': [item.model_dump(mode='json', by_alias=True) for item in items]}, redact)
            consecutive_errors = 0
        except Exception as exc:
            record['error'] = type(exc).__name__
            consecutive_errors += 1
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status in {400, 401, 403}:
                stopped = 'gateway_request_rejected'
            elif consecutive_errors >= 2:
                stopped = 'two_consecutive_failed_calls'
        record['gateway_attempts'] = attempts[attempt_start:]
        record['elapsed_seconds'] = round(perf_counter() - start, 3)
        records.append(record)
        state = {'planned_calls': len(cases) * n * 2, 'completed_calls': len(records),
                 'failed_calls': sum(r['error'] is not None for r in records),
                 'actual_gateway_attempts': len(attempts), 'stopped_reason': stopped,
                 'records': records}
        write_json(out / 'raw_responses.json', state)
        for name, document in replay_documents(records, prompt_hashes).items():
            write_json(out / f'{name}_replay.json', document)
        print(f"{case['case_id']} run={run} arm={arm} status={record['error'] or 'parsed'}", flush=True)
        if stopped:
            break
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--baseline-ref', required=True)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--n', type=int, default=3)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error('Output directory exists; refusing overwrite')
    if args.n < 1:
        parser.error('n must be positive')
    logging.disable(logging.CRITICAL)
    try:
        from app import food_analyzer
        from app.config import Settings
        from evals.fixture_server import serve_fixtures
        import httpx

        root = args.repo_root.resolve()
        raw_manifest = args.manifest.read_bytes()
        manifest = json.loads(raw_manifest)
        cases = load_cases(manifest, root)
        source = subprocess.check_output(['git', '-C', str(root), 'show',
                                         f'{args.baseline_ref}:ai/app/food_analyzer.py'],
                                        text=True, stderr=subprocess.DEVNULL)
        baseline = baseline_builder(source)
        meal_types = sorted({case['meal_type'] for case in cases})
        prompts = {'baseline': {meal: baseline(meal) for meal in meal_types},
                   'candidate': {meal: food_analyzer._build_prompt(meal) for meal in meal_types}}
        prompt_hashes = {arm: sha(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
                         for arm, value in prompts.items()}
        if prompt_hashes['baseline'] == prompt_hashes['candidate']:
            raise ValueError('Both prompts are identical')
        settings = Settings(_env_file=args.env_file) if args.env_file else food_analyzer.get_settings()
        if not settings.LLM_VISION_URL or not settings.LLM_API_KEY.get_secret_value():
            raise ValueError('Vision credentials unavailable')
        secrets = [settings.LLM_API_KEY.get_secret_value(), settings.LLM_VISION_URL,
                   settings.LLM_REASONING_URL]

        def redact(value):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, '[REDACTED]')
            return value

        args.output_dir.mkdir(parents=True, exist_ok=False)
        snapshot = {'created_at_utc': datetime.now(timezone.utc).isoformat(),
                    'manifest_sha256': sha(raw_manifest), 'manifest': manifest,
                    'image_sha256': {c['case_id']: sha((root/c['image_path']).read_bytes()) for c in cases},
                    'baseline_ref': args.baseline_ref, 'baseline_source_sha256': sha(source.encode()),
                    'candidate_source_sha256': sha((root/'ai/app/food_analyzer.py').read_bytes()),
                    'prompt_hashes': prompt_hashes, 'prompts': prompts, 'n': args.n,
                    'planned_calls': len(cases)*args.n*2,
                    'order': 'case order, repetition, alternating baseline/candidate first by case+run parity',
                    'settings': {'timeout_seconds': settings.LLM_TIMEOUT_SEC,
                                 'max_retries': settings.LLM_MAX_RETRIES},
                    'model_identity_note': 'Same configured gateway for both arms; provider metadata recorded when available.'}
        snapshot['execution_scope'] = (
            'Calls production food_analyzer vision client and parser only, not the full API or safety_guard. '
            'mealId, modelVersion and safetyStatus are evaluation scaffolding; SAFE is not a safety classifier result. '
            'Envelope/schema validation and HTTP retries share the configured gateway attempt budget. '
            'gateway_attempts retains each attempt\'s redacted content, allowlisted finish reason and original '
            'character count, including responses rejected before the client returns. '
            'Record raw_text is the final returned content when available. '
            'Actual provider model metadata is recorded in gateway_attempts when supplied.')
        write_json(args.output_dir/'protocol.json', snapshot)
        attempts = []
        original_post, original_settings = httpx.AsyncClient.post, food_analyzer.get_settings

        food_analyzer.get_settings = lambda: settings
        httpx.AsyncClient.post = track_gateway_posts(original_post, attempts, redact)
        try:
            with serve_fixtures(root) as base_url:
                state = asyncio.run(collect(cases, args.n, prompts, base_url, args.output_dir,
                                            food_analyzer._call_vision_model, food_analyzer._parse_response,
                                            attempts, redact, prompt_hashes))
        finally:
            httpx.AsyncClient.post = original_post
            food_analyzer.get_settings = original_settings
        return 1 if state['failed_calls'] or state['stopped_reason'] else 0
    except Exception as exc:
        print(f'Experiment failed ({type(exc).__name__}); details omitted to protect credentials.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
