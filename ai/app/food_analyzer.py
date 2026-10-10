"""음식 이미지·텍스트 분석 — displayName/amount/unit 추정.

실제 모델 호출은 아직 없다(TODO). `config.py`(모델 화이트리스트·클라이언트)가
생기면 `_analyze_image`/`_analyze_text` 안의 TODO 자리에 붙인다.

여기서 만든 결과는 `main.py`가 받아서 `schemas.AnalyzeMealResponse`
(`originalFoodName`/`estimatedAmount` 등 BE 계약 필드명)로 옮겨 담는다 — 이 파일
안에서는 displayName/amount/unit 이라는 이름 그대로 쓴다.
"""

from __future__ import annotations

from typing import Any


async def analyze_food(
    meal_type: str,
    image: bytes | None = None,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """음식 이미지 또는 텍스트를 분석하여 displayName, amount, unit 을 반환한다."""
    if image is not None:
        return await _analyze_image(image, meal_type)

    if raw_text is not None:
        return await _analyze_text(raw_text, meal_type)

    raise ValueError("image 또는 raw_text 중 하나는 필요합니다.")


async def _analyze_image(image: bytes, meal_type: str) -> dict[str, Any]:
    """이미지 기반 음식 분석."""
    prompt = _build_prompt(meal_type)

    # TODO:
    # 1. VLM 호출
    # 2. 이미지 전달
    # 3. JSON 응답 수신
    model_response = None

    return _parse_response(model_response)


async def _analyze_text(raw_text: str, meal_type: str) -> dict[str, Any]:
    """텍스트 기반 음식 분석."""
    prompt = _build_prompt(meal_type, raw_text=raw_text)

    # TODO:
    # 1. LLM 호출
    # 2. raw_text 전달
    # 3. JSON 응답 수신
    model_response = None

    return _parse_response(model_response)


def _build_prompt(meal_type: str, *, raw_text: str | None = None) -> str:
    """음식 분석용 프롬프트 생성."""
    text_section = f"\n사용자가 입력한 텍스트: {raw_text}\n" if raw_text is not None else ""

    return f"""입력된 음식 정보를 분석하세요.

mealType: {meal_type}
{text_section}
각 음식에 대해 다음 정보를 반환하세요.

- displayName: 표준 음식명
- amount: 섭취량
- unit: 섭취량 단위

브랜드명이나 불필요한 수식어는 제거하세요.

결과는 JSON 형식으로 반환하세요."""


def _parse_response(response: Any) -> dict[str, Any]:
    """모델 응답을 Food Analysis Response 구조로 변환한다."""
    # TODO: 실제 모델 응답 파싱
    items: list[dict[str, Any]] = []

    return {
        "items": items,
        "clarifyQuestion": None,
    }
