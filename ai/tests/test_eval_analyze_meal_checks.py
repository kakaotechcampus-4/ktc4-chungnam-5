"""`evals/analyze_meal_checks.py` 단위 테스트 — `/analyze-meal` 구조 판정의 양방향(통과해야 할 답 / 실패해야 할 답)만 본다.

모델 · 네트워크 · 키 없음. 응답 JSON 과 요청 JSON(camelCase)을 손으로 만들어 판정 함수에 넣는다.
테스트 이름의 `r<번호>` 는 계획 §6-1 명세표 행 번호다 (행 28 · 30 은 §11-2 에서 삭제, 행 29 는 29a · 29b 로 나뉨).

팩토리 축(`no_phantom_food` · `no_missing_food` · `standard_name`)은 사진 라벨을 넣어 만든 Check 로 판정한다.
g 단위 사본(`GRAM_UNITS`)은 BE `backend/app/services/meal.py` 를 **import 하지 않고** AST 로 읽어 비교한다 —
ai 는 BE 에 의존하지 않는다(절대 규칙 1). BE 파일이 없으면 skip 이 아니라 실패한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.schemas import AnalyzeMealRequest
from evals.analyze_meal_checks import (
    GRAM_UNITS,
    amount_accuracy,
    confidence_calibration,
    contract_parseable,
    gram_unit_default,
    natural_unit_allowed,
    no_missing_food,
    no_phantom_food,
    non_food_empty,
    standard_name,
)

_REPO = Path(__file__).resolve().parents[2]
_BE_MEAL = _REPO / "backend" / "app" / "services" / "meal.py"

# 사진 라벨 — 팩토리 축에 묶는 값. 행 15 · 16 이 같은 라벨로 양방향을 본다
_NON_FOODS = ["그릇", "젓가락", "쟁반", "뚝배기", "공기"]
_REQUIRED = [["밥", "쌀밥", "흰쌀밥"], ["배추김치", "김치"]]
# 금지어는 부분 문자열로 판정한다(행 25) — "수제" 처럼 표준명 안에도 흔히 들어가는 낱말은 라벨에 넣지 않는다
_FORBIDDEN = ["빙그레", "CJ"]

_MARKER = "ZQX-7731-PAPAYA"


# ─────────────────────────── 빌더 ───────────────────────────


def _req(*, meal_type="LUNCH") -> dict:
    """analyze-meal 요청 JSON. 계약(`AnalyzeMealRequest`)을 만족하는지 여기서 한 번 확인한다."""
    data = {
        "mealId": "meal-1",
        "mealType": meal_type,
        "eatenAt": "2026-10-17T12:00:00+09:00",
        "stage": "MAINTENANCE",
        "imageUrl": "meal.jpg",
    }
    AnalyzeMealRequest.model_validate(data)
    return data


def _item(name="닭가슴살", *, amount=100, unit="g", confidence=0.9) -> dict:
    """인식 항목 하나(camelCase). 제약 위반 행도 만들 수 있게 여기서 검증하지 않는다."""
    return {
        "originalFoodName": name,
        "estimatedAmount": amount,
        "unit": unit,
        "confidence": confidence,
        "candidateFoodRefId": None,
        "clarifyQuestion": None,
    }


def _resp(*items: dict) -> dict:
    """응답 JSON(camelCase). 항목을 안 주면 `items=[]`."""
    return {
        "mealId": "meal-1",
        "modelVersion": "fake-model",
        "safetyStatus": "SAFE",
        "items": list(items),
    }


def _named(*names: str, unit="g") -> dict:
    """이름만 다른 항목들로 된 응답 — 음식명 축(비음식 · 누락 · 표준명)에 쓴다."""
    return _resp(*(_item(name, unit=unit) for name in names))


# ─────────────────────────── contract_parseable ───────────────────────────


def test_r1_contract_passes_valid_200():
    """행 1 — 200 · 스키마 만족 응답은 `(True, "")` 다. BE 가 meal_items 로 저장할 수 있는 응답이다."""
    resp = _resp(_item("닭가슴살", amount=120, unit="g"), _item("현미밥", amount=210, unit="g", confidence=0.7))
    assert contract_parseable(200, resp) == (True, "")


def test_r2_contract_passes_empty_items():
    """행 2 — `items=[]` 인 200 도 계약은 통과한다. 음식 없는 사진의 정답이라 계약이 걸러선 안 된다."""
    ok, _ = contract_parseable(200, _resp())
    assert ok is True


@pytest.mark.parametrize("status", [500, 503])
def test_r3_contract_fails_on_5xx(status):
    """행 3 — 5xx 는 실패하고 note 에 status 숫자를 남긴다. 파싱 실패가 500 으로 나와 사진 경로 전체가 FAILED 가 된다."""
    ok, note = contract_parseable(status, {"detail": "Internal Server Error"})
    assert ok is False
    assert str(status) in note


@pytest.mark.parametrize("status", [201, 404], ids=["r3-201", "r3-404"])
def test_r3_contract_fails_on_non_200_with_valid_body(status):
    """행 3 — status 가 200 이 아니면 본문이 스키마를 만족해도 실패한다. 계약은 "200 + 스키마" 둘 다다 (이슈 표)."""
    ok, note = contract_parseable(status, _resp(_item("닭가슴살", amount=120, unit="g")))
    assert ok is False
    assert str(status) in note


@pytest.mark.parametrize(
    "item",
    [
        pytest.param(_item(unit="g" * 33), id="unit-33"),
        pytest.param(_item(name="가" * 256), id="name-256"),
        pytest.param(_item(confidence=1.2), id="confidence-1.2"),
        pytest.param(_item(confidence=-0.1), id="confidence-minus-0.1"),
        pytest.param(_item(amount=-1), id="amount-minus-1"),
    ],
)
def test_r4_contract_fails_on_constraint_violation(item):
    """행 4 — 단위 33자 · 이름 256자 · 확신도 0~1 밖 · 음수 양은 실패한다. BE meal_items 컬럼에 저장할 수 없다."""
    ok, _ = contract_parseable(200, _resp(item))
    assert ok is False


@pytest.mark.parametrize(
    "item",
    [
        pytest.param(_item(unit="g" * 32), id="unit-32"),
        pytest.param(_item(name="가" * 255), id="name-255"),
        pytest.param(_item(confidence=0), id="confidence-0"),
        pytest.param(_item(confidence=1), id="confidence-1"),
    ],
)
def test_r5_contract_passes_on_boundary_values(item):
    """행 5 — 경계값(단위 32자 · 이름 255자 · 확신도 0 · 1)은 통과한다. 행 4 의 짝 — 경계를 한 칸 안쪽으로 당기지 않는다."""
    ok, _ = contract_parseable(200, _resp(item))
    assert ok is True


def test_r6_contract_note_excludes_model_output():
    """행 6 — 스키마 위반 note 에 모델 출력값(256자 이름)을 싣지 않는다. note 는 baseline · 로그에 남는다."""
    name = (_MARKER * 20)[:256]
    assert len(name) == 256  # 전제: 이름 길이 제약(≤255) 위반
    ok, note = contract_parseable(200, _resp(_item(name=name)))
    assert ok is False
    assert _MARKER not in note
    assert name not in note


@pytest.mark.parametrize("payload", [None, [], [_item()]], ids=["none", "empty-list", "list"])
def test_r7_contract_fails_when_payload_not_object(payload):
    """행 7 — 200 이어도 응답이 JSON 객체가 아니면(None · list) 실패한다. 예외로 하네스를 죽이지 않는다."""
    ok, _ = contract_parseable(200, payload)
    assert ok is False


# ─────────────────────────── gram_unit_default ───────────────────────────


# BE 정본(`_GRAM_UNITS | _MILLILITRE_UNITS`)의 원소를 명시해 둔다 — STUB 의 빈 `GRAM_UNITS` 로 parametrize 하면
# 수집되는 케이스가 0개가 돼 RED 가 공허해진다. `GRAM_UNITS` 자체에 묶인 판정은 아래 `_all_gram_units` 테스트가 본다
_BE_GRAM_UNITS = ["g", "G", "그램", "ml", "mL", "ML", "밀리리터"]


def _space_variants(unit: str) -> list[str]:
    """단위 하나의 공백 변형(그대로 · 앞 · 뒤 · 앞뒤). BE `to_grams` 는 strip 한 뒤 비교한다."""
    return [unit, f" {unit}", f"{unit} ", f" {unit} "]


@pytest.mark.parametrize("unit", [variant for unit in _BE_GRAM_UNITS for variant in _space_variants(unit)])
def test_r8_gram_unit_passes_when_all_items_in_be_units(unit):
    """행 8 — 모든 항목이 BE `to_grams` 가 환산하는 단위(공백은 BE 가 strip)면 통과한다. 그대로 채점에 반영된다."""
    resp = _resp(_item("군만두", amount=150, unit=unit), _item("단무지", amount=20, unit=unit))
    ok, _ = gram_unit_default(resp, _req())
    assert ok is True


def test_r8_gram_unit_passes_for_all_gram_units():
    """행 8 — `GRAM_UNITS` 의 모든 원소(공백 변형 포함)가 통과한다. 판정이 BE 정본 사본(`GRAM_UNITS`)에 묶여 있다.

    사본이 비어 있으면 이 검사가 공허해지므로 비어 있지 않다는 것부터 단언한다.
    """
    assert GRAM_UNITS, "GRAM_UNITS 가 비었다 — 행 8 이 공허하게 통과한다"
    failed = []
    for unit in sorted(GRAM_UNITS):
        for variant in _space_variants(unit):
            resp = _resp(_item("군만두", amount=150, unit=variant), _item("단무지", amount=20, unit=variant))
            ok, _ = gram_unit_default(resp, _req())
            if ok is not True:
                failed.append(variant)
    assert failed == []


@pytest.mark.parametrize("unit", ["개", "조각", "장"])
def test_r9_gram_unit_fails_on_piece_unit(unit):
    """행 9 — 낱개 단위 항목이 하나라도 있으면 실패하고 note 에 그 항목 이름만 남긴다. BE 가 g 으로 못 바꿔 채점에서 빠진다.

    note 는 빠진(환산 못 하는) 것만 적는다 — 통과한 항목(단무지 20g)의 이름은 note 에 없다.
    """
    resp = _resp(_item("단무지", amount=20, unit="g"), _item("군만두", amount=5, unit=unit))
    ok, note = gram_unit_default(resp, _req())
    assert ok is False
    assert "군만두" in note
    assert "단무지" not in note


def test_r9_gram_unit_fails_when_piece_unit_item_comes_first():
    """행 9 — 낱개 단위 항목이 앞에 와도(군만두 5개 · 단무지 20g) 실패한다. 마지막 항목만 보는 판정은 이 답을 통과시킨다.

    note 는 여전히 빠진 항목(군만두)만 적는다.
    """
    resp = _resp(_item("군만두", amount=5, unit="개"), _item("단무지", amount=20, unit="g"))
    ok, note = gram_unit_default(resp, _req())
    assert ok is False
    assert "군만두" in note
    assert "단무지" not in note


@pytest.mark.parametrize("unit", ["그람", "gram", "Gram", "Ml", "gM", "kg", "cc", "oz", "㎖", "ｇ", "m l"])
def test_r10_gram_unit_fails_on_spelling_be_does_not_convert(unit):
    """행 10 — BE 가 인식하지 않는 단위 표기(그람 · gram · Gram · Ml · gM · kg · cc · oz · ㎖ · ｇ · m l)는 실패한다. `to_grams` 가 None 을 낸다.

    BE 는 대소문자를 무시하지 않고 `GRAM_UNITS` 원소 그대로만 받는다 — "Ml" · "gM" 은 그 집합에 없다.
    kg · cc · oz 는 무게 · 부피 단위여도 BE 가 환산하지 않는다 — 판정이 "무게 단위 같아 보이는가" 가 아니라 `GRAM_UNITS` 에 묶인다.
    BE 는 strip 말고는 정규화하지 않는다 — 합자 "㎖" · 전각 "ｇ" · 가운데 공백 "m l" 을 NFKC · 공백 제거로 살려 주지 않는다.
    """
    resp = _resp(_item("군만두", amount=150, unit=unit))
    ok, _ = gram_unit_default(resp, _req())
    assert ok is False


def test_r11_gram_unit_fails_on_no_items():
    """행 11 — 항목 0개는 `gram_unit_default` 실패다. 낱개 음식 사진에서 아무것도 안 낸 것을 단위 통과로 세지 않는다."""
    ok, _ = gram_unit_default(_resp(), _req())
    assert ok is False


# ─────────────────────────── natural_unit_allowed ───────────────────────────


@pytest.mark.parametrize("unit", ["판", "통", "개"])
def test_r12_natural_unit_passes_for_whole_food(unit):
    """행 12 — 통째 덩어리 음식을 자연 단위(판 · 통 · 개)로 내면 통과한다. 근거 없는 g 을 지어내지 않은 답이다."""
    resp = _resp(_item("페퍼로니피자", amount=1, unit=unit))
    ok, _ = natural_unit_allowed(resp, _req())
    assert ok is True


@pytest.mark.parametrize("unit", ["g", "ml", "그램", " g "])
def test_r13_natural_unit_fails_on_gram_for_whole_food(unit):
    """행 13 — 덩어리 음식을 g · ml 로 내면 실패한다 (결정 D). 한 판 전체의 g 은 사진에서 잴 근거가 없다."""
    resp = _resp(_item("페퍼로니피자", amount=1800, unit=unit))
    ok, _ = natural_unit_allowed(resp, _req())
    assert ok is False


def test_r13_natural_unit_fails_when_any_item_in_gram():
    """행 13 — 자연 단위 항목이 있어도 다른 항목을 g 로 냈으면(피자 1판 · 피클 30g) 실패한다. 이 축도 모든 항목을 본다."""
    resp = _resp(_item("페퍼로니피자", amount=1, unit="판"), _item("피클", amount=30, unit="g"))
    ok, _ = natural_unit_allowed(resp, _req())
    assert ok is False


def test_r13_natural_unit_fails_when_gram_item_comes_first():
    """행 13 — g 항목이 앞에 와도(피클 30g · 피자 1판) 실패한다. 마지막 항목만 보는 판정은 이 답을 통과시킨다."""
    resp = _resp(_item("피클", amount=30, unit="g"), _item("페퍼로니피자", amount=1, unit="판"))
    ok, _ = natural_unit_allowed(resp, _req())
    assert ok is False


def test_r13_natural_unit_fails_for_all_gram_units():
    """행 13 — 덩어리 음식의 단위가 `GRAM_UNITS` 의 어느 원소든 실패한다. 이 축도 BE 정본 사본(`GRAM_UNITS`)에 묶여 있다.

    "g" · "ml" 몇 개만 막는 판정은 "밀리리터" · "ML" 로 지어낸 무게를 통과시킨다.
    사본이 비어 있으면 이 검사가 공허해지므로 비어 있지 않다는 것부터 단언한다.
    """
    assert GRAM_UNITS, "GRAM_UNITS 가 비었다 — 행 13 이 공허하게 통과한다"
    passed = []
    for unit in sorted(GRAM_UNITS):
        resp = _resp(_item("페퍼로니피자", amount=1800, unit=unit))
        ok, _ = natural_unit_allowed(resp, _req())
        if ok is not False:
            passed.append(unit)
    assert passed == []


def test_r14_natural_unit_fails_on_no_items():
    """행 14 — 항목 0개는 `natural_unit_allowed` 실패다. 덩어리 음식을 아예 못 낸 것을 단위 통과로 세지 않는다."""
    ok, _ = natural_unit_allowed(_resp(), _req())
    assert ok is False


# ─────────────────────────── no_phantom_food ───────────────────────────


@pytest.mark.parametrize("name", ["그릇", "빈 그릇", "나무 젓가락", "그릇 2개", "젓가락 한 쌍", "흰 그릇 하나"])
def test_r15_phantom_fails_on_non_food_word_name(name):
    """행 15 — 비음식 라벨 낱말이 항목 이름의 낱말이면(처음 · 마지막 낱말이 아니어도) 실패한다. 그릇 · 수저를 음식으로 저장하게 된다."""
    check = no_phantom_food(_NON_FOODS)
    ok, _ = check(_named("된장찌개", name), _req())
    assert ok is False


@pytest.mark.parametrize("name", ["쟁반짜장", "뚝배기불고기", "공기밥", "해물뚝배기"])
def test_r16_phantom_passes_when_non_food_word_is_part_of_food_name(name):
    """행 16 — 비음식 낱말이 음식명의 일부(앞 · 끝 — 쟁반짜장 · 뚝배기불고기 · 공기밥 · 해물뚝배기)면 통과한다.

    행 15 의 과교정을 막는 짝이다. 앞부분 · 뒷부분 일치가 아니라 낱말 단위로 본다.
    """
    assert "뚝배기" in _NON_FOODS  # 전제: "해물뚝배기" 는 라벨 낱말이 끝에 붙은 경우다
    check = no_phantom_food(_NON_FOODS)
    ok, _ = check(_named("된장찌개", name), _req())
    assert ok is True


def test_r17_phantom_fails_on_empty_label():
    """행 17 — 비음식 라벨이 비면 실패한다. 라벨 없이 만든 축이 늘 통과하면 판정한 것처럼 보인다."""
    check = no_phantom_food([])
    ok, _ = check(_named("된장찌개"), _req())
    assert ok is False


# ─────────────────────────── no_missing_food ───────────────────────────


@pytest.mark.parametrize(
    "names",
    [("쌀밥", "배추김치"), ("흰 쌀밥", "김치"), ("밥", "배추 김치")],
    ids=["exact", "space-in-rice", "space-in-kimchi"],
)
def test_r18_missing_passes_when_each_group_named(names):
    """행 18 — 동의어 묶음마다 하나가 나오면(공백 무시) 통과한다. 밥 · 쌀밥 · 흰쌀밥은 같은 음식이다."""
    check = no_missing_food(_REQUIRED)
    ok, _ = check(_named(*names), _req())
    assert ok is True


def test_r19_missing_fails_and_names_missing_group():
    """행 19 — 한 묶음이 빠지면 실패하고 note 에 그 묶음의 대표명(첫 이름)만 남긴다. 무엇을 빠뜨렸는지 사람이 본다.

    note 는 빠진 것만 적는다 — 채워진 묶음(밥 · 쌀밥 · 흰쌀밥)의 대표명 "밥" 은 note 에 없다.
    """
    check = no_missing_food(_REQUIRED)
    ok, note = check(_named("쌀밥", "계란말이"), _req())
    assert ok is False
    assert "배추김치" in note
    assert _REQUIRED[0][0] not in note


@pytest.mark.parametrize(
    "names",
    [("김치볶음밥", "배추김치"), ("쌀밥", "김치찌개"), ("쌀밥", "김치 찌개"), ("콩밥", "배추김치")],
    ids=["r20-suffix-rice", "r20-prefix-kimchi", "r20-word-kimchi", "r20-suffix-rice-short"],
)
def test_r20_missing_substring_does_not_satisfy_group(names):
    """행 20 — 이름의 부분 문자열("김치볶음밥" · "콩밥" 의 "밥" · "김치찌개" 의 앞부분 "김치")은 묶음을 채우지 않는다.

    다른 음식을 맞힌 것으로 세지 않는다 — 공백 제거 후 완전 일치만 인정하고, 앞부분 · 뒷부분 일치도 아니다.
    낱말 단위 일치도 아니다 — "김치 찌개" 의 낱말 "김치" 는 김치찌개를 가리키므로 김치 묶음을 채우지 않는다.
    """
    check = no_missing_food(_REQUIRED)
    ok, _ = check(_named(*names), _req())
    assert ok is False


def test_r21_missing_passes_with_extra_foods():
    """행 21 — 라벨 밖 음식이 더 있어도 통과한다. 이 축은 누락만 보고, 추가는 다른 축(비음식)이 본다."""
    check = no_missing_food(_REQUIRED)
    ok, _ = check(_named("쌀밥", "배추김치", "계란말이", "멸치볶음"), _req())
    assert ok is True


@pytest.mark.parametrize("required", [[], [[]]], ids=["no-groups", "empty-group"])
def test_r22_missing_fails_on_empty_label(required):
    """행 22 — 필수 라벨이 비면 실패한다. 라벨 없이 만든 축이 늘 통과하면 판정한 것처럼 보인다."""
    check = no_missing_food(required)
    ok, _ = check(_named("쌀밥", "배추김치"), _req())
    assert ok is False


# ─────────────────────────── non_food_empty ───────────────────────────


def test_r23_non_food_empty_passes_on_no_items():
    """행 23 — 음식 없는 사진에 항목 0개면 통과한다."""
    ok, _ = non_food_empty(_resp(), _req())
    assert ok is True


def test_r24_non_food_empty_fails_and_names_invented_item():
    """행 24 — 항목이 있으면 실패하고 note 에 지어낸 항목 이름을 남긴다. 없는 식사가 기록된다."""
    ok, note = non_food_empty(_named("식빵"), _req())
    assert ok is False
    assert "식빵" in note


# ─────────────────────────── standard_name ───────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "빙그레 바나나우유",
        "빙그레바나나우유",
        "cj 즉석밥",
        "ＣＪ 즉석밥",
        "바나나우유 빙그레",
        "즉석밥(CJ)",
        "햇반CJ",
        "바나나빙그레우유",
        "즉석CJ밥",
    ],
    ids=[
        "r25-brand",
        "r25-brand-attached",
        "r25-brand-lowercase",
        "r25-brand-fullwidth",
        "r25-brand-last-word",
        "r25-brand-in-parens",
        "r25-brand-attached-end",
        "r25-brand-attached-middle",
        "r25-brand-attached-middle-latin",
    ],
)
def test_r25_standard_name_fails_on_forbidden_word(name):
    """행 25 — 금지 브랜드 · 수식어가 이름에 부분 문자열로 있으면(붙여 씀 · 대소문자 · 전각 무시 · 가운데 · 끝) 실패한다.

    "빙그레바나나우유" 처럼 띄어 쓰지 않은 브랜드도 food_refs 매칭이 빗나가므로 낱말 단위가 아니라 부분 문자열로 본다.
    금지어가 이름 앞에만 오는 것이 아니다 — "바나나우유 빙그레" · "즉석밥(CJ)" · "햇반CJ" 도 실패다.
    앞 · 끝 일치가 아니라 부분 문자열이다 — 낱말 가운데에 붙은 "바나나빙그레우유" · "즉석CJ밥" 도 실패다.
    """
    check = standard_name(_FORBIDDEN)
    ok, _ = check(_named("배추김치", name), _req())
    assert ok is False


def test_r26_standard_name_passes_on_standard_names():
    """행 26 — 표준명(바나나우유 · 즉석밥 · 돈가스)은 통과한다. 행 25 의 짝."""
    names = ("바나나우유", "즉석밥", "돈가스")
    # 전제: 표준명이 금지어를 부분 문자열로도 품지 않는다 — 품으면 행 25 기준상 실패가 정답이다
    assert not [(n, w) for n in names for w in _FORBIDDEN if w.casefold() in n.casefold()]
    check = standard_name(_FORBIDDEN)
    ok, _ = check(_named(*names), _req())
    assert ok is True


def test_r27_standard_name_fails_on_empty_label():
    """행 27 — 금지어 라벨이 비면 실패한다. 라벨 없이 만든 축이 늘 통과하면 판정한 것처럼 보인다."""
    check = standard_name([])
    ok, _ = check(_named("바나나우유"), _req())
    assert ok is False


# ─────────────────────────── 관측 ───────────────────────────


def test_r29a_amount_accuracy_passes_and_notes_name_amount_unit():
    """행 29a — `amount_accuracy` 는 항목이 있으면 통과하고 note 에 이름 · 양 · 단위를 남긴다. 사람이 g 정확도를 읽는다."""
    resp = _resp(_item("식빵", amount=2.5, unit="조각"), _item("우유", amount=180, unit="ml"))
    ok, note = amount_accuracy(resp, _req())
    assert ok is True
    for part in ("식빵", "2.5", "조각", "우유", "180", "ml"):
        assert part in note


def test_r29b_confidence_calibration_passes_and_notes_confidences():
    """행 29b — `confidence_calibration` 은 통과하고 note 에 항목별 확신도를 남긴다. 사람이 보정 여부를 읽는다."""
    resp = _resp(_item("식빵", confidence=0.73), _item("우유", confidence=0.41))
    ok, note = confidence_calibration(resp, _req())
    assert ok is True
    assert "0.73" in note
    assert "0.41" in note


# ─────────────────────────── 모양이 틀린 응답 ───────────────────────────

# 판정 축은 둘로 나눠 담는다 — 이미 만든 Check / (팩토리, 라벨). 팩토리 쪽은 수집 시점이 아니라 테스트 안에서 Check 를 만든다
_JUDGED_CHECKS = [
    pytest.param(gram_unit_default, id="gram_unit_default"),
    pytest.param(natural_unit_allowed, id="natural_unit_allowed"),
    pytest.param(non_food_empty, id="non_food_empty"),
]
_JUDGED_FACTORIES = [
    pytest.param(no_phantom_food, _NON_FOODS, id="no_phantom_food"),
    pytest.param(no_missing_food, _REQUIRED, id="no_missing_food"),
    pytest.param(standard_name, _FORBIDDEN, id="standard_name"),
]
_OBSERVED_CHECKS = [
    pytest.param(amount_accuracy, id="amount_accuracy"),
    pytest.param(confidence_calibration, id="confidence_calibration"),
]
_FACTORIES = {"no_phantom_food", "no_missing_food", "standard_name"}

_MALFORMED = [
    pytest.param({}, id="no-items"),
    pytest.param({"items": None}, id="items-none"),
    pytest.param({"items": "쌀밥"}, id="items-str"),
    pytest.param({"items": [None]}, id="item-none"),
    pytest.param({"items": [{"originalFoodName": 3, "unit": ["g"], "confidence": "높음"}]}, id="item-wrong-types"),
]


def _is_verdict(value: object) -> bool:
    return isinstance(value, tuple) and len(value) == 2 and type(value[0]) is bool and isinstance(value[1], str)


@pytest.mark.parametrize("resp", _MALFORMED)
@pytest.mark.parametrize("check", _JUDGED_CHECKS)
def test_r31_judged_checks_fail_on_malformed_response(check, resp):
    """행 31 — 모양이 틀린 응답에 판정 축은 예외 없이 `(False, note)` 를 낸다. 하네스가 크래시로 세지 않고 실패로 센다."""
    verdict = check(resp, _req())
    assert _is_verdict(verdict)
    assert verdict[0] is False


@pytest.mark.parametrize("resp", _MALFORMED)
@pytest.mark.parametrize(("factory", "label"), _JUDGED_FACTORIES)
def test_r31_judged_factory_checks_fail_on_malformed_response(factory, label, resp):
    """행 31 — 라벨을 묶어 만든 팩토리 축도 모양이 틀린 응답에 예외 없이 `(False, note)` 를 낸다."""
    verdict = factory(label)(resp, _req())
    assert _is_verdict(verdict)
    assert verdict[0] is False


@pytest.mark.parametrize("resp", _MALFORMED)
@pytest.mark.parametrize("check", _OBSERVED_CHECKS)
def test_r31_observed_checks_pass_on_malformed_response(check, resp):
    """행 31 — 관측 축은 모양이 틀린 응답에도 예외 없이 `(True, note)` 를 낸다. 게이트 밖이라 사람이 note 로 읽는다."""
    verdict = check(resp, _req())
    assert _is_verdict(verdict)
    assert verdict[0] is True


@pytest.mark.parametrize(
    ("factory", "label"),
    [(no_phantom_food, _NON_FOODS), (no_missing_food, _REQUIRED), (standard_name, _FORBIDDEN)],
    ids=["no_phantom_food", "no_missing_food", "standard_name"],
)
def test_r32_factory_check_name_is_axis_id(factory, label):
    """행 32 — 팩토리가 만든 Check 의 `__name__` 이 팩토리 이름(= 축 id)과 같다. 축 id 가 결과 키 · baseline 키가 된다."""
    assert factory.__name__ in _FACTORIES
    assert factory(label).__name__ == factory.__name__


# ─────────────────────────── GRAM_UNITS ↔ BE 정본 ───────────────────────────


def _read(path: Path) -> str:
    """BE 소스를 읽는다. 파일이 없으면 FileNotFoundError 그대로 — skip 하지 않는다."""
    return path.read_text(encoding="utf-8")


def _assignments(source: str) -> dict[str, ast.expr]:
    """최상위 `X = ...` · `X: T = ...` 의 이름 → 값 식."""
    found: dict[str, ast.expr] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            found[node.target.id] = node.value
    return found


def _eval_set(node: ast.expr, names: dict[str, ast.expr], seen: frozenset[str] = frozenset()) -> frozenset[str]:
    """문자열 Set 리터럴 · 같은 파일 이름 · `|` 만 해석한다. 모르는 식은 ValueError — 조용히 빈 집합을 내지 않는다."""
    if isinstance(node, ast.Set):
        values = [element.value for element in node.elts if isinstance(element, ast.Constant)]
        if len(values) != len(node.elts) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"문자열 상수가 아닌 원소가 있다: {ast.dump(node)}")
        return frozenset(values)
    if isinstance(node, ast.Name):
        if node.id in seen or node.id not in names:
            raise ValueError(f"이름 {node.id!r} 를 이 파일에서 해석할 수 없다")
        return _eval_set(names[node.id], names, seen | {node.id})
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _eval_set(node.left, names, seen) | _eval_set(node.right, names, seen)
    raise ValueError(f"해석하지 않는 식: {ast.dump(node)}")


def _be_gram_units(source: str) -> frozenset[str]:
    """BE `_GRAM_UNITS | _MILLILITRE_UNITS` 의 값. 둘 중 하나라도 없으면 LookupError."""
    names = _assignments(source)
    for name in ("_GRAM_UNITS", "_MILLILITRE_UNITS"):
        if name not in names:
            raise LookupError(f"상수 {name!r} 가 없다")
    union = ast.BinOp(left=ast.Name(id="_GRAM_UNITS"), op=ast.BitOr(), right=ast.Name(id="_MILLILITRE_UNITS"))
    return _eval_set(union, names)


def test_r33_gram_units_match_be_to_grams_units():
    """행 33 — `GRAM_UNITS` 가 BE `_GRAM_UNITS | _MILLILITRE_UNITS` 와 같다 (AST 판독, import 없음).

    BE `to_grams` 가 단위를 늘리거나 줄였는데 사본을 안 맞추면, 채점에 반영되는 답을 실패로(또는 반대로) 센다.
    BE 파일이 없으면 FileNotFoundError 로 실패한다 — skip 하지 않는다.
    """
    be = _be_gram_units(_read(_BE_MEAL))
    assert be, "BE 단위 집합이 비었다 — 판독기를 의심한다"
    assert GRAM_UNITS == be
