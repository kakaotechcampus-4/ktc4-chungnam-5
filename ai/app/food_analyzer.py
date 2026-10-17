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
import httpx
from pydantic import BaseModel

from app.config import get_settings
from app.schemas import RecognizedItem


class _ImageAnalysisResult(BaseModel):
    """비전 모델 출력 스키마. `items` 하나만 감싼다."""

    items: list[RecognizedItem]


async def analyze_food(meal_type: str, image_url: str) -> list[RecognizedItem]:
    """음식 사진을 분석하여 RecognizedItem 목록을 반환한다."""
    prompt = _build_prompt(meal_type)
    response_text = await _call_vision_model(image_url, prompt)
    return _parse_response(response_text)


async def _call_vision_model(image_url: str, prompt: str) -> str:
    """`LLM_VISION_URL`(mlapi.run 게이트웨이, Gemini Flash)을 호출해 JSON 문자열을 받는다.

    ⚠️ 가정(미확정): 게이트웨이의 실제 요청/응답 스펙 문서를 아직 못 받아서, 가장
    흔한 관례인 OpenAI 호환 chat completions 형식으로 가정하고 짰다 — 요청은
    `{"messages": [...]}`, 응답은 `choices[0].message.content`. 실제 스펙 받으면
    이 함수만 고치면 된다 (호출부 `analyze_food`는 안 바뀜).

    `settings.DEV_VISION_MODEL`이 설정돼 있으면 게이트웨이 대신 Claude API를 직접
    호출한다 — 스펙 확인 전까지 실제 사진 분석이 되는지 보기 위한 임시 경로다.
    """
    settings = get_settings()

    if settings.DEV_VISION_MODEL is not None:
        return await _call_claude_directly(image_url, prompt, settings.DEV_VISION_MODEL)

    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SEC) as client:
        for attempt in range(settings.LLM_MAX_RETRIES + 1):
            try:
                response = await client.post(
                    settings.LLM_VISION_URL,
                    headers={
                        "Authorization": f"Bearer {settings.LLM_API_KEY.get_secret_value()}"
                    },
                    json={
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "image_url", "image_url": {"url": image_url}},
                                    {"type": "text", "text": prompt},
                                ],
                            }
                        ],
                    },
                )
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = exc

    assert last_error is not None
    raise last_error


async def _call_claude_directly(image_url: str, prompt: str, model: str) -> str:
    """⚠️ 임시(개발용) — `config.py::Settings.DEV_VISION_MODEL` 참고. 게이트웨이
    스펙 받으면 이 함수와 호출부 분기를 통째로 지운다."""
    settings = get_settings()
    client = anthropic.AsyncAnthropic(
        api_key=settings.LLM_API_KEY.get_secret_value(),
        timeout=settings.LLM_TIMEOUT_SEC,
        max_retries=settings.LLM_MAX_RETRIES,
    )
    response = await client.messages.create(
        model=model,
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

결과는 다른 설명 없이 아래 형태의 JSON만 반환하세요.

{{"items": [{{"originalFoodName": "...", "estimatedAmount": 0, "unit": "...", "confidence": 0}}]}}"""


def _parse_response(response: str) -> list[RecognizedItem]:
    """모델 응답(JSON 문자열)을 RecognizedItem 목록으로 변환한다.

    `candidate_food_ref_id`/`clarify_question`은 여기서 비워 둔다 — 전자는 tool
    연동 전까지 항상 None, 후자는 1차 개발 범위 밖(명세 기준)이라 RecognizedItem
    기본값(None)을 그대로 쓴다.

    게이트웨이가 `output_config.format` 같은 스키마 강제를 지원하는지 몰라서,
    마크다운 코드펜스(```json ... ```)로 감싸서 줄 가능성까지 방어적으로 벗겨낸다.
    """
    return _ImageAnalysisResult.model_validate_json(_strip_code_fence(response)).items


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return text
