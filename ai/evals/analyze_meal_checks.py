"""`/analyze-meal` 판정 함수 — 비전 모델이 낸 인식 항목이 프롬프트 규칙을 지켰는가를 구조로 판정한다.

모두 순수 함수이고, 예외 없이 `Verdict`(통과?, note)를 돌려준다. 판정에 LLM judge 를 두지 않는다.
`checks.py` 의 텍스트 판정과 달리 단위 · 음식명 · 항목 수 · confidence 같은 **구조**를 본다.
chat · DAILY 사이클이 `checks.py` 를 고치므로 공유 파일 충돌을 줄이려 별도 모듈에 둔다.

사진별 기대값(주요 음식 · 비음식 · 금지어)은 요청 JSON 에 실을 수 없어(BE 계약 밖 필드)
팩토리가 라벨을 클로저로 묶은 Check 를 만든다 (`checks.memo_injection_ignored` 선례).
팩토리가 만든 Check 의 이름은 팩토리 이름과 같다 — 축 id 가 된다.

모양이 틀린 응답(items 가 list 가 아님 · 항목이 dict 가 아님 · 필드 타입 오류)은 판정 축에서 실패,
관측 축에서 통과다 — 하네스가 크래시로 세지 않는다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Sequence

from pydantic import ValidationError

from app.schemas import AnalyzeMealResponse
from evals.harness import Check, Verdict

GRAM_UNITS: frozenset[str] = frozenset({"g", "G", "그램", "ml", "mL", "ML", "밀리리터"})
"""BE `to_grams` 가 환산하는 단위 — `backend/app/services/meal.py` 의 `_GRAM_UNITS | _MILLILITRE_UNITS` 사본.

BE 를 import 하지 않으려고 원소를 복사해 둔다. 일치 검사는
`ai/tests/test_eval_analyze_meal_checks.py` 의 `test_r33_gram_units_match_be_to_grams_units`(AST 판독)가 한다.
BE 는 strip 말고는 정규화하지 않으므로 판정도 `unit.strip()` 이 이 원소 그대로인지만 본다.
"""

_MALFORMED: Verdict = (False, "응답 모양이 틀렸다 — items 가 항목 객체 목록이 아니다")
_EMPTY_LABEL: Verdict = (False, "라벨이 비어 있다 — 판정 불가")


def _verdict(label: str, found: Iterable[str]) -> Verdict:
    """찾은 위반이 있으면 `(False, "<label>: …")`(순서 유지 중복 제거), 없으면 통과.

    `checks._verdict` 와 같은 모양이다 — 공유 파일(`checks.py`)에 묶이지 않으려고 import 하지 않고 둔다.
    """
    unique = list(dict.fromkeys(found))
    return (False, f"{label}: {', '.join(unique)}") if unique else (True, "")

# ─────────────────────────── 응답 읽기 ───────────────────────────


def _items(resp: object) -> list[dict] | None:
    """`items` 의 항목 목록. items 가 list 가 아니거나 항목이 dict 가 아니면 None."""
    items = resp.get("items") if isinstance(resp, dict) else None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return None
    return items


def _names(resp: object) -> list[str] | None:
    """항목 이름 목록. 모양이 틀렸거나 이름이 문자열이 아닌 항목이 있으면 None."""
    items = _items(resp)
    if items is None:
        return None
    names = [item.get("originalFoodName") for item in items]
    return names if all(isinstance(name, str) for name in names) else None


def _name_of(item: dict) -> str:
    name = item.get("originalFoodName")
    return name if isinstance(name, str) else "(이름 없음)"


def _in_gram_units(item: dict) -> bool | None:
    """단위가 BE 가 환산하는 g · ml 인가. 단위가 문자열이 아니면 None."""
    unit = item.get("unit")
    return unit.strip() in GRAM_UNITS if isinstance(unit, str) else None


# ─────────────────────────── 계약 ───────────────────────────


def contract_parseable(status_code: int, payload: object) -> Verdict:
    """200 이고 응답이 `AnalyzeMealResponse` 를 만족하는가. note 에 모델 출력값을 싣지 않는다."""
    if status_code != 200:
        return False, f"status {status_code}"
    if not isinstance(payload, dict):
        return False, f"응답이 JSON 객체가 아니다 ({type(payload).__name__})"
    try:
        AnalyzeMealResponse.model_validate(payload)
    except ValidationError as exc:
        # 입력값(모델 출력)은 note 에 싣지 않는다 — 위치와 오류 종류만 (`checks.contract_parseable` 과 같다)
        where = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors()[:3])
        return False, f"응답 스키마 위반 — {where}"
    return True, ""


# ─────────────────────────── 단위 ───────────────────────────


def _all_units(resp: object, *, gram: bool, label: str) -> Verdict:
    """모든 항목의 단위가 g · ml 인가(`gram=True`) / 아닌가(`gram=False`). 단위가 문자열이 아니면 양쪽 모두 위반."""
    items = _items(resp)
    if items is None:
        return _MALFORMED
    if not items:
        return False, "항목이 없다"
    return _verdict(label, [_name_of(item) for item in items if _in_gram_units(item) is not gram])


def gram_unit_default(resp: dict, req: dict) -> Verdict:
    """낱개 음식도 모든 항목을 BE 가 환산하는 g · ml 단위로 냈는가."""
    return _all_units(resp, gram=True, label="g · ml 이 아닌 단위")


def natural_unit_allowed(resp: dict, req: dict) -> Verdict:
    """통째 덩어리 음식을 g · ml 로 지어내지 않고 자연 단위로 냈는가 — `gram_unit_default` 의 짝."""
    return _all_units(resp, gram=False, label="덩어리 음식을 g · ml 로 냈다")


# ─────────────────────────── 항목 ───────────────────────────


def non_food_empty(resp: dict, req: dict) -> Verdict:
    """음식 없는 사진에 항목을 지어내지 않았는가."""
    items = _items(resp)
    if items is None:
        return _MALFORMED
    return _verdict("음식 없는 사진에 항목을 냈다", [_name_of(item) for item in items])


def no_phantom_food(non_foods: Sequence[str]) -> Check:
    """그릇 · 수저 · 장식(`non_foods`)을 음식 항목으로 내지 않았는가를 판정하는 Check 를 만든다.

    이름을 공백으로 나눈 **낱말**이 라벨과 같을 때만 실패다 — "쟁반짜장" · "공기밥" 은 음식명이라 통과한다.
    """
    words = frozenset(non_foods)

    def no_phantom_food(resp: dict, req: dict) -> Verdict:
        if not words:
            return _EMPTY_LABEL
        names = _names(resp)
        if names is None:
            return _MALFORMED
        return _verdict("비음식을 음식으로 냈다", [name for name in names if not words.isdisjoint(name.split())])

    return no_phantom_food


def _no_space(text: str) -> str:
    return "".join(text.split())


def no_missing_food(required: Sequence[Sequence[str]]) -> Check:
    """주요 음식을 빠뜨리지 않았는가 — 동의어 묶음(`required`)마다 하나가 나와야 통과하는 Check 를 만든다.

    공백을 지운 이름이 공백을 지운 동의어와 **완전히** 같아야 묶음을 채운다 — "김치볶음밥" 은 밥 묶음을 채우지 않는다.
    note 에는 빠진 묶음의 대표명(첫 이름)만 적는다.
    """
    groups = [list(group) for group in required]

    def no_missing_food(resp: dict, req: dict) -> Verdict:
        if not groups or not all(groups):
            return _EMPTY_LABEL
        names = _names(resp)
        if names is None:
            return _MALFORMED
        given = {_no_space(name) for name in names}
        missing = [group[0] for group in groups if given.isdisjoint(map(_no_space, group))]
        return _verdict("빠진 주요 음식", missing)

    return no_missing_food


def _fold(text: str) -> str:
    """NFKC(전각 → 반각) + casefold — 브랜드 표기 차이(ＣＪ · cj)를 같은 문자열로."""
    return unicodedata.normalize("NFKC", text).casefold()


def standard_name(forbidden_words: Sequence[str]) -> Check:
    """음식명에서 브랜드명 · 수식어(`forbidden_words`)를 뺐는가를 판정하는 Check 를 만든다.

    붙여 쓴 브랜드("빙그레바나나우유")도 food_refs 매칭이 빗나가므로 낱말이 아니라 부분 문자열로 본다.
    """
    keys = [_fold(word) for word in forbidden_words]

    def standard_name(resp: dict, req: dict) -> Verdict:
        if not keys:
            return _EMPTY_LABEL
        names = _names(resp)
        if names is None:
            return _MALFORMED
        return _verdict("브랜드 · 수식어가 남았다", [name for name in names if any(k in _fold(name) for k in keys)])

    return standard_name


# ─────────────────────────── 관측 ───────────────────────────


def _observe(resp: object, describe: Callable[[dict], str]) -> Verdict:
    """관측 축 공통 — 항상 통과하고 항목마다 `describe(item)` 을 note 에 적는다. 같은 항목이 둘이면 둘 다 적는다."""
    items = _items(resp)
    if items is None:
        return True, "응답 모양이 틀렸다 — 읽을 항목이 없다"
    if not items:
        return True, "항목 없음"
    return True, ", ".join(describe(item) for item in items)


def amount_accuracy(resp: dict, req: dict) -> Verdict:
    """관측 — 항상 통과하고 note 에 항목별 이름 · 양 · 단위를 남긴다. 정답 g 실측 전까지 사람이 읽는다."""
    return _observe(resp, lambda item: f"{_name_of(item)} {item.get('estimatedAmount')} {item.get('unit')}")


def confidence_calibration(resp: dict, req: dict) -> Verdict:
    """관측 — 항상 통과하고 note 에 항목별 확신도를 남긴다."""
    return _observe(resp, lambda item: f"{_name_of(item)} {item.get('confidence')}")
