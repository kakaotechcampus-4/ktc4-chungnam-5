"""끼니 피드백 골든 데이터셋 검증 (verify 층). 모델 호출 없음 — 키 없이 CI 에서 돈다.

케이스가 틀리면 eval 전체가 틀린다. 그래서 모델을 부르기 전에 **데이터셋과 판정 함수**를 먼저 본다.

- 형식 — id · 필수 필드 · 허용값
- 기대값 — `expectedJudgements` 가 실제 규칙(`judge_nutrients`) 결과와 같은가
- 양방향 — 모범 답(`reference`)은 통과하고, 반례(`counterexamples`)는 적은 검사에서만 실패하는가
- 규칙 동기화 — AI 쪽 기준 사본이 BE `stage_profile.py` 와 같은가
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.schemas import Stage
from app.short_feedback import _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY, BE_RULE_VERSION
from evals import golden
from evals import meal_feedback as mf

COMMON = golden.load_common(mf.AREA)
PATHS = golden.case_paths(mf.AREA)
CASES = [json.loads(p.read_text(encoding="utf-8")) for p in PATHS]
IDS = [p.stem for p in PATHS]

COUNTEREXAMPLES = [(case, i) for case in CASES for i in range(len(case.get("counterexamples", [])))]
COUNTEREXAMPLE_IDS = [f"{case['id']}#{i}" for case, i in COUNTEREXAMPLES]

BACKEND = Path(__file__).resolve().parents[2] / "backend" / "app" / "services" / "evaluation"


def _failed(case, response) -> set[str]:
    """신호(관측)를 뺀 실패 검사 이름. 반례 · 모범 답은 등급과 상관없이 이것으로 본다."""
    return {
        r.check
        for r in mf.check_response(case, response, COMMON)
        if not r.passed and r.check not in mf.OBSERVE_CHECKS and r.check != "signal_must_not_contain"
    }


# ─────────────────────────── 형식 ───────────────────────────


def test_dataset_is_not_empty():
    assert CASES


@pytest.mark.parametrize("path", PATHS, ids=IDS)
def test_case_format(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    assert golden.validate_case(case, path, mf.AREA) == []
    assert "expectedJudgements" in case


def test_common_checks_match_code():
    """`_common.json` 에 적은 검사와 코드의 검사가 같아야 문서가 거짓말을 안 한다."""
    assert set(COMMON["checks"]) == set(mf.COMMON_CHECKS)
    assert {spec["grade"] for spec in COMMON["checks"].values()} <= set(golden.GRADES)
    assert {n for n, spec in COMMON["checks"].items() if spec["grade"] == "observe"} == set(mf.OBSERVE_CHECKS)


def test_every_grade_appears():
    assert {case["grade"] for case in CASES} == set(golden.GRADES)


# ─────────────────────────── 기대값 ───────────────────────────


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_expected_judgements_match_rule(case):
    """손으로 적은 기대값이 규칙과 다르면, 검사기가 맞는 답을 틀렸다고 한다."""
    assert case["expectedJudgements"] == mf.expected_judgements(case)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_input_avoids_few_shot_foods(case):
    """입력에 예시 음식이 있으면 예시를 베낀 답이 정답처럼 보인다."""
    eaten = " ".join(item["displayName"] for item in case["input"]["items"])
    assert not [p for p in mf._EXAMPLE_PHRASES if p in eaten]


# ─────────────────────────── 양방향 ───────────────────────────


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_reference_passes(case):
    assert _failed(case, case["reference"]) == set()


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_reference_has_no_observed_flags(case):
    """관측 축도 모범 답에서는 걸리지 않아야 키워드 목록이 맞는지 알 수 있다."""
    flagged = [r.check for r in mf.check_response(case, case["reference"], COMMON) if not r.passed]
    assert flagged == []


@pytest.mark.parametrize(("case", "index"), COUNTEREXAMPLES, ids=COUNTEREXAMPLE_IDS)
def test_counterexample_fails_only_where_declared(case, index):
    """적은 검사에서만 실패해야 한다. 더 많이 실패하면 검사가 너무 넓어 맞는 답도 떨어뜨린다."""
    bad = case["counterexamples"][index]
    assert _failed(case, bad["response"]) == set(bad["failsOn"]), bad["why"]


def test_every_common_check_has_counterexample():
    """반례가 없는 검사는 실패를 잡는지 확인한 적이 없는 검사다."""
    covered = {check for case in CASES for bad in case["counterexamples"] for check in bad["failsOn"]}
    assert set(mf.COMMON_CHECKS) - set(mf.OBSERVE_CHECKS) - covered == set()


# ─────────────────────────── 규칙 동기화 ───────────────────────────


@pytest.mark.skipif(not BACKEND.exists(), reason="AI 컨테이너만 받은 경우 — 모노레포에서만 본다")
def test_rule_copy_matches_backend():
    """BE 가 기준을 바꾸고 AI 사본을 안 고치면 점수와 문장이 조용히 어긋난다."""
    version = re.search(r'STAGE_RULE_VERSION: Final = "(\w+)"', (BACKEND / "__init__.py").read_text("utf-8"))
    profile = (BACKEND / "stage_profile.py").read_text("utf-8")
    sodium = re.search(r'_SODIUM_MG_PER_DAY: Final = Decimal\("(\d+)"\)', profile)
    fiber = re.search(r'_FIBER_G_PER_DAY: Final = Decimal\("(\d+)"\)', profile)

    assert version and version.group(1) == BE_RULE_VERSION == COMMON["beRuleVersion"]
    assert sodium and float(sodium.group(1)) == _SODIUM_MG_PER_DAY
    assert fiber and float(fiber.group(1)) == _FIBER_G_PER_DAY[Stage.MAINTENANCE]
    assert "_FIBER_G_PER_DAY * 2 / 3" in profile
    assert _FIBER_G_PER_DAY[Stage.INITIAL] == pytest.approx(float(fiber.group(1)) * 2 / 3)


# ─────────────────────────── 집계 · 게이트 ───────────────────────────


def _runs(grade, outcomes):
    return {"c": [[golden.CheckResult("x", grade, ok)] for ok in outcomes]}


def test_gate_blocks_on_single_critical_failure():
    assert golden.gate(golden.aggregate(_runs("critical", [True, True, False])))
    assert not golden.gate(golden.aggregate(_runs("critical", [True, True, True])))


def test_gate_uses_threshold_for_normal():
    tallies = golden.aggregate(_runs("normal", [True] * 9 + [False]))
    assert not golden.gate(tallies, threshold=0.9)
    assert golden.gate(tallies, threshold=0.95)


def test_gate_ignores_observe():
    assert not golden.gate(golden.aggregate(_runs("observe", [False, False])))


def test_compare_reports_changes():
    meta = {"beRuleVersion": "v2", "promptHash": "a"}
    old = golden.to_baseline(golden.aggregate(_runs("normal", [True, False])), meta)
    new = golden.to_baseline(golden.aggregate(_runs("normal", [True, True])), {**meta, "promptHash": "b"})
    lines = golden.compare(old, new)
    assert any("promptHash" in line for line in lines)
    assert any(line.startswith("+ c · x") for line in lines)
