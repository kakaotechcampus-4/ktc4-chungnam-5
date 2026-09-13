"""의료 조언 가드레일.

스텁 전용이 아니다 — 실제 LLM 구현도 이 모듈을 그대로 쓴다.
지금은 키워드 목록이 전부다. 실제 연동 시 출력 검증이 여기에 붙는다.
"""

from __future__ import annotations

import re

BLOCKED_MESSAGE = "약물 관련 판단은 담당 의료진과 상의해 주세요."

# 투약 판단을 요구하는 표현. 식사 맥락에서 흔한 말(체중 감량 등)은 일부러 넣지 않는다.
_KEYWORDS = (
    "용량",
    "증량",
    "단약",
    "처방",
    "복용",
    "투여",
    "주사량",
    "약을 끊",
    "약을 늘",
    "약을 줄",
)

_MG = re.compile(r"\d+\s*mg\b", re.IGNORECASE)


def find_violation(*texts: str | None) -> str | None:
    """걸린 표현을 돌려준다. 깨끗하면 None."""
    for text in texts:
        if not text:
            continue
        for keyword in _KEYWORDS:
            if keyword in text:
                return keyword
        match = _MG.search(text)
        if match:
            return match.group(0)
    return None
