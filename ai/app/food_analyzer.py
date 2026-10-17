"""음식 사진 분석 — BE 계약(`schemas.RecognizedItem`)에 맞춰 반환한다.

팀 결정: 텍스트 입력은 BE 가 food_refs 로 직접 매칭하고 AI 를 거치지 않는다.
그래서 이 모듈은 사진 경로만 책임진다 — 텍스트 분석 로직은 없다.

`mealId`/`modelVersion`/`safetyStatus`로 감싸 `AnalyzeMealResponse`를 완성하는 건
`main.py`의 일이다 — `safety_guard.py` 결과까지 합쳐야 해서 여러 모듈을 조합하는
자리는 여기가 아니라 `main.py`다. 이 파일은 `items` 배열만 책임진다.

`candidate_food_ref_id`는 여기서 채우지 않는다 — `search_foods` tool(BE
`/internal/v1`)이 생기기 전까지 항상 `None`이다(README 절대 규칙 1: AI는 DB를 모른다).
"""

from __future__ import annotations

import anthropic
from pydantic import BaseModel

from app.config import get_settings
from app.schemas import RecognizedItem


class _ImageAnalysisResult(BaseModel):
    """비전 모델 출력 스키마. `items` 하나만 감싼다 — 응답 최상위가 배열이면
    `output_config.format`의 JSON 스키마가 object를 요구해서 못 쓴다."""

    items: list[RecognizedItem]


async def analyze_food(meal_type: str, image_url: str) -> list[RecognizedItem]:
    """음식 사진을 분석하여 RecognizedItem 목록을 반환한다."""
    prompt = _build_prompt(meal_type)
    response_text = await _call_vision_model(image_url, prompt)
    return _parse_response(response_text)


async def _call_vision_model(image_url: str, prompt: str) -> str:
    """Claude 비전 모델을 호출해 JSON 문자열을 받는다.

    이미지를 직접 다운로드해서 base64 로 보내지 않는다 — `image_url` 은 presigned
    URL 이라, Anthropic 서버가 바로 fetch 하도록 `source.type="url"`로 넘긴다
    (AI 는 S3 권한이 없으니 애초에 직접 받을 방법도 없다).
    """
    settings = get_settings()
    client = anthropic.AsyncAnthropic(
        api_key=settings.LLM_API_KEY.get_secret_value(),
        base_url=settings.LLM_BASE_URL,
        timeout=settings.LLM_TIMEOUT_SEC,
        max_retries=settings.LLM_MAX_RETRIES,
    )
    response = await client.messages.create(
        model=settings.LLM_MODEL_VISION,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": image_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": _ImageAnalysisResult.model_json_schema(by_alias=True),
            }
        },
    )

    if response.stop_reason == "refusal":
        # 의료 조언 등으로 거절된 경우 — 여긴 음식 인식만 책임지므로 빈 결과로
        # 처리한다. BLOCKED 로 감싸는 건 main.py/safety_guard.py 의 일이다.
        return '{"items": []}'

    return next(block.text for block in response.content if block.type == "text")


def _build_prompt(meal_type: str) -> str:
    """음식 분석용 프롬프트 생성."""
    return f"""입력된 음식 사진을 분석하세요.

mealType: {meal_type}

각 음식에 대해 다음 정보를 반환하세요.

- originalFoodName: 표준 음식명 (브랜드명·불필요한 수식어는 제거)
- estimatedAmount: 추정 섭취량
- unit: 섭취량 단위
- confidence: 이 인식에 대한 확신도 (0~1)

결과는 JSON 형식으로 반환하세요."""


def _parse_response(response: str) -> list[RecognizedItem]:
    """모델 응답(JSON 문자열)을 RecognizedItem 목록으로 변환한다.

    `candidate_food_ref_id`/`clarify_question`은 여기서 비워 둔다 — 전자는 tool
    연동 전까지 항상 None, 후자는 1차 개발 범위 밖(명세 기준)이라 RecognizedItem
    기본값(None)을 그대로 쓴다.
    """
    return _ImageAnalysisResult.model_validate_json(response).items
