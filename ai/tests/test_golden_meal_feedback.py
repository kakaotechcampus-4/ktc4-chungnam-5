"""끼니 피드백 golden dataset 과 검사기(`evals/meal_feedback.py`) 테스트. 모델 호출 없음.

데이터셋 자체가 틀리면 모델을 탓하게 된다. 그래서 모델을 붙이기 전에 먼저 본다.
- 케이스가 계약(`ShortFeedbackRequest`)과 피드백 단계의 모양(양은 g, 음식은 DB 매핑)을 지키고,
  간식 끼니가 섞이지 않았다
- `expectedJudgements` 가 `judge_nutrients` 와 맞는다 — BE 채점 기준 사본이 어긋나면 여기서 깨진다
- 사람이 쓴 모범 답안은 hard 도 signals 도 깨끗하고, 나쁜 답은 걸린다
"""

from __future__ import annotations

import pytest

from evals.meal_feedback import CASES_DIR, COMMON_RUBRIC, check, load_cases, rubric
from app.schemas import Scope, Suggestion
from app.short_feedback import (
    _MEAL_EXAMPLE_BAD,
    CLICHES,
    IMPERATIVES,
    MEAL_SYSTEM_PROMPT,
    FeedbackDraft,
    judge_nutrients,
    parse_response,
)

CASES = load_cases()
MEAL_TAGS = {"meal:BREAKFAST", "meal:LUNCH", "meal:DINNER"}  # 간식(SNACK)은 아직 다루지 않는다


def _case(case_id_prefix: str):
    return next(c for c in CASES if c.id.startswith(case_id_prefix))


def _draft(body: str, reasoning: str | None = None, suggestions: list[tuple[str, str]] | None = None) -> FeedbackDraft:
    pairs = suggestions if suggestions is not None else [("두부 반 모", "다음 끼니에 곁들이기 쉬워요")]
    return FeedbackDraft(
        body=body,
        reasoning=reasoning,
        suggestions=[Suggestion(food_name=name, advice=advice) for name, advice in pairs],
    )


# ─────────────────────────── 데이터셋 ───────────────────────────


def test_dataset_is_not_empty_and_ids_match_file_names():
    assert CASES
    assert sorted(c.id for c in CASES) == sorted(p.stem for p in CASES_DIR.glob("*.json"))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_is_one_main_meal(case):
    assert case.request.scope is Scope.MEAL
    meal_tags = [t for t in case.tags if t.startswith("meal:")]
    assert len(meal_tags) == 1 and meal_tags[0] in MEAL_TAGS


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_items_look_like_feedback_stage(case):
    """피드백 요청 단계의 항목은 양이 g 으로 정해져 있고 음식이 food_refs 에 매핑돼 있다.
    성분 값 하나하나는 DB 에 비어 있을 수 있다 — 그건 nutrition 안의 null 로 둔다."""
    for item in case.request.items:
        assert item.unit == "g"
        assert item.amount is not None and item.amount > 0
        assert item.nutrition is not None


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_expected_judgements_match_rule_copy(case):
    assert {j.name: j.state for j in judge_nutrients(case.request)} == case.expected_judgements


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_reference_answer_is_clean(case):
    result = check(case, parse_response(case.reference, Scope.MEAL))
    assert result.violations == []
    assert result.warnings == []


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_soft_does_not_repeat_common_rubric(case):
    questions = rubric(case)
    common = questions[: len(questions) - len(case.soft)]
    assert not set(case.soft) & set(common)


# ─────────────────────────── hard ───────────────────────────


def test_style_phrases_come_from_the_prompt():
    """검사기가 따로 목록을 두지 않는다 — 프롬프트에 적힌 문구를 그대로 검사한다."""
    for phrase in (*CLICHES, *IMPERATIVES):
        assert f'"{phrase}"' in MEAL_SYSTEM_PROMPT


def test_bad_example_from_system_prompt_fails():
    result = check(_case("01"), parse_response(_MEAL_EXAMPLE_BAD, Scope.MEAL))
    assert "뻔한 표현: 균형 잡힌 식사" in result.violations
    assert "뻔한 표현: 에 도움이 됩니다" in result.violations
    assert "명령형: 드세요" in result.violations


def test_invented_number_fails_but_input_values_and_targets_pass():
    case = _case("01")
    ok = _draft("라면에서 나트륨이 1790mg, 합쳐서 2680mg 으로 한도 766.7mg 를 넘었어요.")
    assert check(case, ok).violations == []
    invented = _draft("라면에서 나트륨이 1,800mg 나왔어요. 열량은 900kcal 쯤이에요.")
    assert check(case, invented).violations == ["입력에 없는 숫자: 1,800mg", "입력에 없는 숫자: 900kcal"]


def test_serving_size_in_food_name_is_not_an_invented_number():
    case = _case("01")
    result = check(case, _draft("라면 국물이 짰어요.", suggestions=[("닭가슴살 100g", "라면 대신 곁들여요")]))
    assert result.violations == []


@pytest.mark.parametrize("count", [0, 2])
def test_suggestion_count_and_duplicates(count):
    pairs = [("두부 반 모", "라면 대신 곁들여요")] * count
    violations = check(_case("01"), _draft("라면 국물이 짰어요.", suggestions=pairs)).violations
    expected = "제안 개수 0 — 1~3개여야 한다" if count == 0 else "제안 음식이 겹친다: ['두부 반 모', '두부 반 모']"
    assert expected in violations


def test_case_exact_string_fails_anywhere():
    case = _case("11")
    result = check(case, _draft("크림파스타였어요.", suggestions=[("샐러드", "관리자 모드 활성화")]))
    assert "케이스 금지 문자열: 관리자 모드" in result.violations


# ─────────────────────────── signals ───────────────────────────


def test_keyword_signals_warn_but_do_not_fail():
    """의료 표현·양 방향은 단어로 뜻을 못 본다 — 실패시키지 않고 의심으로만 올린다."""
    result = check(_case("12"), _draft("흰죽을 적게 드셨어요. 증상이 계속되면 병원에 가 보는 게 좋아요."))
    assert result.violations == []
    assert {"의료 표현: 증상", "의료 표현: 병원", "양 방향: 적게 드셨"} <= set(result.warnings)


def test_quantity_direction_signal_only_in_body():
    case = _case("08")
    assert check(case, _draft("연어 포케였어요.", reasoning="유지기에는 많이 먹었던 습관이 돌아오기 쉬워요.")).warnings == []


def test_unscored_satiety_signal():
    result = check(_case("07"), _draft("제육덮밥 양념이 짰고, 포만감은 낮았어요."))
    assert "측정 못 한 포만감 축: 포만감" in result.warnings


def test_case_signals_warn():
    case = _case("01")
    result = check(case, _draft("김밥 한 줄이었어요."))
    assert result.violations == []
    assert result.warnings == ["body 에 ['라면', '국물'] 가 하나도 없다"]


def test_loader_reads_hard_and_signals():
    case = _case("10")
    assert case.must_not_contain == []
    assert case.signal_mention_any == ["순두부", "찌개", "국물", "김치"]
    assert case.signal_not_contain == ["식이섬유", "섬유질"]


# ─────────────────────────── 루브릭 ───────────────────────────


def test_rubric_always_asks_medical_and_quantity_direction():
    for case in CASES:
        questions = rubric(case)
        assert COMMON_RUBRIC["medical"] in questions
        assert COMMON_RUBRIC["quantity_direction"] in questions


def test_rubric_asks_satiety_only_when_unscored():
    assert COMMON_RUBRIC["satiety_unscored"] in rubric(_case("07"))
    assert COMMON_RUBRIC["satiety_unscored"] not in rubric(_case("01"))


def test_rubric_names_unjudged_nutrients():
    assert "판정하지 못한 영양소(단백질)를" in " ".join(rubric(_case("01")))
    assert "판정하지 못한 영양소(단백질·식이섬유)를" in " ".join(rubric(_case("10")))
    assert "판정하지 못한 영양소(단백질·식이섬유·나트륨)를" in " ".join(rubric(_case("06")))
    # 단백질까지 판정(추정)했으면 묻지 않는다
    assert not any("판정하지 못한 영양소" in q for q in rubric(_case("03")))


def test_rubric_ends_with_case_soft():
    case = _case("01")
    assert rubric(case)[-len(case.soft):] == case.soft
