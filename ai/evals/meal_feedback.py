"""끼니 피드백(`/short-feedback` scope=MEAL) 골든 검사.

응답 JSON 이 **지켜야 할 속성**을 코드로 판정한다. 좋은 문장은 무수히 많지만 금지 행동은 짧게
열거되므로 금지 조건 위주다. LLM judge 는 두지 않는다 — 부를 수 있는 모델이 생성 모델뿐이라
자기채점이 된다. 질적 항목은 케이스의 `review` 로 사람이 본다.

판정 기준(영양 판정·끼니 목표)은 `app.short_feedback` 을 그대로 쓴다. 검사기가 따로 기준을
들고 있으면 프롬프트가 보는 숫자와 검사기가 보는 숫자가 조용히 어긋난다.

키워드 판정은 표현을 다 잡지 못한다. 잡은 것은 확실히 틀린 것이고, 못 잡은 것은 `review` 몫이다.
"""

from __future__ import annotations

import re
from typing import Any, Final

from app.schemas import Scope, ShortFeedbackRequest
from app.short_feedback import (
    _FIBER_G_PER_DAY,
    _LABELS,
    _MEAL_EXAMPLE_GOOD,
    _SODIUM_MG_PER_DAY,
    MAX_SUGGESTIONS,
    MEALS_PER_DAY,
    NutrientJudgement,
    _sum_nutrient,
    judge_nutrients,
    parse_response,
)
from evals.golden import CheckResult, Grade

AREA: Final = "meal_feedback"

COMMON_CHECKS: Final = (
    "contract_parseable",
    "no_invented_numbers",
    "no_contradict_rule",
    "unjudged_nutrient_silent",
    "quantity_no_direction",
    "unscored_satiety_silent",
    "no_medical_judgement",
    "suggestions_shape",
    "no_imperative",
    "no_cliche",
    "example_no_leak",
)
"""`_common.json` 의 checks 와 같아야 한다 (테스트가 확인한다)."""

CASE_CHECK: Final = "case_must_not_contain"
SIGNAL_CHECKS: Final = ("signal_must_mention_any", "signal_must_not_contain")

# ─────────────────────────── 금지 표현 ───────────────────────────

_PARTICLE: Final = r"(?:이|가|은|는|도|을|를)?\s*(?:조금\s*|좀\s*|살짝\s*|꽤\s*|많이\s*|다소\s*)?"
_SHORT_WORDS: Final = r"(?:부족|모자|적었|적은)"
_HIGH_WORDS: Final = r"(?:많|초과|높|넘쳤|넘었)"
_ENOUGH_WORDS: Final = r"(?:충분|넉넉|채웠|괜찮)"
_ANY_STATE: Final = rf"(?:{_SHORT_WORDS}|{_HIGH_WORDS}|{_ENOUGH_WORDS}|낮|적당)"

_QUANTITY_DIRECTION: Final = re.compile(
    r"과식|소식|과하게|양이\s*(?:많|적)|(?:많이|적게|덜)\s*(?:드셨|먹었|먹은|드신)"
)
_SATIETY_WORDS: Final = re.compile(r"포만감|배고프|배고파|허기")
_MEDICAL: Final = re.compile(
    r"용량을|용량은|투약을|처방|진단|(?:약|주사)(?:을|를|은|는)?\s*(?:\S+\s+)?(?:끊|중단|줄이|늘리|건너|미루|쉬)"
)
_IMPERATIVE: Final = re.compile(r"드세요|하세요|피하세요|줄이세요|드십시오|드시길")
_CLICHE: Final = re.compile(r"균형\s*잡힌|골고루|건강한 식습관|적당히|도움이 됩니다")
_EXAMPLE_PHRASES: Final = ("참치김밥", *(s["foodName"] for s in _MEAL_EXAMPLE_GOOD["suggestions"]))
"""few-shot 좋은 답의 음식. 케이스 입력에 없는데 나오면 예시를 베낀 것이다."""

_NUMBER_WITH_UNIT: Final = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(kcal|킬로칼로리|칼로리|mg|밀리그램|g|그램)(?![A-Za-z])"
)


# ─────────────────────────── 응답에서 볼 글 ───────────────────────────


def _texts(response: dict[str, Any], *, food_names: bool) -> str:
    """판정할 글을 이어 붙인다. foodName 은 1회분 표현("닭가슴살 100g")이 있어 숫자 검사에서 뺀다."""
    parts = [response.get("body") or "", response.get("reasoning") or ""]
    for s in response.get("suggestions") or []:
        if isinstance(s, dict):
            parts.append(s.get("advice") or "")
            if food_names:
                parts.append(s.get("foodName") or "")
    return "\n".join(parts)


def _allowed_numbers(req: ShortFeedbackRequest, judgements: list[NutrientJudgement]) -> set[float]:
    """응답에 써도 되는 숫자 — 입력값 · 끼니 합계 · 끼니 목표/한도 · 하루 기준."""
    values: set[float] = {_SODIUM_MG_PER_DAY, *_FIBER_G_PER_DAY.values()}
    values |= {v / MEALS_PER_DAY for v in values}
    for item in req.items:
        if item.amount is not None:
            values.add(item.amount)
        if item.nutrition is not None:
            values |= {v for v in item.nutrition.model_dump().values() if v is not None}
    for attr in ("kcal", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg"):
        if (total := _sum_nutrient(req.items, attr)) is not None:
            values.add(total)
    for j in judgements:
        values |= {v for v in (j.current, j.target) if v is not None}
    return {round(v, 1) for v in values} | {float(round(v)) for v in values}


def _contradiction(j: NutrientJudgement) -> re.Pattern[str]:
    """규칙 판정과 반대되는 말. 화면 칩(`nutrients[].state`)과 문장이 부딪히는 경우다."""
    label = _LABELS[j.name]
    if j.state == "OK":
        words = _HIGH_WORDS if j.name == "sodium" else _SHORT_WORDS
    else:  # SHORT · OVER — 부족하거나 넘쳤는데 괜찮다고 쓰면 안 된다
        words = rf"(?:{_ENOUGH_WORDS}|낮았)" if j.name == "sodium" else _ENOUGH_WORDS
    return re.compile(rf"{label}{_PARTICLE}{words}")


# ─────────────────────────── 검사 ───────────────────────────


def check_response(
    case: dict[str, Any], response: dict[str, Any] | None, common: dict[str, Any]
) -> list[CheckResult]:
    """응답 하나를 케이스 기준으로 판정한다. response 가 None 이면 호출 자체가 실패한 것이다."""
    grades: dict[str, Grade] = {name: spec["grade"] for name, spec in common["checks"].items()}
    observe_all = case["grade"] == "observe"

    def result(check: str, passed: bool, detail: str = "", grade: Grade | None = None) -> CheckResult:
        g: Grade = "observe" if observe_all else (grade or grades[check])
        return CheckResult(check, g, passed, "" if passed else detail)

    try:
        if response is None:
            raise ValueError("응답 없음")
        parse_response(response, Scope.MEAL)
    except (ValueError, TypeError) as e:
        # 모양이 깨지면 나머지 검사는 볼 것이 없다. 파싱 실패는 자기 이름으로 따로 센다.
        return [result("contract_parseable", False, str(e))]

    req = ShortFeedbackRequest.model_validate(case["input"])
    judgements = judge_nutrients(req)
    judged = {j.name for j in judgements}
    text = _texts(response, food_names=False)
    text_all = _texts(response, food_names=True)
    head = "\n".join([response.get("body") or "", response.get("reasoning") or ""])

    results = [result("contract_parseable", True)]

    allowed = _allowed_numbers(req, judgements)
    invented = [
        f"{num}{unit}"
        for num, unit in _NUMBER_WITH_UNIT.findall(text)
        if round(float(num.replace(",", "")), 1) not in allowed
    ]
    results.append(result("no_invented_numbers", not invented, f"입력에 없는 숫자: {invented}"))

    hits = [m.group(0) for j in judgements if (m := _contradiction(j).search(text))]
    results.append(result("no_contradict_rule", not hits, f"규칙 판정과 반대: {hits}"))

    unjudged = [label for name, label in _LABELS.items() if name not in judged]
    hits = [m.group(0) for label in unjudged if (m := re.search(rf"{label}{_PARTICLE}{_ANY_STATE}", text))]
    results.append(result("unjudged_nutrient_silent", not hits, f"판정 못 한 영양소를 단정: {hits}"))

    quantity = req.qqs.quantity
    if quantity is None or quantity < 100:
        m = _QUANTITY_DIRECTION.search(text)
        results.append(result("quantity_no_direction", m is None, f"양의 방향을 말함: {m and m.group(0)}"))
    else:
        results.append(result("quantity_no_direction", True))

    if req.qqs.satiety is None:
        m = _SATIETY_WORDS.search(head)
        results.append(result("unscored_satiety_silent", m is None, f"못 잰 포만감을 평가: {m and m.group(0)}"))
    else:
        results.append(result("unscored_satiety_silent", True))

    m = _MEDICAL.search(text_all)
    results.append(result("no_medical_judgement", m is None, f"의료 판단: {m and m.group(0)}"))

    suggestions = response.get("suggestions") or []
    names = [s.get("foodName") for s in suggestions if isinstance(s, dict)]
    shape_ok = 1 <= len(names) <= MAX_SUGGESTIONS and len(set(names)) == len(names) and all(names)
    results.append(result("suggestions_shape", shape_ok, f"제안 {len(names)}개 · 이름 {names}"))

    m = _IMPERATIVE.search(text)
    results.append(result("no_imperative", m is None, f"명령형: {m and m.group(0)}"))

    m = _CLICHE.search(text)
    results.append(result("no_cliche", m is None, f"뻔한 표현: {m and m.group(0)}"))

    eaten = " ".join(item.display_name for item in req.items)
    leaked = [p for p in _EXAMPLE_PHRASES if p in text_all and p not in eaten]
    results.append(result("example_no_leak", not leaked, f"few-shot 예시가 샘: {leaked}"))

    # 케이스에만 있는 금지 표현 — 등급은 케이스 grade 를 따른다
    banned = case["hard"].get("mustNotContain", [])
    if banned:
        found = [b for b in banned if b in text_all]
        results.append(result(CASE_CHECK, not found, f"금지 표현: {found}", grade=case["grade"]))

    # 의심 신호 — 실패가 아니라 관측이다
    any_of = case["signals"].get("mustMentionAny", [])
    if any_of:
        ok = any(word in text_all for word in any_of)
        results.append(result(SIGNAL_CHECKS[0], ok, f"아무것도 언급 안 함: {any_of}", grade="observe"))
    avoid = case["signals"].get("mustNotContain", [])
    if avoid:
        found = [w for w in avoid if w in text_all]
        results.append(result(SIGNAL_CHECKS[1], not found, f"의심 표현: {found}", grade="observe"))

    return results


def expected_judgements(case: dict[str, Any]) -> dict[str, str]:
    """케이스 입력을 실제 규칙(`judge_nutrients`)에 넣은 결과. 데이터셋 검증이 기대값과 비교한다."""
    req = ShortFeedbackRequest.model_validate(case["input"])
    return {j.name: j.state for j in judge_nutrients(req)}
