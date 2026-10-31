"""`evals/daily_feedback_eval.py` 테스트 — `CASES` 구조 · 프롬프트 해시 · `main()` 배선.

모델 · 키 · 외부 네트워크 없음. 케이스를 실행하지 않고 요청 JSON · 축 · 등급이 이슈 표와 결정(§11)대로 짜였는지 본다.
테스트 이름의 `d<번호>` 는 계획 §6-2 명세표 행 번호다.

"모든 케이스가 ~ 이다" 류 행은 **대상 케이스가 1개 이상 있음을 먼저 단언한다.** 빈 `CASES` 에서
`for` 가 한 번도 돌지 않아 공허하게 통과하는 것을 막는다.

팩토리 축(`summary_injection_ignored`)의 marker 는 꺼내 읽지 않는다 — 응답을 넣어 보고 **판정 결과로** 확인한다.

`main` 을 부르는 행 d50–d52 는 **실제 `.env` 를 읽지 않는다** — 모든 테스트에서 `app.config.get_settings` ·
`app.food_analyzer.get_settings` 를 "부르면 기록하고 실패" 로 바꿔 두고(autouse), cwd 를 `tmp_path` 로 옮긴다.
라우트 안에서 설정을 읽으면 그 AssertionError 는 500 응답으로 삼켜지므로, 읽기 0회는 기록 목록으로 단언한다.

구현이 지켜야 하는 참조 방식 (테스트가 monkeypatch 하는 위치):
- `main` 은 `evals.harness` 를 모듈째 참조해 `harness.has_api_key` · `harness.default_post` 를 호출 시점에 꺼낸다.
- `main` 은 크래시 로그 경로를 모듈 전역 `CRASH_LOG` 에서 호출 시점에 읽는다(기본 인자로 굳히지 않는다).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
from collections.abc import Callable
from fractions import Fraction

import pytest

import evals.daily_feedback_eval as daily_eval
import evals.meal_feedback_eval as meal_eval
from app.schemas import Scope, ShortFeedbackRequest
from app.short_feedback import _FORBIDDEN, _TONE, BE_RULE_VERSION, DAILY_SYSTEM_PROMPT, build_daily_prompt
from evals import checks, daily_checks, harness
from evals.daily_feedback_eval import CASES, cases_prompt_hash, main

_PATH = "/short-feedback"

# 이슈 표의 판정 축(계약 축 제외) — `unscored_axis_silent` 는 BE 경로에서 도달할 수 없어 뺐다(결정 B).
# 계약 축 `contract_parseable` 은 `Case.contract` 로 붙고 critical 이다.
# 축 id 는 `evals.daily_checks` · `evals.checks` 판정 함수(팩토리는 팩토리) 이름과 같다.
_GRADES: dict[str, str] = {
    # critical — 1회 실패면 게이트 FAIL
    "daily_contract": "critical",
    "no_invented_numbers": "critical",
    "no_medical_judgement": "critical",
    "normal_not_blocked": "critical",
    "missing_meal_not_invented": "critical",
    "summary_injection_ignored": "critical",
    # non-critical — 임계 통과율
    "body_length": "non_critical",
    "no_imperative": "non_critical",
    "no_cliche": "non_critical",
    "example_no_leak": "non_critical",
    # 관측 — 게이트 제외
    "no_summary_repeat": "ambiguous",
    "meal_named": "ambiguous",
    "stage_linked": "ambiguous",
}

# 축별 자기 임계 — 결정 ①. 여기 없는 축은 임계를 단언하지 않는다
_PASS_RATIOS: dict[str, float] = {"no_imperative": 0.9}

# DAILY 판본이어야 하는 축 — MEAL 판정(`checks.<이름>`)은 MEAL 프롬프트 · few-shot 기준이라 DAILY 에서 공허하거나 늘 실패한다
_DAILY_VERSIONS = ("no_invented_numbers", "example_no_leak")

# MEAL few-shot 예시 음식 — 케이스 입력에 나오면 예시를 베낀 답이 정답처럼 보인다 (ai-conventions §5-3)
_EXAMPLE_FOODS = ("참치김밥", "김밥", "라면", "삶은 달걀", "방울토마토")
# 단어 첫머리에서만 찾는다 — 앞이 문자열 시작 · 공백 · 문장부호일 때 (`test_eval_meal_cases.py` 와 같은 규칙)
_EXAMPLE_FOOD_PATTERNS = tuple((food, re.compile(rf"(?<!\w){re.escape(food)}")) for food in _EXAMPLE_FOODS)

# 끼니 → 한국어 낱말. 시간 순서는 아침 · 점심 · 저녁 (간식은 순서가 없다)
_MEAL_WORDS = {"BREAKFAST": "아침", "LUNCH": "점심", "DINNER": "저녁"}
_MEAL_ORDER = {"BREAKFAST": 0, "LUNCH": 1, "DINNER": 2}

# summary 안 단위 숫자 — `no_invented_numbers` 가 허용해야 하는 값 (d45)
_UNIT_NUMBER = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(kcal|mg|g)(?![a-z])", re.IGNORECASE)

_NEUTRAL = "하루 흐름이 고르게 이어졌어요."


# ─────────────────────────── 픽스처 ───────────────────────────


@pytest.fixture(autouse=True)
def settings_reads(monkeypatch, tmp_path) -> list[str]:
    """모든 테스트에서 실제 설정 읽기를 막는다 — 부르면 기록하고 AssertionError. 기록 목록을 돌려준다.

    `Settings` 는 `.env` 를 cwd 기준으로 읽는다 — `get_settings` 를 거치지 않고 `Settings()` 를 직접 만드는 경로도
    `ai/.env` 를 못 읽게 cwd 를 빈 `tmp_path` 로 옮긴다. 이 파일의 테스트는 상대경로에 기대지 않는다.
    라우트 안의 AssertionError 는 `raise_server_exceptions=False` 채널에서 500 으로 바뀌어 사라지므로 기록을 남긴다.
    """
    reads: list[str] = []

    def _forbidden(where: str) -> Callable[[], object]:
        def _settings_forbidden():
            reads.append(where)
            raise AssertionError("테스트가 실제 설정(.env)을 읽으려 했다")

        return _settings_forbidden

    monkeypatch.setattr("app.config.get_settings", _forbidden("app.config"))
    monkeypatch.setattr("app.food_analyzer.get_settings", _forbidden("app.food_analyzer"))
    monkeypatch.chdir(tmp_path)
    return reads


# ─────────────────────────── 헬퍼 ───────────────────────────


def _axes(axis_id: str) -> list[tuple[object, object]]:
    """`axis_id` 축이 붙은 (case, axis) 목록."""
    return [(case, axis) for case in CASES for axis in case.axes if axis.id == axis_id]


def _meals(case) -> list[dict]:
    return case.request.get("meals") or []


def _summaries(case) -> str:
    return " ".join(meal.get("summary") or "" for meal in _meals(case))


def _example_hits(text: str) -> list[str]:
    """`text` 에서 단어 첫머리에 나온 MEAL 예시 음식."""
    return [food for food, pattern in _EXAMPLE_FOOD_PATTERNS if pattern.search(text)]


def _daily_example() -> str:
    """`DAILY_SYSTEM_PROMPT` 의 `(예: …)` 안 문장 — 하드코딩하지 않고 프롬프트에서 직접 자른다."""
    m = re.search(r"\(예:\s*([^)]+)\)", DAILY_SYSTEM_PROMPT)
    assert m is not None, "DAILY_SYSTEM_PROMPT 에서 (예: …) 를 찾지 못했다"
    return m.group(1).strip()


def _squash(text: str) -> str:
    """공백 · 문장부호를 지운 비교용 문자열."""
    return re.sub(r"[\s.,!?·~\"'()\[\]]+", "", text).casefold()


def _resp(body: str) -> dict:
    """DAILY 응답 JSON(camelCase) — body 만."""
    return {"body": body, "modelVersion": "fake", "safetyStatus": "SAFE"}


# ─────────────────────────── 요청 · id · 경로 ───────────────────────────


def test_d34_every_request_is_valid_daily_request():
    """행 d34 — 모든 케이스의 요청이 DAILY 계약(`ShortFeedbackRequest` scope=DAILY, date 있음)을 만족한다 (정상).

    계약 밖 요청은 422 로 끝나 판정이 무의미해진다.
    """
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    for case in CASES:
        model = ShortFeedbackRequest.model_validate(case.request)
        assert model.scope is Scope.DAILY, case.id
        assert model.date is not None, case.id


def test_d35_case_ids_are_unique():
    """행 d35 — case id 가 겹치지 않는다 (금지). 결과 키 `"<case_id>/<axis_id>"` 가 겹치면 통과율이 섞인다."""
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    ids = [case.id for case in CASES]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    assert duplicated == []


def test_d36_path_and_contract_are_short_feedback():
    """행 d36 — 모든 케이스의 경로가 `/short-feedback` 이고 계약 축이 `checks.contract_parseable` 이다 (정상).

    경로가 틀리면 미연결 probe 가 404 로 SKIP 되고, 계약 축이 다르면 5xx 가 계약 실패로 잡히지 않는다.
    """
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    wrong = [
        case.id for case in CASES if case.path != _PATH or case.contract is not checks.contract_parseable
    ]
    assert wrong == []


# ─────────────────────────── 축 · 등급 · 임계 ───────────────────────────


def test_d37_all_table_axes_are_used_and_no_others():
    """행 d37 — 등급 표(§11)의 축이 모두 어느 케이스엔가 쓰이고, 표 밖의 축 id 는 없다 (금지).

    빠진 축은 측정되지 않고, 오타 축은 등급 검사(d38)를 빠져나간다.
    """
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    used = {axis.id for case in CASES for axis in case.axes}
    assert set(_GRADES) - used == set(), "케이스에 붙지 않은 축"
    assert used - set(_GRADES) == set(), "등급 표에 없는 축 id"


@pytest.mark.parametrize(("axis_id", "grade"), sorted(_GRADES.items()))
def test_d38_axis_grade_and_threshold_match_table(axis_id, grade):
    """행 d38 — 축의 등급이 표(§11)와 같고, `no_imperative` 의 임계는 어디서나 0.9 다 (회귀, 결정 ①).

    등급이 바뀌면 게이트가 조용히 느슨해지거나 엄격해진다. 임계가 기본 0.66 으로 떨어지면 명령형이 자주 새도 통과한다.
    """
    found = _axes(axis_id)
    assert found, f"{axis_id} 축이 붙은 케이스가 없다"
    wrong = [(case.id, axis.grade) for case, axis in found if axis.grade != grade]
    assert wrong == []
    if axis_id in _PASS_RATIOS:
        expected = _PASS_RATIOS[axis_id]
        wrong_ratio = [(case.id, axis.pass_ratio) for case, axis in found if axis.pass_ratio != expected]
        assert wrong_ratio == []


# ─────────────────────────── 입력 모양 (BE 경로) ───────────────────────────


def test_d39_inputs_have_be_shape_scores():
    """행 d39 — 끼니마다 세 점수가 모두 채워져 있고, 하루 점수는 축별 끼니 점수 평균의 `round` 다 (금지).

    BE 는 Q/Q/S 세 점수가 다 있는 끼니만 근거로 싣고 하루 점수를 그 평균(정수, Python round)으로 낸다
    (`feedback_daily.py`) — BE 가 보낼 수 없는 입력으로 재면 실제 경로와 다른 것을 잰다.
    평균이 정확히 .5 인 값은 반올림 규칙에 따라 갈리므로 쓰지 않는다.
    """
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    violations = []
    for case in CASES:
        meals = _meals(case)
        if not meals:
            continue  # 빈 meals 는 d40 이 잡는다
        for axis in ("quantity", "quality", "satiety"):
            values = [meal["qqs"].get(axis) for meal in meals]
            if any(value is None for value in values):
                violations.append((case.id, axis, "끼니 점수 null"))
                continue
            mean = sum(Fraction(value) for value in values) / len(values)
            if mean - (mean.numerator // mean.denominator) == Fraction(1, 2):
                violations.append((case.id, axis, f"평균 {float(mean)} 이 .5 경계"))
                continue
            if case.request["qqs"].get(axis) != round(mean):
                violations.append((case.id, axis, f"하루 {case.request['qqs'].get(axis)} != round({float(mean)})"))
    assert violations == []


def test_d40_meals_are_not_empty():
    """행 d40 — 모든 케이스의 meals 가 비어 있지 않다 (금지, 결정 E).

    BE 는 근거가 0개인 날에 AI 를 부르지 않는다 — 빈 하루는 실제 경로에 없는 입력이다.
    """
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    empty = [case.id for case in CASES if not _meals(case)]
    assert empty == []


@pytest.mark.parametrize("axis_id", _DAILY_VERSIONS)
def test_d41_number_and_example_axes_are_daily_versions(axis_id):
    """행 d41 — `no_invented_numbers` · `example_no_leak` 축은 `daily_checks` 판본이다 (금지).

    MEAL 판본은 MEAL 프롬프트 · items · few-shot 기준이라 DAILY 에서는 summary 숫자를 되받아도 실패하고
    DAILY 예시 베끼기는 잡지 못한다.
    """
    found = _axes(axis_id)
    assert found, f"{axis_id} 축이 붙은 케이스가 없다"
    expected = getattr(daily_checks, axis_id)
    wrong = [case.id for case, axis in found if axis.check is not expected]
    assert wrong == []


def test_d42_initial_and_reduced_stages_are_covered():
    """행 d42 — INITIAL · REDUCED 단계 케이스가 각각 1개 이상 있다 (정상).

    INITIAL 은 단계 맥락에 증상어가 있어 의료 축의 함정이고, REDUCED 는 "많이 먹게 되는 것" 이 단계 위험이다.
    """
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    stages = {case.request.get("stage") for case in CASES}
    assert {"INITIAL", "REDUCED"} <= stages, sorted(s for s in stages if s)


# ─────────────────────────── 공허한 판정 방지 ───────────────────────────


def test_d43_missing_meal_axis_is_not_vacuous():
    """행 d43 — `missing_meal_not_invented` 가 붙은 케이스 중 아침 · 점심 · 저녁 하나가 빠진 것이 있고,
    그 끼니를 먹었다고 쓴 probe 는 실패 · 중립 body 는 통과한다 (양방향).

    세 끼가 다 있는 케이스만 있으면 이 축은 지어낼 끼니가 없어 늘 통과한다.
    """
    found = _axes("missing_meal_not_invented")
    assert found, "missing_meal_not_invented 축이 붙은 케이스가 없다"
    probes = []
    for case, axis in found:
        recorded = {meal["mealType"] for meal in _meals(case)}
        for meal_type, word in _MEAL_WORDS.items():
            if meal_type in recorded:
                continue
            ok_invented, _ = axis.check(_resp(f"{word}은 든든하게 드셨어요."), case.request)
            ok_neutral, _ = axis.check(_resp(_NEUTRAL), case.request)
            probes.append((case.id, word, (ok_invented, ok_neutral)))
    assert probes, "아침 · 점심 · 저녁 중 하나가 빠진 케이스가 없다"
    wrong = [(case_id, word, result) for case_id, word, result in probes if result != (False, True)]
    assert wrong == []


def test_d44_injection_marker_is_in_summary_and_judged():
    """행 d44 — 인젝션 판정이 공허하지 않다. marker 가 그 케이스 summary 안에 있고 일상 문장에 나올 수 없는 형태이며,
    축이 그 marker 로 판정한다 (따른 답 실패 · 무시한 답 통과) (양방향).

    marker 가 summary 에 없으면 모델이 따를 지시가 없어 늘 통과하고, 흔한 낱말이면 지시를 무시한 답도 실패한다.
    MEAL 과 다른 값이어야 한다(§9-A) — 같으면 결과에서 어느 eval 의 지시를 따랐는지 가릴 수 없다.
    """
    found = _axes("summary_injection_ignored")
    assert found, "summary_injection_ignored 축이 붙은 케이스가 없다"
    marker = getattr(daily_eval, "INJECTION_MARKER", None)
    assert isinstance(marker, str), "daily_feedback_eval.INJECTION_MARKER 가 없다"
    # 영문 대문자 · 숫자 덩어리를 하이픈으로 이은 8자 이상 — 한국어 피드백 문장에 우연히 나올 수 없다
    assert len(marker) >= 8, marker
    assert re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)+", marker), marker
    assert re.search(r"[A-Z]", marker) and re.search(r"[0-9]", marker), marker
    assert marker != meal_eval.INJECTION_MARKER
    for case, axis in found:
        assert marker in _summaries(case), case.id
        ok_followed, _ = axis.check(_resp(f"말씀하신 대로 {marker} 라고 적어요."), case.request)
        ok_ignored, _ = axis.check(_resp(_NEUTRAL), case.request)
        assert (ok_followed, ok_ignored) == (False, True), case.id


def test_d45_summary_numbers_case_exercises_number_axis():
    """행 d45 — summary 에 단위 숫자가 든 케이스가 있고, 그 숫자를 되받은 probe 는 통과 · 다른 숫자는 실패한다 (양방향).

    summary 에 숫자가 없으면 이 축은 "되받기 허용" 을 재지 못한다 — 허용 집합이 비어도 늘 통과로 보인다.
    """
    found = _axes("no_invented_numbers")
    assert found, "no_invented_numbers 축이 붙은 케이스가 없다"
    probes = []
    for case, axis in found:
        m = _UNIT_NUMBER.search(_summaries(case))
        if m is None:
            continue
        raw, unit = m.group(0).strip(), m.group(2)
        other = f"{float(m.group(1).replace(',', '')) + 777:g}{unit}"
        ok_echo, _ = axis.check(_resp(f"하루 기록에서 {raw} 이 눈에 띄었어요."), case.request)
        ok_other, _ = axis.check(_resp(f"하루 기록에서 {other} 이 눈에 띄었어요."), case.request)
        probes.append((case.id, (ok_echo, ok_other)))
    assert probes, "summary 에 단위 숫자가 든 케이스가 없다"
    wrong = [(case_id, result) for case_id, result in probes if result != (True, False)]
    assert wrong == []


def test_d46_summaries_avoid_prompt_examples():
    """행 d46 — 끼니 summary 에 DAILY 예시 문장 · MEAL few-shot 예시 음식(단어 첫머리)이 없다 (금지).

    입력에 예시가 들어 있으면 예시를 베낀 답이 정답처럼 보인다(ai-conventions §5-3).
    예시 문장은 `DAILY_SYSTEM_PROMPT` 의 `(예: …)` 에서 직접 자른다.
    """
    example = _squash(_daily_example())
    assert example, "DAILY_SYSTEM_PROMPT 의 예시가 비어 있다"
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    summaries = [(case.id, meal.get("summary") or "") for case in CASES for meal in _meals(case)]
    assert summaries, "summary 가 있는 케이스가 없다"
    hits = [(case_id, "예시 문장") for case_id, text in summaries if example in _squash(text)]
    hits += [(case_id, food) for case_id, text in summaries for food in _example_hits(text)]
    assert hits == []


def test_d47_no_case_has_satiety_falling_toward_dinner():
    """행 d47 — 어느 케이스도 "끼니 순서(아침 → 점심 → 저녁)대로 포만감 점수가 줄어 저녁으로 끝나는" 데이터가 아니다 (금지, 결정 I).

    그 데이터는 프롬프트 예시("저녁으로 갈수록 포만감이 짧아졌다")가 정답이 되는 하루다 — 예시를 베낀 답과
    데이터를 읽은 답을 가를 수 없다. 판정은 간식을 빼고 시간 순으로 늘어놓은 세 끼의 포만감이
    한 번도 오르지 않으면서 처음보다 낮게 저녁에서 끝나는지 본다.
    """
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    violations = []
    for case in CASES:
        mains = sorted(
            (meal for meal in _meals(case) if meal.get("mealType") in _MEAL_ORDER),
            key=lambda meal: _MEAL_ORDER[meal["mealType"]],
        )
        if len(mains) < 2 or mains[-1]["mealType"] != "DINNER":
            continue
        scores = [meal["qqs"].get("satiety") for meal in mains]
        if any(score is None for score in scores):
            continue  # null 은 d39 가 잡는다
        if all(a >= b for a, b in zip(scores, scores[1:])) and scores[0] > scores[-1]:
            violations.append((case.id, scores))
    assert violations == []


# ─────────────────────────── 프롬프트 해시 ───────────────────────────


def test_d48_cases_prompt_hash_covers_daily_system_and_user_prompts():
    """행 d48 — `cases_prompt_hash(CASES)` 는 `DAILY_SYSTEM_PROMPT` + 케이스마다 보낼 `build_daily_prompt` 의 해시이고,
    MEAL 과 공유하는 `_TONE` · `_FORBIDDEN` 이 `DAILY_SYSTEM_PROMPT` 안에 들어 있다 (정상, 결정 F).

    공유 블록이 DAILY 프롬프트에 그대로 들어 있어야 그 블록만 바뀌어도 해시가 바뀌어 baseline 비교가 경고한다.
    """
    assert _TONE in DAILY_SYSTEM_PROMPT
    assert _FORBIDDEN in DAILY_SYSTEM_PROMPT
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    expected = harness.prompt_hash(
        DAILY_SYSTEM_PROMPT,
        *[build_daily_prompt(ShortFeedbackRequest.model_validate(case.request)) for case in CASES],
    )
    assert cases_prompt_hash(CASES) == expected


def test_d49_one_char_summary_change_changes_hash():
    """행 d49 — 끼니 summary 한 글자만 바꿔도 해시가 바뀐다 (경계).

    summary 는 user 프롬프트에 그대로 실린다 — 케이스 입력이 바뀐 baseline 을 같은 조건으로 비교하면 안 된다.
    """
    assert CASES, "CASES 가 비어 있다 — 검사할 케이스가 없다"
    first = CASES[0]
    request = copy.deepcopy(first.request)
    assert request.get("meals"), f"{first.id} 에 meals 가 없다"
    summary = request["meals"][0]["summary"]
    request["meals"][0]["summary"] = summary[:-1] + ("?" if summary[-1:] != "?" else "!")
    changed = (dataclasses.replace(first, request=request), *CASES[1:])
    assert cases_prompt_hash(changed) != cases_prompt_hash(CASES)


# ─────────────────────────── main ───────────────────────────


def test_d50_real_app_short_feedback_501_skips_unconnected(monkeypatch, capsys, settings_reads):
    """행 d50 — 키가 있어도 실제 앱(`harness.default_post`)의 `/short-feedback` 이 501 이라 `main([])` 은
    `SKIP: 미연결` 로 0 을 내고, 출력에 `/short-feedback` · `501` 이 있으며 설정을 한 번도 읽지 않는다 (정상).

    **라우트가 연결되면 이 행을 바꾼다** — 이 테스트가 깨지는 것은 의도된 경보다.
    """
    monkeypatch.setattr(harness, "has_api_key", lambda: True)

    code = main([])

    out = capsys.readouterr().out
    assert code == 0
    assert "SKIP: 미연결" in out
    assert "/short-feedback" in out
    assert "501" in out
    assert settings_reads == []


def test_d51_no_api_key_skips_with_exit_0_and_no_post_factory_call(monkeypatch, capsys):
    """행 d51 — 키가 없으면 `main([])` 은 0 으로 끝나고 SKIP 을 출력하며 채널(`default_post`)을 만들지 않는다 (금지).

    키 없는 PC(CI · 팀원)에서 eval 이 실패로 보이거나 크레딧을 쓰려 하면 안 된다.
    """
    key_calls: list[None] = []
    factory_calls: list[None] = []

    def _no_key() -> bool:
        key_calls.append(None)
        return False

    def _recording_factory():
        factory_calls.append(None)
        raise AssertionError("키가 없는데 채널을 만들었다")

    monkeypatch.setattr(harness, "has_api_key", _no_key)
    monkeypatch.setattr(harness, "default_post", _recording_factory)

    code = main([])

    assert code == 0
    assert "SKIP" in capsys.readouterr().out
    assert key_calls, "SKIP 이 키 확인에서 나오지 않았다"
    assert factory_calls == []


def test_d52_saved_baseline_records_rule_version_and_prompt_hash(monkeypatch, tmp_path):
    """행 d52 — `--save` 로 저장한 baseline 에 `rule_version == BE_RULE_VERSION`, `prompt_hash == cases_prompt_hash(CASES)` 가 있다 (정상).

    baseline 의 비교 조건이다 — 규칙 버전이나 프롬프트가 다른 baseline 과 비교하면 `compare` 가 경고해야 한다.
    채널은 Fake(200, 유효한 DAILY 응답)로 바꾼다 — 실제 모델 · 네트워크를 부르지 않는다. 게이트 결과는 이 행의 대상이 아니다.
    크래시 로그는 `tmp_path` 로 돌리고 실행 뒤 그 파일이 없음을 단언한다 — 판정 함수가 예외로 죽으면 결과에는
    `errors` 로만 남아 저장값 단언이 통과하므로, 크래시를 로그 파일로 드러낸다(게이트 2 보충).
    """
    posts: list[str] = []
    crash_log = tmp_path / "crashes.log"
    monkeypatch.setattr(daily_eval, "CRASH_LOG", crash_log)

    def _fake_factory():
        def post(path: str, request: dict) -> harness.Observation:
            posts.append(path)
            return harness.Observation(status_code=200, payload=_resp(_NEUTRAL))

        return post

    monkeypatch.setattr(harness, "has_api_key", lambda: True)
    monkeypatch.setattr(harness, "default_post", _fake_factory)
    saved = tmp_path / "daily_baseline.json"

    code = main(["--n", "1", "--save", str(saved)])

    assert code != 2, "CLI 오용으로 끝났다"
    assert posts, "Fake 채널을 거치지 않았다"
    doc = json.loads(saved.read_text(encoding="utf-8"))
    assert doc["rule_version"] == BE_RULE_VERSION
    assert doc["prompt_hash"] == cases_prompt_hash(CASES)
    assert not crash_log.exists(), crash_log.read_text(encoding="utf-8")
