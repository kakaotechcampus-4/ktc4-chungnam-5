"""음식 이미지·텍스트 분석 — BE 계약(`schemas.RecognizedItem`)에 맞춰 반환한다.

실제 모델 호출은 아직 없다(TODO). `config.py`(모델 화이트리스트·클라이언트)가
생기면 `_analyze_image`/`_analyze_text` 안의 TODO 자리에 붙인다.

`mealId`/`modelVersion`/`safetyStatus`로 감싸 `AnalyzeMealResponse`를 완성하는 건
`main.py`의 일이다 — `safety_guard.py` 결과까지 합쳐야 해서 여러 모듈을 조합하는
자리는 여기가 아니라 `main.py`다. 이 파일은 `items` 배열만 책임진다.

`candidate_food_ref_id`는 여기서 채우지 않는다 — `search_foods` tool(BE
`/internal/v1`)이 생기기 전까지 항상 `None`이다(README 절대 규칙 1: AI는 DB를 모른다).
"""

from __future__ import annotations

from typing import Any

from app.schemas import RecognizedItem


async def analyze_food(
    meal_type: str,
    image: bytes | None = None,
    raw_text: str | None = None,
) -> list[RecognizedItem]:
    """음식 이미지 또는 텍스트를 분석하여 RecognizedItem 목록을 반환한다."""
    if image is not None:
        return await _analyze_image(image, meal_type)

    if raw_text is not None:
        return await _analyze_text(raw_text, meal_type)

    raise ValueError("image 또는 raw_text 중 하나는 필요합니다.")


async def _analyze_image(image: bytes, meal_type: str) -> list[RecognizedItem]:
    """이미지 기반 음식 분석."""
    prompt = _build_prompt(meal_type)

    # TODO:
    # 1. VLM 호출
    # 2. 이미지 전달
    # 3. JSON 응답 수신
    model_response = None

    return _parse_response(model_response)


async def _analyze_text(raw_text: str, meal_type: str) -> list[RecognizedItem]:
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

- originalFoodName: 표준 음식명 (브랜드명·불필요한 수식어는 제거)
- estimatedAmount: 추정 섭취량
- unit: 섭취량 단위
- confidence: 이 인식에 대한 확신도 (0~1)

결과는 JSON 형식으로 반환하세요."""


def _parse_response(response: Any) -> list[RecognizedItem]:
    """모델 응답을 RecognizedItem 목록으로 변환한다.

    `candidate_food_ref_id`/`clarify_question`은 여기서 비워 둔다 — 전자는 tool
    연동 전까지 항상 None, 후자는 1차 개발 범위 밖(명세 기준)이라 RecognizedItem
    기본값(None)을 그대로 쓴다.
    """
    # TODO: 실제 모델 응답 파싱
    return []
