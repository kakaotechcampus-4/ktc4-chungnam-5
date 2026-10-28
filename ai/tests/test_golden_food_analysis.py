"""음식 분석 golden dataset 과 검사기(`evals/food_analysis.py`) 테스트. 모델 호출 없음.

케이스(메뉴젠 사진 6장)는 g 이 확인되지 않아 양 오차를 보지 않는다. 그래서 양 검사는 확인된 g 이 있는
`_template.json` 으로 본다.
"""

from __future__ import annotations

import json

import pytest

from evals.food_analysis import (
    CASES_DIR,
    DEFAULT_AMOUNT_TOLERANCE,
    Item,
    check,
    load_cases,
    parse_case,
    parse_items,
)

CASES = load_cases()
TEMPLATE = parse_case(json.loads((CASES_DIR / "_template.json").read_text(encoding="utf-8")))
MEAL_TAGS = {"meal:BREAKFAST", "meal:LUNCH", "meal:DINNER"}  # 간식(SNACK)은 아직 다루지 않는다


def _items(*triples: tuple[str, float, str]) -> list[Item]:
    return [Item(name, amount, unit) for name, amount, unit in triples]


PERFECT = _items(("김치찌개", 400, "g"), ("공깃밥", 210, "g"), ("계란말이", 80, "g"))


# ─────────────────────────── 데이터셋 ───────────────────────────


def test_template_is_not_a_case():
    assert all(not c.id.startswith("_") for c in CASES)


@pytest.mark.parametrize("case", [TEMPLATE, *CASES], ids=lambda c: c.id)
def test_case_shape(case):
    """입력은 사진 하나다. 정답 음식마다 허용 이름이 있고 양은 실측 g 이다."""
    assert case.image_url.startswith("https://")
    request = case.request()
    assert request.image_url == case.image_url and request.raw_text is None
    meal_tags = [t for t in case.tags if t.startswith("meal:")]
    assert meal_tags == [f"meal:{case.meal_type.value}"] and meal_tags[0] in MEAL_TAGS
    assert case.expected
    for e in case.expected:
        assert e.names and all(n.strip() for n in e.names)
        assert e.amount_g > 0
    assert 0 < case.amount_tolerance < 1


def test_ids_match_file_names():
    stems = sorted(p.stem for p in CASES_DIR.glob("*.json") if not p.name.startswith("_"))
    assert sorted(c.id for c in CASES) == stems


# ─────────────────────────── 음식명 ───────────────────────────


def test_perfect_answer_passes():
    result = check(TEMPLATE, PERFECT)
    assert result.violations == []
    assert result.matched == 3


def test_any_allowed_name_and_spacing_match():
    items = _items(("돼지고기 김치찌개", 400, "g"), ("흰쌀밥", 210, "g"), ("달걀말이", 80, "g"))
    assert check(TEMPLATE, items).violations == []


def test_missed_and_extra_items():
    items = _items(("김치찌개", 400, "g"), ("공깃밥", 210, "g"), ("단무지", 30, "g"))
    result = check(TEMPLATE, items)
    assert result.violations == ["빠뜨림: 계란말이", "사진에 없는 음식: 단무지"]
    assert result.matched == 2


def test_wrong_name_counts_as_missed_and_extra():
    items = _items(("된장찌개", 400, "g"), ("공깃밥", 210, "g"), ("계란말이", 80, "g"))
    assert check(TEMPLATE, items).violations == ["빠뜨림: 김치찌개", "사진에 없는 음식: 된장찌개"]


def test_one_item_matches_only_once():
    items = _items(("공깃밥", 210, "g"), ("공깃밥", 210, "g"))
    violations = check(TEMPLATE, items).violations
    assert "사진에 없는 음식: 공깃밥" in violations


# ─────────────────────────── 양 ───────────────────────────


def test_unit_must_be_gram():
    items = _items(("김치찌개", 400, "g"), ("공깃밥", 1, "공기"), ("계란말이", 3, "조각"))
    assert check(TEMPLATE, items).violations == [
        "단위가 g 이 아니다: 공깃밥 1공기",
        "단위가 g 이 아니다: 계란말이 3조각",
    ]


@pytest.mark.parametrize("amount, ok", [(280, True), (279, False), (520, True), (521, False)])
def test_amount_tolerance(amount, ok):
    """정답 400g, 허용 ±30% → 280~520g."""
    items = _items(("김치찌개", amount, "g"), ("공깃밥", 210, "g"), ("계란말이", 80, "g"))
    assert (check(TEMPLATE, items).violations == []) is ok


def test_amount_error_message():
    items = _items(("김치찌개", 400, "g"), ("공깃밥", 100, "g"), ("계란말이", 80, "g"))
    assert check(TEMPLATE, items).violations == ["양 오차: 공깃밥 100g (정답 210g, 허용 ±30%)"]


def test_default_tolerance():
    data = json.loads((CASES_DIR / "_template.json").read_text(encoding="utf-8"))
    del data["amountTolerance"]
    assert parse_case(data).amount_tolerance == DEFAULT_AMOUNT_TOLERANCE


def test_parse_items_from_response():
    raw = {"items": [{"originalFoodName": "공깃밥", "estimatedAmount": 210, "unit": "g", "confidence": 0.9}]}
    assert parse_items(raw) == [Item("공깃밥", 210, "g")]


# ─────────────────────────── 확인 안 된 g · 곁들임 ───────────────────────────


def _case(case_id_prefix: str):
    return next(c for c in CASES if c.id.startswith(case_id_prefix))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_unverified_amount_is_not_judged_but_unit_is(case):
    """메뉴젠 g 은 후보값이다 — 양 오차는 실패로 세지 않는다. g 이 아닌 단위는 여전히 실패다."""
    assert not case.amount_verified
    name = case.expected[0].names[0]
    far_off = case.expected[0].amount_g * 3
    assert check(case, [Item(name, far_off, "g")]).violations == []
    assert check(case, [Item(name, 1, "인분")]).violations == [f"단위가 g 이 아니다: {name} 1인분"]


def test_may_also_contain_is_not_extra():
    case = _case("06")
    items = [Item("고등어구이", 316, "g"), Item("파채", 20, "g")]
    assert check(case, items).violations == []
    assert check(case, [*items, Item("깍두기", 50, "g")]).violations == ["사진에 없는 음식: 깍두기"]


def test_fine_grained_name_is_not_relaxed():
    """궁중떡볶이를 '떡볶이' 로 내면 고추장 떡볶이의 성분이 붙는다 — 틀린 것으로 본다."""
    assert check(_case("04"), [Item("떡볶이", 400, "g")]).violations == ["빠뜨림: 궁중떡볶이", "사진에 없는 음식: 떡볶이"]
