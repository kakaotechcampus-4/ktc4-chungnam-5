"""Q/Q/S 채점기 — **순수 함수다** (절대 규칙 2).

DB 도 네트워크도 모른다. AI 가 죽어도 점수는 남는다는 게 이 층의 존재 이유다.

## 지금은 Satiety 만 있다

API 명세는 응답 모양(`scores.quantity` · `quality` · `satiety`)만 정하고 **계산식을
주지 않는다.** Satiety 만 예외인데, 명세 예시가 요청 `satietyAfterPct: 68` 을 응답
`scores.satiety: 68` 로 그대로 내보내기 때문이다.

Quantity · Quality 는 기준선이 있어야 매길 수 있다:

- Quantity — 개인 baseline 대비 **감소폭**(절대 규칙 4 · D7)을 단계별 목표 범위에
  비추어야 한다. 그 범위가 미정이다.
- Quality — 영양소 목표 대비 충족도인데, 명세의 `nutrients[].target` 이 전부
  `null` 이고 "미확정" 이라고 적혀 있다.

그래서 **None 을 낸다.** 0 을 쓰면 "못 쟀다" 와 "바닥이다" 가 같은 값이 되고,
`qqs_evaluations.*_score` 가 NULL 허용인 것도 그래서다. 기준선이 정해지면 여기에
함수를 더하고 `stage_profile` 에 수치를 채운다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class Scores(NamedTuple):
    """명세 응답의 `scores`. 0~100 정수이고, 근거가 없으면 None.

    **합치지 않는다** (절대 규칙 3 · D9). `total_score` 도 가중치도 없다.
    """

    quantity: int | None
    quality: int | None
    satiety: int | None


def score_satiety(satiety_after_pct: int | None) -> int | None:
    """사용자가 말한 포만감을 그대로 쓴다.

    명세 예시가 그렇다 — 요청 `satietyAfterPct: 68` 이 응답 `scores.satiety: 68`
    로 나간다. 주관 지표라 서버가 보정할 근거가 없다.
    """
    if satiety_after_pct is None:
        return None
    clamped = max(_ZERO, min(_HUNDRED, Decimal(satiety_after_pct)))
    return int(clamped.to_integral_value())


def evaluate(*, satiety_after_pct: int | None) -> Scores:
    """세 축을 매긴다. 기준선이 없는 두 축은 None 이다 (모듈 독스트링 참고)."""
    return Scores(
        quantity=None,
        quality=None,
        satiety=score_satiety(satiety_after_pct),
    )
