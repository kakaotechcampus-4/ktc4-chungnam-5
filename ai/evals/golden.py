"""골든 데이터셋 공통 — 케이스 읽기 · 형식 검사 · 집계 · 게이트 · baseline 비교.

영역(끼니 피드백, 챗봇 …)마다 다른 건 **검사 내용**뿐이다. 케이스를 읽고, 여러 번 돌린 결과를
통과율로 묶고, 등급에 따라 게이트를 정하는 일은 여기서 한 번만 한다.

등급은 검사마다 붙는다. 공통 검사의 등급은 영역 폴더의 `_common.json` 이 정하고, 케이스에만
있는 검사(`hard`)는 케이스의 `grade` 를 따른다. 케이스 `grade` 가 `observe` 면 그 케이스의 모든
검사가 관측으로 내려간다 — 정답이 팀 합의 전인 케이스가 게이트를 막지 않게 하려는 것이다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

GOLDEN_DIR: Final = Path(__file__).parent / "golden"

Grade = Literal["critical", "normal", "observe"]
GRADES: Final = ("critical", "normal", "observe")
SOURCES: Final = ("design", "incident", "review")
"""사전 설계 · 실제 사고 · 리뷰 중 발견."""
GROUPS: Final = ("정상", "경계값", "결측", "안전", "인젝션")
"""어떤 종류의 위험을 보는 케이스인가. 분포로 빈틈을 찾는다."""

REQUIRED_FIELDS: Final = (
    "id",
    "description",
    "grade",
    "source",
    "group",
    "tags",
    "input",
    "hard",
    "signals",
    "review",
    "reference",
    "counterexamples",
)

AREA_PREFIX: Final = {"meal_feedback": "MF"}
"""영역 폴더 → 케이스 id 접두어."""

DEFAULT_THRESHOLD: Final = 0.9
"""normal 검사의 임계 통과율."""


@dataclass(frozen=True)
class CheckResult:
    check: str
    grade: Grade
    passed: bool
    detail: str = ""


# ─────────────────────────── 읽기 · 형식 검사 ───────────────────────────


def load_common(area: str) -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / area / "_common.json").read_text(encoding="utf-8"))


def case_paths(area: str) -> list[Path]:
    return sorted(p for p in (GOLDEN_DIR / area).glob("*.json") if not p.name.startswith("_"))


def load_cases(area: str) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in case_paths(area)]


def validate_case(case: dict[str, Any], path: Path, area: str) -> list[str]:
    """형식 오류 목록. 비어 있으면 통과다. 내용(기대값이 맞는지)은 영역 검사가 본다."""
    errors = [f"필드 없음: {name}" for name in REQUIRED_FIELDS if name not in case]
    if errors:
        return errors

    if case["id"] != path.stem:
        errors.append(f"id({case['id']}) 와 파일명({path.stem}) 이 다르다")
    if not re.fullmatch(rf"{AREA_PREFIX[area]}-\d{{2}}-[a-z0-9-]+", case["id"]):
        errors.append(f"id 형식이 {AREA_PREFIX[area]}-nn-kebab-case 가 아니다")
    if case["grade"] not in GRADES:
        errors.append(f"grade 허용값 아님: {case['grade']}")
    if case["source"] not in SOURCES:
        errors.append(f"source 허용값 아님: {case['source']}")
    if case["group"] not in GROUPS:
        errors.append(f"group 허용값 아님: {case['group']}")
    if case["source"] == "incident" and not case.get("incident"):
        errors.append("source=incident 면 어떤 사고였는지 incident 에 적는다")
    if not isinstance(case["counterexamples"], list) or not case["counterexamples"]:
        errors.append("counterexamples 가 하나 이상 있어야 한다 (판정 함수 양방향 검증)")
    for i, bad in enumerate(case.get("counterexamples") or []):
        if not {"why", "response", "failsOn"} <= bad.keys():
            errors.append(f"counterexamples[{i}] 에 why · response · failsOn 이 필요하다")
    return errors


# ─────────────────────────── 집계 · 게이트 ───────────────────────────


@dataclass
class Tally:
    grade: Grade
    passed: int = 0
    runs: int = 0

    @property
    def rate(self) -> float:
        return self.passed / self.runs if self.runs else 0.0


def aggregate(runs: dict[str, list[list[CheckResult]]]) -> dict[str, dict[str, Tally]]:
    """케이스 id → (검사 이름 → 통과 횟수). runs 는 케이스마다 N 번 돌린 검사 결과다."""
    tallies: dict[str, dict[str, Tally]] = {}
    for case_id, attempts in runs.items():
        per_check = tallies.setdefault(case_id, {})
        for results in attempts:
            for r in results:
                tally = per_check.setdefault(r.check, Tally(r.grade))
                tally.runs += 1
                tally.passed += r.passed
    return tallies


def gate(tallies: dict[str, dict[str, Tally]], threshold: float = DEFAULT_THRESHOLD) -> list[str]:
    """게이트를 막는 이유 목록. 비어 있으면 통과다.

    critical 은 N 번 중 한 번만 실패해도 막는다 (Pass^k). 사용자는 한 번 틀린 응답도 그대로 받는다.
    normal 은 임계 통과율 아래면 막는다. observe 는 기록만 한다.
    """
    reasons = []
    for case_id, per_check in tallies.items():
        for check, t in per_check.items():
            if t.grade == "critical" and t.passed < t.runs:
                reasons.append(f"[critical] {case_id} · {check} {t.passed}/{t.runs}")
            elif t.grade == "normal" and t.rate < threshold:
                reasons.append(f"[normal] {case_id} · {check} {t.passed}/{t.runs} < {threshold:.0%}")
    return reasons


def to_baseline(tallies: dict[str, dict[str, Tally]], meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "meta": meta,
        "cases": {
            case_id: {check: f"{t.passed}/{t.runs}" for check, t in sorted(per_check.items())}
            for case_id, per_check in sorted(tallies.items())
        },
    }


def compare(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """baseline 과 달라진 (케이스, 검사) 목록. 통과율이 오르면 +, 내리면 - 를 붙인다."""

    def rate(cell: str | None) -> float | None:
        if cell is None:
            return None
        passed, runs = map(int, cell.split("/"))
        return passed / runs if runs else None

    lines = []
    for key in ("beRuleVersion", "promptHash"):
        if old["meta"].get(key) != new["meta"].get(key):
            lines.append(f"  meta.{key}: {old['meta'].get(key)} → {new['meta'].get(key)}")
    for case_id in sorted(old["cases"].keys() | new["cases"].keys()):
        before, after = old["cases"].get(case_id, {}), new["cases"].get(case_id, {})
        for check in sorted(before.keys() | after.keys()):
            a, b = rate(before.get(check)), rate(after.get(check))
            if a == b:
                continue
            mark = "+" if a is not None and b is not None and b > a else "-"
            lines.append(f"{mark} {case_id} · {check}: {before.get(check, '없음')} → {after.get(check, '없음')}")
    return lines
