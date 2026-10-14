"""`app/food_analyzer.py` 단위 테스트.

실제 Claude 호출(`_call_vision_model`)은 매 PR 테스트에서 모킹한다 — 모델 응답은
비결정적이라 CI 게이트로 쓰지 않는다. 여기선 "모델이 이런 JSON을 줬을 때 코드가
올바르게 처리하는가"(프롬프트 구성, 응답 파싱, 분기)만 검증한다.
"""

from __future__ import annotations

import asyncio

import pytest

from app.food_analyzer import _build_prompt, _parse_response, analyze_food


def test_analyze_food_requires_image_or_raw_text():
    with pytest.raises(ValueError):
        asyncio.run(analyze_food("LUNCH"))


def test_analyze_food_with_image_parses_model_response(monkeypatch):
    """`_call_vision_model`이 반환한 JSON을 RecognizedItem 목록으로 변환해야 한다."""

    async def _fake_call_vision_model(image_url: str, prompt: str) -> str:
        assert image_url == "https://example.com/meal.jpg"
        return """
        {"items": [
            {"originalFoodName": "닭가슴살", "estimatedAmount": 100, "unit": "g", "confidence": 0.9}
        ]}
        """

    monkeypatch.setattr("app.food_analyzer._call_vision_model", _fake_call_vision_model)

    result = asyncio.run(analyze_food("LUNCH", image_url="https://example.com/meal.jpg"))

    assert len(result) == 1
    assert result[0].original_food_name == "닭가슴살"
    assert result[0].estimated_amount == 100
    assert result[0].confidence == 0.9
    assert result[0].candidate_food_ref_id is None


def test_analyze_food_with_raw_text_returns_empty_list_stub():
    """텍스트 경로는 팀 결정 대기 중이라 아직 TODO — 항상 빈 목록."""
    result = asyncio.run(analyze_food("LUNCH", raw_text="닭가슴살 100g 먹었어"))
    assert result == []


def test_build_prompt_includes_meal_type():
    prompt = _build_prompt("DINNER")
    assert "DINNER" in prompt


def test_build_prompt_includes_raw_text_when_given():
    """텍스트 분석 프롬프트엔 사용자가 쓴 내용이 실제로 들어가야 한다 — 안 들어가면
    모델이 mealType 만 보고 무엇을 먹었는지 전혀 모른다."""
    prompt = _build_prompt("LUNCH", raw_text="허닭 닭가슴살 먹었어")
    assert "허닭 닭가슴살 먹었어" in prompt


def test_build_prompt_omits_raw_text_section_when_not_given():
    prompt = _build_prompt("LUNCH")
    assert "사용자가 입력한 텍스트" not in prompt


def test_parse_response_none_returns_empty_list():
    """`_analyze_text`가 아직 TODO 라 `None`이 들어올 수 있다."""
    assert _parse_response(None) == []


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
