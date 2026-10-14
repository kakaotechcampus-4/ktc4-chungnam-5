"""음식 이미지·텍스트 분석 — BE 계약(`schemas.RecognizedItem`)에 맞춰 반환한다.

`mealId`/`modelVersion`/`safetyStatus`로 감싸 `AnalyzeMealResponse`를 완성하는 건
`main.py`의 일이다 — `safety_guard.py` 결과까지 합쳐야 해서 여러 모듈을 조합하는
자리는 여기가 아니라 `main.py`다. 이 파일은 `items` 배열만 책임진다.

`candidate_food_ref_id`는 여기서 채우지 않는다 — `search_foods` tool(BE
`/internal/v1`)이 생기기 전까지 항상 `None`이다(README 절대 규칙 1: AI는 DB를 모른다).

텍스트 입력(`_analyze_text`)은 아직 구현하지 않는다 — LLM을 거칠지, BE가 바로
`food_refs`와 매칭할지 팀 결정이 안 나서 보류 중이다.
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


async def analyze_food(
    meal_type: str,
    image_url: str | None = None,
    raw_text: str | None = None,
) -> list[RecognizedItem]:
    """음식 이미지 또는 텍스트를 분석하여 RecognizedItem 목록을 반환한다."""
    if image_url is not None:
        return await _analyze_image(image_url, meal_type)

    if raw_text is not None:
        return await _analyze_text(raw_text, meal_type)

    raise ValueError("image_url 또는 raw_text 중 하나는 필요합니다.")


async def _analyze_image(image_url: str, meal_type: str) -> list[RecognizedItem]:
    """이미지 기반 음식 분석."""
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


async def _analyze_text(raw_text: str, meal_type: str) -> list[RecognizedItem]:
    """텍스트 기반 음식 분석."""
    prompt = _build_prompt(meal_type, raw_text=raw_text)

    # TODO: 텍스트를 LLM에 보낼지, BE가 food_refs로 바로 매칭할지 팀 결정 대기 중.
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


def _parse_response(response: str | None) -> list[RecognizedItem]:
    """모델 응답(JSON 문자열)을 RecognizedItem 목록으로 변환한다.

    `candidate_food_ref_id`/`clarify_question`은 여기서 비워 둔다 — 전자는 tool
    연동 전까지 항상 None, 후자는 1차 개발 범위 밖(명세 기준)이라 RecognizedItem
    기본값(None)을 그대로 쓴다.

    `_analyze_text`가 아직 TODO 라 `response`로 `None`이 들어올 수 있다 — 그
    경우도 빈 목록으로 처리한다.
    """
    if response is None:
        return []
    return _ImageAnalysisResult.model_validate_json(response).items
