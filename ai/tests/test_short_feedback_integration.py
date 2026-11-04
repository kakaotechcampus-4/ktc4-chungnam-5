"""실제 과금 호출 없이 모델 HTTP 계약, API 및 실패·차단 경로를 검증한다."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import Scope
from app.short_feedback import FeedbackDraft, _call_reasoning_model, generate_short_feedback
from app.safety_guard import BLOCKED_BODY
from tests.test_short_feedback import _meal


def test_gateway_messages_and_retry(monkeypatch):
    calls = []
    def handler(request):
        import json
        calls.append(request)
        assert request.url == 'https://mlapi.run/reasoning/v1/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-key'
        assert json.loads(request.content)['messages'] == [
            {'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'record'}]
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"body":"ok"}'}}]})
    _transport(monkeypatch, handler)
    assert asyncio.run(_call_reasoning_model('rules', 'record')) == '{"body":"ok"}'
    assert len(calls) == 2


def _transport(monkeypatch, handler):
    monkeypatch.setattr('app.short_feedback.get_settings', lambda: SimpleNamespace(
        LLM_REASONING_URL='https://mlapi.run/reasoning/', LLM_TIMEOUT_SEC=5,
        LLM_MAX_RETRIES=1, LLM_API_KEY=SimpleNamespace(get_secret_value=lambda: 'test-key')))
    factory = httpx.AsyncClient
    monkeypatch.setattr('app.short_feedback.httpx.AsyncClient',
                        lambda **kw: factory(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize('failure', ['timeout', 'http', 'envelope', 'content', 'json', 'body'])
def test_model_failures(monkeypatch, failure):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('secret-url', request=request)
        if failure == 'http':
            return httpx.Response(503)
        if failure == 'envelope':
            return httpx.Response(200, json={'choices': []})
        content = {'content': None, 'json': 'not json', 'body': '{}'}[failure]
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    _transport(monkeypatch, handler)
    with pytest.raises((httpx.HTTPError, ValueError)):
        asyncio.run(generate_short_feedback(_meal()))
    assert len(calls) == (2 if failure in ('timeout', 'http') else 1)


@pytest.mark.parametrize('scope', ['MEAL', 'DAILY'])
def test_api_response(monkeypatch, scope):
    async def fake(req):
        return FeedbackDraft('기록 요약', None, [] if req.scope is Scope.MEAL else None)
    monkeypatch.setattr('app.main.generate_short_feedback', fake)
    payload = _meal().model_dump(mode='json', by_alias=True)
    if scope == 'DAILY':
        payload.update(scope='DAILY', date='2026-10-30', mealId=None)
    response = TestClient(app).post('/short-feedback', json=payload)
    assert response.status_code == 200
    assert response.json()['safetyStatus'] == 'SAFE'
    assert response.json()['suggestions'] == ([] if scope == 'MEAL' else None)
    assert response.json()['modelVersion'] == 'reasoning-gateway'


@pytest.mark.parametrize('error, code', [(httpx.ReadTimeout('secret'), 504),
                                         (httpx.ConnectError('secret'), 502),
                                         (ValueError('secret'), 502)])
def test_api_sanitizes_errors(monkeypatch, error, code):
    async def fake(req):
        raise error
    monkeypatch.setattr('app.main.generate_short_feedback', fake)
    response = TestClient(app).post('/short-feedback', json=_meal().model_dump(mode='json'))
    assert response.status_code == code
    assert 'secret' not in response.text


@pytest.mark.parametrize('where', ['input', 'body', 'reasoning', 'suggestion'])
def test_medical_directive_blocked(monkeypatch, where):
    from app.schemas import Suggestion
    unsafe = '약물 용량을 늘리세요'
    async def fake(req):
        assert where != 'input', '차단된 입력은 모델을 부르면 안 된다'
        return FeedbackDraft(unsafe if where == 'body' else '요약',
                             unsafe if where == 'reasoning' else None,
                             [Suggestion(food_name='두부', advice=unsafe)] if where == 'suggestion' else [])
    monkeypatch.setattr('app.main.generate_short_feedback', fake)
    req = _meal(satiety={'userComment': unsafe} if where == 'input' else None)
    response = TestClient(app).post('/short-feedback', json=req.model_dump(mode='json'))
    assert response.status_code == 200
    assert response.json()['safetyStatus'] == 'BLOCKED'
    assert response.json()['body'] == BLOCKED_BODY
    assert response.json()['suggestions'] is None
    assert unsafe not in response.text


def test_api_rejects_invalid_request():
    assert TestClient(app).post('/short-feedback', json={}).status_code == 422


@pytest.mark.parametrize('scope', ['MEAL', 'DAILY'])
def test_api_through_gateway_and_parser(monkeypatch, scope):
    def handler(request):
        import json
        messages = json.loads(request.content)['messages']
        assert messages[0]['role'] == 'system'
        assert messages[1]['role'] == 'user'
        assert ('[날짜]' in messages[1]['content']) == (scope == 'DAILY')
        draft = {'body': '끼니 기록을 정리했어요.', 'reasoning': '채소 추가',
                 'suggestions': [{'foodName': '두부', 'advice': '반찬으로 곁들이기 좋아요',
                                  'candidateFoodRefId': 'invented'}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(draft)}}]})
    # TestClient 자체의 httpx.Client는 바꾸지 않고 비동기 모델 전송만 모킹한다.
    _transport(monkeypatch, handler)
    payload = _meal().model_dump(mode='json')
    if scope == 'DAILY':
        payload.update(scope='DAILY', date='2026-10-30', meal_id=None)
    response = TestClient(app).post('/short-feedback', json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body['safetyStatus'] == 'SAFE'
    if scope == 'DAILY':
        assert body['reasoning'] is None
        assert body['suggestions'] is None
    else:
        assert body['suggestions'][0]['candidateFoodRefId'] is None


@pytest.mark.parametrize('text', ['두부를 곁들이면 좋아요.', '식이섬유가 부족했어요.',
                                  '유지기에는 영양 구성이 중요해요.'])
def test_food_observations_are_not_blocked(text):
    from app.safety_guard import is_unsafe
    assert not is_unsafe(text)
