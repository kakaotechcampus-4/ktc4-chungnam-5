"""/analyze-meal 더미 응답 생성기.

같은 입력에는 항상 같은 출력을 돌려준다 — 테스트가 assert 를 걸 수 있어야 한다 (S4).
"""

from __future__ import annotations

import hashlib

from agents.schemas import AnalyzeMealRequest, AnalyzeMealResponse, RecognizedItem, SafetyStatus
from app.config import Scenario
from guardrail.medical import BLOCKED_MESSAGE

MODEL_VERSION = "stub-vision-0"

# rawText 가 없을 때 쓰는 고정 픽스처
_FIXTURES = (
    ("참치김밥", 250.0, "g", 0.91),
    ("삶은 계란", 2.0, "개", 0.96),
    ("미역국", 300.0, "ml", 0.88),
)

# 음식명에서 단위를 대충 찍는다. 더미 데이터를 눈으로 따라가기 쉬우라고 둔 것이다.
_UNIT_HINTS = (
    (("국", "탕", "찌개", "스프"), 300.0, "ml"),
    (("계란", "달걀"), 2.0, "개"),
    (("밥", "공기"), 210.0, "g"),
)

# LOW_CONFIDENCE 에서 쓸 신뢰도. 공개 API 의 "0.8 미만은 FE 강조" 기준 아래로 깐다.
_LOW_CONFIDENCES = (0.62, 0.55, 0.41)


def _food_ref_id(name: str) -> str:
    """음식명에서 결정적으로 만든 가짜 food_refs 키."""
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()
    return f"D{digest[:6].upper()}"


def _guess_amount(name: str) -> tuple[float, str]:
    for needles, amount, unit in _UNIT_HINTS:
        if any(needle in name for needle in needles):
            return amount, unit
    return 150.0, "g"


def _seed_items(req: AnalyzeMealRequest) -> list[tuple[str, float, str, float]]:
    if not req.raw_text:
        return list(_FIXTURES)
    names = [chunk.strip() for chunk in req.raw_text.split(",") if chunk.strip()]
    if not names:
        return list(_FIXTURES)
    seeded = []
    for name in names:
        amount, unit = _guess_amount(name)
        seeded.append((name, amount, unit, 0.9))
    return seeded


def build(req: AnalyzeMealRequest, scenario: Scenario) -> AnalyzeMealResponse:
    seeds = _seed_items(req)
    items = []
    for index, (name, amount, unit, confidence) in enumerate(seeds):
        if scenario is Scenario.LOW_CONFIDENCE:
            confidence = _LOW_CONFIDENCES[index % len(_LOW_CONFIDENCES)]
            clarify = f"{name} 양이 이 정도가 맞나요?"
        else:
            clarify = None

        items.append(
            RecognizedItem(
                original_food_name=name,
                estimated_amount=amount,
                unit=unit,
                confidence=confidence,
                candidate_food_ref_id=None if scenario is Scenario.NO_MATCH else _food_ref_id(name),
                clarify_question=clarify,
            )
        )

    return AnalyzeMealResponse(
        meal_id=req.meal_id,
        model_version=MODEL_VERSION,
        safety_status=SafetyStatus.SAFE,
        items=items,
    )


def blocked(req: AnalyzeMealRequest) -> AnalyzeMealResponse:
    """가드레일에 걸린 응답. 인식 결과를 내보내지 않는다."""
    return AnalyzeMealResponse(
        meal_id=req.meal_id,
        model_version=MODEL_VERSION,
        safety_status=SafetyStatus.BLOCKED,
        items=[],
    )


__all__ = ["BLOCKED_MESSAGE", "MODEL_VERSION", "blocked", "build"]
