"""수동 평가 도구가 실서비스 처리 경로와 오류 원문을 올바르게 기록하는지 확인한다."""
import asyncio
from pathlib import Path

import httpx
import pytest

from app.schemas import ShortFeedbackRequest
from scripts.try_short_feedback import evaluate, main, prepare
from tests.test_short_feedback import _meal


@pytest.mark.parametrize('case', ['meal', 'meal_unknown', 'daily', 'breakfast', 'skipped', 'dinner'])
def test_example_request_and_dry_run(case):
    path = Path(__file__).resolve().parents[1] / 'manual_feedback' / 'cases' / f'{case}.json'
    req = ShortFeedbackRequest.model_validate_json(path.read_text())
    report = prepare(req)
    assert report['rawResponse'] is None
    assert report['modelCalled'] is False
    assert report['userPrompt']
    assert main([str(path), '--dry-run']) == 0


def test_manual_report_shows_calculated_cumulative_progress():
    report = prepare(_meal(weightKg=80, dailyNutritionTotals={'proteinG': 42}))
    protein = report['cumulativeProgress'][0]
    assert (protein['current'], protein['target'], protein['percent']) == (42, 96, 43.8)


@pytest.mark.parametrize('raw, expected', [
    ('{"body":"식사 기록 요약","suggestions":[]}', 'SAFE'),
    ('{"body":"약물 용량을 늘리세요"}', 'BLOCKED'),
])
def test_capture_raw_and_api_safety(monkeypatch, raw, expected):
    calls = []
    async def fake(system, user):
        calls.append((system, user))
        return raw
    monkeypatch.setattr('app.short_feedback._call_reasoning_model', fake)
    req = _meal()
    report = prepare(req)
    asyncio.run(evaluate(req, report))
    assert len(calls) == 1
    assert report['rawResponse'] == raw
    assert report['finalResponse']['safetyStatus'] == expected
    assert report['modelCalled'] is True


def test_bad_json_preserved_for_debugging(monkeypatch):
    async def fake(*_):
        return 'broken json'
    monkeypatch.setattr('app.short_feedback._call_reasoning_model', fake)
    req = _meal()
    report = prepare(req)
    asyncio.run(evaluate(req, report))
    assert report['rawResponse'] == 'broken json'
    assert report['apiStatus'] == 502
    assert report['status'] == 'failed'


def test_blocked_input_does_not_call_model(monkeypatch):
    async def fake(*_):
        pytest.fail('차단된 입력은 모델 호출 금지')
    monkeypatch.setattr('app.short_feedback._call_reasoning_model', fake)
    req = _meal(satiety={'userComment': '약물 용량을 늘리세요'})
    report = prepare(req)
    asyncio.run(evaluate(req, report))
    assert report['modelCalled'] is False
    assert report['finalResponse']['safetyStatus'] == 'BLOCKED'


def test_gateway_failure_does_not_leak_url(monkeypatch):
    async def fake(*_):
        request = httpx.Request('POST', 'https://secret.example')
        response = httpx.Response(401, request=request)
        response.raise_for_status()
    monkeypatch.setattr('app.short_feedback._call_reasoning_model', fake)
    req = _meal()
    report = prepare(req)
    asyncio.run(evaluate(req, report))
    assert report['gatewayStatus'] == 401
    assert report['apiStatus'] == 502
    assert 'secret.example' not in str(report)


def test_invalid_input_is_rejected(tmp_path):
    path = tmp_path / 'invalid.json'
    path.write_text('{}')
    assert main([str(path), '--dry-run']) == 2
