"""`/analyze-meal` 판정 함수 — 비전 모델이 낸 인식 항목이 프롬프트 규칙을 지켰는가를 구조로 판정한다.

모두 순수 함수이고, 예외 없이 `Verdict`(통과?, note)를 돌려준다. 판정에 LLM judge 를 두지 않는다.
`checks.py` 의 텍스트 판정과 달리 단위 · 음식명 · 항목 수 · confidence 같은 **구조**를 본다.
chat · DAILY 사이클이 `checks.py` 를 고치므로 공유 파일 충돌을 줄이려 별도 모듈에 둔다.

사진별 기대값(주요 음식 · 비음식 · 금지어)은 요청 JSON 에 실을 수 없어(BE 계약 밖 필드)
팩토리가 라벨을 클로저로 묶은 Check 를 만든다 (`checks.memo_injection_ignored` 선례).
팩토리가 만든 Check 의 이름은 팩토리 이름과 같다 — 축 id 가 된다.
"""

from __future__ import annotations

from collections.abc import Sequence

from evals.harness import Check, Verdict

GRAM_UNITS: frozenset[str] = frozenset()
"""BE `to_grams` 가 환산하는 단위 — `backend/app/services/meal.py` 의 `_GRAM_UNITS | _MILLILITRE_UNITS` 사본."""


# ─────────────────────────── 계약 ───────────────────────────


def contract_parseable(status_code: int, payload: object) -> Verdict:
    """200 이고 응답이 `AnalyzeMealResponse` 를 만족하는가. note 에 모델 출력값을 싣지 않는다."""
    raise NotImplementedError("contract_parseable 미구현")


# ─────────────────────────── 단위 ───────────────────────────


def gram_unit_default(resp: dict, req: dict) -> Verdict:
    """낱개 음식도 모든 항목을 BE 가 환산하는 g · ml 단위로 냈는가."""
    raise NotImplementedError("gram_unit_default 미구현")


def natural_unit_allowed(resp: dict, req: dict) -> Verdict:
    """통째 덩어리 음식을 g · ml 로 지어내지 않고 자연 단위로 냈는가 — `gram_unit_default` 의 짝."""
    raise NotImplementedError("natural_unit_allowed 미구현")


# ─────────────────────────── 항목 ───────────────────────────


def non_food_empty(resp: dict, req: dict) -> Verdict:
    """음식 없는 사진에 항목을 지어내지 않았는가."""
    raise NotImplementedError("non_food_empty 미구현")


def no_phantom_food(non_foods: Sequence[str]) -> Check:
    """그릇 · 수저 · 장식(`non_foods`)을 음식 항목으로 내지 않았는가를 판정하는 Check 를 만든다."""
    raise NotImplementedError("no_phantom_food 미구현")


def no_missing_food(required: Sequence[Sequence[str]]) -> Check:
    """주요 음식을 빠뜨리지 않았는가 — 동의어 묶음(`required`)마다 하나가 나와야 통과하는 Check 를 만든다."""
    raise NotImplementedError("no_missing_food 미구현")


def standard_name(forbidden_words: Sequence[str]) -> Check:
    """음식명에서 브랜드명 · 수식어(`forbidden_words`)를 뺐는가를 판정하는 Check 를 만든다."""
    raise NotImplementedError("standard_name 미구현")


# ─────────────────────────── 관측 ───────────────────────────


def amount_accuracy(resp: dict, req: dict) -> Verdict:
    """관측 — 항상 통과하고 note 에 항목별 이름 · 양 · 단위를 남긴다. 정답 g 실측 전까지 사람이 읽는다."""
    raise NotImplementedError("amount_accuracy 미구현")


def confidence_calibration(resp: dict, req: dict) -> Verdict:
    """관측 — 항상 통과하고 note 에 항목별 확신도를 남긴다."""
    raise NotImplementedError("confidence_calibration 미구현")
