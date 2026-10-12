"""`app/food_analyzer.py` 단위 테스트. 모델 호출 없음(TODO 상태라 전부 결정적)."""

from __future__ import annotations

import asyncio

import pytest

from app.food_analyzer import _build_prompt, _parse_response, analyze_food


def test_analyze_food_requires_image_or_raw_text():
    with pytest.raises(ValueError):
        asyncio.run(analyze_food("LUNCH"))


def test_analyze_food_with_image_returns_empty_list_stub():
    """모델 호출이 아직 TODO 라 지금은 항상 빈 목록을 돌려줘야 한다."""
    result = asyncio.run(analyze_food("LUNCH", image=b"fake-image-bytes"))
    assert result == []


def test_analyze_food_with_raw_text_returns_empty_list_stub():
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


def test_parse_response_shape_is_stable_regardless_of_input():
    """TODO 가 채워지기 전까지는 어떤 응답이 와도 빈 목록을 돌려준다."""
    assert _parse_response(None) == []
    assert _parse_response({"anything": "goes here"}) == []
