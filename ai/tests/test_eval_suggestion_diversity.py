"""추천 음식 다양성 지표 — 이름이 아니라 주재료로 묶어 쏠림을 잰다 (관측 지표, 게이트 밖)."""
import pytest

from evals.suggestion_diversity import diversity, main_foods


@pytest.mark.parametrize(
    "name, expected",
    [
        ("두부 반 모", {"두부"}),
        ("두부구이 반 모", {"두부"}),
        ("간하지 않은 두부 반 모", {"두부"}),
        ("순두부 반 봉지", {"두부"}),
        ("삶은 달걀 2개", {"달걀"}),
        ("달걀찜 한 그릇", {"달걀"}),
        ("계란말이 한 접시", {"달걀"}),
        ("닭가슴살 샐러드 한 접시", {"닭고기", "채소"}),
        ("오이·방울토마토 한 컵", {"오이", "토마토"}),
        ("콩나물무침 한 접시", {"나물"}),
        ("삶은 병아리콩 한 컵", {"콩"}),
        ("무가당 플레인 요거트 1개", {"유제품"}),
        ("고등어구이 한 토막", {"생선"}),
    ],
)
def test_names_group_by_main_food(name, expected):
    """조리법 · 1회분 표현이 달라도 같은 주재료면 같은 음식이다 — "두부 반 모" 와 "두부구이 반 모" 는 둘 다 두부."""
    assert main_foods(name) == expected


def test_unknown_food_falls_back_to_its_name_without_serving():
    """표에 없는 음식은 1회분 표현을 뗀 이름으로 센다 — 버리지 않는다."""
    assert main_foods("메밀소바 한 그릇") == {"메밀소바"}


def _resp(*names: str) -> dict:
    return {"suggestions": [{"foodName": n, "advice": "-"} for n in names]}


def test_diversity_counts_responses_containing_each_main_food():
    """한 응답에 두부가 두 번 나와도 그 응답은 한 번 센다. 비율의 분모는 추천이 있는 응답 수다."""
    result = diversity(
        [
            _resp("두부 반 모", "두부구이 반 모", "삶은 달걀 2개"),
            _resp("두부조림 한 접시", "고등어구이 한 토막"),
            _resp("데친 브로콜리 한 접시"),
            {"suggestions": []},  # 저녁 — 추천 없음
            {"suggestions": None},  # 차단된 응답
        ]
    )
    assert result["responses"] == 3
    assert result["distinct"] == 4
    assert result["top"][0] == ("두부", 2, pytest.approx(2 / 3))
