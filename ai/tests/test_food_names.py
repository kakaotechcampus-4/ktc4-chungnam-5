"""DB 검색용 동의 표기는 허용하되 음식의 정체성과 수식어를 보존한다."""

import unicodedata

import pytest

from app.food_names import normalize_food_name


@pytest.mark.parametrize(
    ("variants", "canonical"),
    [
        (["망고주스", "망고 주스", "망고쥬스", "망고 쥬스"], "망고주스"),
        (["리치주스", "리치 주스", "리치쥬스", "리치 쥬스"], "리치주스"),
        (["토마토주스", "토마토 주스", "토마토쥬스", "토마토 쥬스"], "토마토주스"),
        (["양송이스프", "양송이 스프", "양송이수프", "양송이 수프"], "양송이 스프"),
        (["크림스프", "크림 스프", "크림수프", "크림 수프"], "크림 스프"),
        (["옥수수스프", "옥수수 스프", "옥수수수프", "옥수수 수프"], "옥수수 스프"),
        (["달걀프라이", "달걀 프라이", "달걀후라이", "달걀 후라이",
          "계란프라이", "계란 프라이", "계란후라이", "계란 후라이"], "달걀후라이"),
    ],
)
def test_equivalent_whole_names_converge_and_are_idempotent(variants, canonical):
    for name in variants:
        normalized = normalize_food_name(name)
        assert normalized == canonical
        assert normalize_food_name(normalized) == normalized


@pytest.mark.parametrize(
    "name",
    [
        "양송이스프분말", "양송이스프라면", "옥수수스프가루", "크림 수프 가루",
        "양송이 크림스프", "단호박 크림 수프", "모구모구 망고 주스",
        "망고 주스 젤리", "망고스프레드", "에스프레소", "프라이드계란모양구미",
        "햄&계란후라이", "계란후라이 2개", "복숭아 쥬스", "오렌지 주스", "포도쥬스",
        "사과 주스", "양념두부", "두부무침", "두부부침", "불고기비빔밥",
        " 망고  바나나 주스\t", "", "   ", "망 고 주스", "망고_주스",
        unicodedata.normalize("NFD", "새로운 음식"),
    ],
)
def test_unknown_or_qualified_names_are_preserved_exactly(name):
    assert normalize_food_name(name) == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("  계란\t 프라이\n", "달걀후라이"),
        ("망고\u00a0쥬스", "망고주스"),
        (unicodedata.normalize("NFD", "양송이  수프"), "양송이 스프"),
    ],
)
def test_lookup_handles_whitespace_and_decomposed_hangul(name, expected):
    assert normalize_food_name(name) == expected
