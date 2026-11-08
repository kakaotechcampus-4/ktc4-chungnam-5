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

import base64

import httpx
from pydantic import BaseModel

from app.config import get_settings
from app.food_names import normalize_food_name
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

    실제 호출로 확인된 스펙(2026-10-17):
    - 엔드포인트는 `LLM_VISION_URL` 바로 뒤에 `/v1/chat/completions`를 붙여야 한다 —
      URL 자체에 바로 POST하면 404 `model_not_found`.
    - 이미지는 외부 URL 참조(`image_url.url`에 presigned URL)는 안 받는다 —
      500 `UnicodeDecodeError`로 터진다. base64 data URI로 직접 인코딩해서 보내야 한다.
      `image_url`은 presigned URL이라 AI가 별도 권한 없이 일반 HTTPS GET으로 받을 수
      있다(S3 API를 쓰는 게 아니라서 README 규칙과 안 어긋난다).
    - 응답은 OpenAI 호환 형식 — `choices[0].message.content`.
    """
    settings = get_settings()
    if not settings.LLM_VISION_URL:
        raise ValueError("음식 분석에는 LLM_VISION_URL 설정이 필요하다")
    endpoint = f"{settings.LLM_VISION_URL}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY.get_secret_value()}"}

    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SEC) as client:
        image_response = await client.get(image_url)
        image_response.raise_for_status()
        media_type = image_response.headers.get("content-type", "image/jpeg").split(";")[0]
        data_uri = (
            f"data:{media_type};base64,"
            f"{base64.b64encode(image_response.content).decode('ascii')}"
        )

        last_error: Exception | None = None
        for attempt in range(settings.LLM_MAX_RETRIES + 1):
            try:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "image_url", "image_url": {"url": data_uri}},
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


def _build_prompt(meal_type: str) -> str:
    """음식 분석용 프롬프트 생성.

    BE(`to_grams`)는 g/ml 단위만 자동으로 그램 환산하고, 그 외 단위는 근거 없이
    지어내지 않고 사용자 확인으로 넘긴다. 그래서 그램 추정이 **QQS 채점에 실제로
    반영되는 유일한 경로**다 — 그램을 기본값으로 하고, "낱개/조각으로 센다"는
    이유만으로 그램을 피하지 않는다. 빵 한 조각·고기 한 점처럼 1개의 크기가
    비교적 일정한 음식은 그램으로 추정 가능하다. 예외는 음식 전체가 하나의
    덩어리라서 크기가 표준화돼 있지 않아 사진만으로는 무게를 가늠할 근거가 없는
    경우(피자 한 판 전체, 수박 한 통 등)뿐이다 — 그런 경우만 자연 단위를 쓴다.
    (이전 버전은 "조각으로 세는 음식"을 통째로 자연 단위 예외로 둬서 토스트처럼
    그램 추정이 합리적인 음식까지 그램이 안 나오는 문제가 있었다 — 2026-10-22.)

    2026-11-04: 그램 앵커·confidence 보정 기준을 추가했다 — 둘 다 골든셋 9개 축 중
    관측 축(`amount_accuracy`/`confidence_calibration`, PASS/FAIL 없이 사람이 읽음)이라
    기존 라벨(단위·금지어·필수 음식)은 안 건드리고 그대로 87/87 통과해야 한다. 그램
    앵커가 없으면 모델이 근거 없이 극단값(10g, 2000g 등)을 낼 때 잡을 기준이 없었고,
    confidence 는 "0~1 숫자"라고만 하고 기준을 안 줘서 매번 비슷한 값(0.9대)만 내는
    경향이 있었다 — 가려짐·모호함처럼 실제로 불확실한 상황에서도 구분이 안 됐다.

    2026-11-04(리뷰 반영): 최초 버전은 밥·국·면을 "300~450g" 한 덩어리로 묶었는데,
    리뷰에서 밥 1공기 실제 기준(약 210g)과 안 맞는다는 지적을 받고 분리했다 — 출처:
    밥 1공기 210g(한국영양학회 1인 1회 분량 기준), 국/찌개/탕 1그릇 250~400g(2021
    가정간편식 실측 평균 — 국류 325g·찌개류 305g·탕류 368g), 라면 등 면 요리
    1그릇 400~550g(2010 국민건강영양조사 라면 1인 1회 섭취분량 500mL, 국물 포함
    근사). 반찬·빵/만두/고기 수치는 공식 출처 없는 눈대중 추정값이다.
    """
    return f"""입력된 음식 사진을 분석하세요.

mealType: {meal_type}

각 음식에 대해 다음 정보를 반환하세요.

- originalFoodName: 표준 음식명 (브랜드명·불필요한 수식어는 제거)
  - 사진에서 확인되는 주재료와 조리법 중 음식을 구분하는 핵심 정보는 이름에 남기세요.
  - 브랜드명·홍보 표현·감상이나 분량 설명은 빼되, 음식 종류를 구분하는 재료·조리법까지 생략하지 마세요.
  - 사진에서 확인할 수 없는 재료·고기 종류·조리법을 추측해 추가하지 마세요.
  - 여러 후보를 나열하거나 설명문을 쓰지 말고, 확인 가능한 범위의 간결한 음식명 하나로 쓰세요.
- estimatedAmount: 추정 섭취량
- unit: 섭취량 단위
  - **기본은 그램(g)입니다.** 밥·국·면류는 물론, 빵 조각·고기 한 점·만두 한 개처럼
    낱개/조각으로 보이는 음식도 1개의 크기를 가늠해서 그램으로 추정하세요.
  - 음식 전체가 하나의 덩어리라서 크기가 표준화돼 있지 않아 사진만으로 무게를
    가늠할 근거가 없는 경우(예: 피자 한 판 전체, 수박 한 통)에만 "판"/"개" 같은
    자연스러운 단위를 쓰세요. 이때도 그램을 억지로 지어내지 마세요.
  - 그램을 가늠할 때는 아래를 참고용 기준치로 삼되, 사진에서 명백히 다르면
    기준치를 따르지 말고 실제로 보이는 양을 우선하세요.
    - 밥 1공기: 약 210g (180~240g)
    - 국/찌개/탕 1그릇: 250~400g
    - 라면 등 면 요리 1그릇(국물 포함): 400~550g
    - 반찬 1접시(작은 그릇): 50~150g
    - 빵 1조각/만두 1개/고기 한 점: 30~80g
- confidence: 이 인식에 대한 확신도 (0~1). "이 음식명과 양 추정이 맞을 확률"을
  뜻하며, 매번 비슷한 값을 내지 말고 실제 불확실성에 따라 구분하세요.
  - 음식이 가려지거나 일부만 보여서 추측에 가까우면 0.3~0.6을 쓰세요.
  - 비슷한 음식과 헷갈릴 수 있으면(예: 비슷한 색의 국·찌개) 0.6~0.8을 쓰세요.
  - 음식이 선명하게 보이고 흔히 아는 메뉴라서 확신이 높으면 0.85 이상을 쓰세요.

결과는 다른 설명 없이 아래 형태의 JSON만 반환하세요.

{{"items": [{{"originalFoodName": "...", "estimatedAmount": 0, "unit": "...", "confidence": 0}}]}}"""


def _parse_response(response: str) -> list[RecognizedItem]:
    """모델 응답(JSON 문자열)을 RecognizedItem 목록으로 변환한다.

    스키마 검증 후 등록된 동의 표기만 정규화한다. 음식명 외 필드는 그대로
    보존하며, DB ID나 영양값을 새로 만들지 않는다.

    게이트웨이가 `output_config.format` 같은 스키마 강제를 지원하는지 몰라서,
    마크다운 코드펜스(```json ... ```)로 감싸서 줄 가능성까지 방어적으로 벗겨낸다.
    """
    items = _ImageAnalysisResult.model_validate_json(_strip_code_fence(response)).items
    for item in items:
        item.original_food_name = normalize_food_name(item.original_food_name)
    return items


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return text
