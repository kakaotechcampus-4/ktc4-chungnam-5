"""`app/food_analyzer.py` 단위 테스트.

실제 Claude 호출(`_call_vision_model`)은 매 PR 테스트에서 모킹한다 — 모델 응답은
비결정적이라 CI 게이트로 쓰지 않는다. 여기선 "모델이 이런 JSON을 줬을 때 코드가
올바르게 처리하는가"(프롬프트 구성, 응답 파싱)만 검증한다.

텍스트 입력은 팀 결정으로 BE 가 food_refs 와 직접 매칭하고 AI 를 거치지 않는다
— 그래서 이 모듈과 테스트엔 텍스트 분석 로직이 없다.
"""

from __future__ import annotations

import asyncio

import pytest

from app.food_analyzer import _build_prompt, _parse_response, analyze_food


def test_analyze_food_parses_model_response(monkeypatch):
    """`_call_vision_model`이 반환한 JSON을 RecognizedItem 목록으로 변환해야 한다."""

    async def _fake_call_vision_model(image_url: str, prompt: str) -> str:
        assert image_url == "https://example.com/meal.jpg"
        return """
        {"items": [
            {"originalFoodName": "닭가슴살", "estimatedAmount": 100, "unit": "g", "confidence": 0.9}
        ]}
        """

    monkeypatch.setattr("app.food_analyzer._call_vision_model", _fake_call_vision_model)

    result = asyncio.run(analyze_food("LUNCH", "https://example.com/meal.jpg"))

    assert len(result) == 1
    assert result[0].original_food_name == "닭가슴살"
    assert result[0].estimated_amount == 100
    assert result[0].confidence == 0.9
    assert result[0].candidate_food_ref_id is None


def test_build_prompt_includes_meal_type():
    prompt = _build_prompt("DINNER")
    assert "DINNER" in prompt


def test_parse_response_parses_valid_json():
    response = '{"items": [{"originalFoodName": "사과", "estimatedAmount": 1, "unit": "개", "confidence": 0.8}]}'
    result = _parse_response(response)
    assert len(result) == 1
    assert result[0].original_food_name == "사과"


def test_parse_response_empty_items():
    assert _parse_response('{"items": []}') == []


def test_parse_response_rejects_malformed_json():
    with pytest.raises(Exception):
        _parse_response("not json")


def test_parse_response_strips_markdown_code_fence():
    """게이트웨이가 JSON 스키마 강제를 지원하는지 몰라서, ```json ... ``` 로 감싸서
    줘도 파싱되도록 방어한다."""
    response = '```json\n{"items": [{"originalFoodName": "김밥", "estimatedAmount": 1, "unit": "줄", "confidence": 0.7}]}\n```'
    result = _parse_response(response)
    assert len(result) == 1
    assert result[0].original_food_name == "김밥"
