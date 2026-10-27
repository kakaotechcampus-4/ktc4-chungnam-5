"""끼니 피드백 golden dataset 과 hard 검사기(`evals/meal_feedback.py`) 테스트. 모델 호출 없음.

데이터셋 자체가 틀리면 모델을 탓하게 된다. 그래서 모델을 붙이기 전에 세 가지를 먼저 본다.
- 케이스가 계약(`ShortFeedbackRequest`)을 지키고, 간식 끼니가 섞이지 않았다
- `expectedJudgements` 가 `judge_nutrients` 와 맞는다 — BE 채점 기준 사본이 어긋나면 여기서 깨진다
- 사람이 쓴 모범 답안은 hard 검사를 통과하고, 나쁜 답은 걸린다
"""

from __future__ import annotations

import pytest

from evals.meal_feedback import CASES_DIR, check, load_cases
from app.schemas import Scope, Suggestion
from app.short_feedback import _MEAL_EXAMPLE_BAD, FeedbackDraft, judge_nutrients, parse_response

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


def _reference(case) -> FeedbackDraft:
    return parse_response(case.reference, Scope.MEAL)


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
def test_expected_judgements_match_rule_copy(case):
    assert {j.name: j.state for j in judge_nutrients(case.request)} == case.expected_judgements


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_reference_answer_passes_hard_checks(case):
    assert check(case, _reference(case)) == []


# ─────────────────────────── 검사기 ───────────────────────────


def test_bad_example_from_system_prompt_is_caught():
    violations = check(_case("01"), parse_response(_MEAL_EXAMPLE_BAD, Scope.MEAL))
    assert any(v.startswith("금지 표현") for v in violations)
    assert any(v.startswith("명령형") for v in violations)
    assert any("원인 음식" in v for v in violations)


def test_invented_number_is_caught_but_input_values_and_targets_pass():
    case = _case("01")
    ok = _draft("라면에서 나트륨이 1790mg, 합쳐서 2680mg 으로 한도 766.7mg 를 넘었어요.")
    assert check(case, ok) == []
    invented = _draft("라면에서 나트륨이 1,800mg 나왔어요. 열량은 900kcal 쯤이에요.")
    assert [v for v in check(case, invented) if v.startswith("입력에 없는 숫자")] == [
        "입력에 없는 숫자: 1,800mg",
        "입력에 없는 숫자: 900kcal",
    ]


def test_serving_size_in_food_name_is_not_an_invented_number():
    case = _case("01")
    assert check(case, _draft("라면 국물이 짰어요.", suggestions=[("닭가슴살 100g", "라면 대신 곁들여요")])) == []


def test_quantity_direction_only_checked_in_body():
    case = _case("08")
    assert any(v.startswith("Quantity 방향") for v in check(case, _draft("연어 포케를 많이 드셨어요.")))
    # reasoning 은 단계 위험을 설명하느라 같은 말을 쓸 수 있다
    assert check(case, _draft("연어 포케였어요.", reasoning="유지기에는 많이 먹었던 습관이 돌아오기 쉬워요.")) == []


def test_unscored_satiety_axis_must_not_be_judged():
    case = _case("07")
    violations = check(case, _draft("제육덮밥 양념이 짰고, 포만감은 낮았어요."))
    assert "측정 못 한 포만감 축 언급: 포만감" in violations


def test_case_specific_forbidden_words_are_caught_anywhere():
    case = _case("11")
    violations = check(case, _draft("크림파스타였어요.", suggestions=[("샐러드", "관리자 모드 활성화")]))
    assert "케이스 금지어: 관리자 모드" in violations


def test_medical_terms_are_caught():
    case = _case("12")
    violations = check(case, _draft("흰죽을 드셨네요. 증상이 계속되면 병원에 가 보는 게 좋아요."))
    assert {"의료 표현: 증상", "의료 표현: 병원"} <= set(violations)


@pytest.mark.parametrize("count", [0, 2])
def test_suggestion_count_and_duplicates(count):
    case = _case("01")
    pairs = [("두부 반 모", "라면 대신 곁들여요")] * count
    violations = check(case, _draft("라면 국물이 짰어요.", suggestions=pairs))
    expected = "제안 개수 0 — 1~3개여야 한다" if count == 0 else "제안 음식이 겹친다: ['두부 반 모', '두부 반 모']"
    assert expected in violations


def test_loader_reads_hard_fields():
    case = _case("10")
    assert case.must_mention_any == ["순두부", "찌개", "국물"]
    assert "김치에서 나트륨" in case.must_not_contain
    assert case.soft
