"""음식 분석(`/analyze-meal`) golden dataset — 케이스 로더 · 검사기.

**입력은 항상 사진이다.** `rawText` 는 쓰지 않는다. 케이스는 사진 URL(`imageUrl`)과 정답을 함께 둔다.
**사진 파일은 저장소에 넣지 않는다** — 저작권. 계약이 사진을 URL 로 받으므로 원본 주소를 그대로 보낸다.
원본이 옮겨지거나 지워지면 그 케이스는 돌릴 수 없다.
**양은 항상 g 으로 나와야 한다.** BE 는 g 이 아닌 단위("2개")를 g 으로 옮기지 못해 성분을 못 구한다
(`backend/app/services/meal.py::to_grams`).

평가는 **음식명과 양** 두 가지만 본다. 둘 다 코드로 판정한다(hard) — 끼니 피드백 · AI 코치와 달리
signals · 루브릭이 없다. 신뢰도 · `clarifyQuestion` · `safetyStatus` 는 보지 않는다.

- 음식명 — 정답 음식마다 허용 이름 목록(`names`)을 둔다. 같은 음식을 가리키는 이름이 여럿일 수 있어서다
  ("공깃밥" · "쌀밥"). 공백은 무시한다. AI 항목과 정답 음식을 1:1 로 짝지은 뒤
  - 짝이 없는 정답 음식 → 빠뜨림
  - 짝이 없는 AI 항목 → 사진에 없는 음식
  - 단, 케이스의 `mayAlsoContain` 에 있는 이름(곁들임 · 고명처럼 내도 틀리지 않은 것)은 세지 않는다
- 양 — 짝지은 항목마다
  - 단위가 g 이 아니면 실패
  - 정답 g 과의 오차가 허용 비율(케이스 `amountTolerance`, 없으면 `DEFAULT_AMOUNT_TOLERANCE`)을 넘으면 실패.
    **정답 g 을 사진 실측으로 확인한 케이스(`amountVerified: true`)만** 본다 — 확인 안 된 g 으로 실패를
    매기면 모델이 아니라 정답을 재는 셈이다

케이스의 `source` 는 사진 · 정답 후보를 가져온 곳이다. 지금 케이스는 농촌진흥청 메뉴젠(농사로)에서 모았고,
g 은 메뉴젠의 `fd_Wgh` 후보값이라 사진 속 양인지 확인되지 않았다.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from app.schemas import AnalyzeMealRequest, MealType, Stage

CASES_DIR: Final = Path(__file__).parent / "golden" / "food_analysis"

DEFAULT_AMOUNT_TOLERANCE: Final = 0.3
"""정답 g 대비 허용 오차 비율. 사진만으로 양을 맞히기 어려워 넉넉히 잡았다. 팀 합의 전 임시값."""


@dataclass(frozen=True)
class ExpectedItem:
    names: list[str]
    """허용 이름. 첫 번째가 대표 이름이다."""
    amount_g: float
    """정답 g. `GoldenCase.amount_verified` 가 False 면 후보값이다."""


@dataclass(frozen=True)
class GoldenCase:
    id: str
    description: str
    tags: list[str]
    image_url: str
    meal_type: MealType
    stage: Stage
    expected: list[ExpectedItem]
    may_also_contain: list[str] = field(default_factory=list)
    amount_verified: bool = False
    amount_tolerance: float = DEFAULT_AMOUNT_TOLERANCE

    def request(self) -> AnalyzeMealRequest:
        """사진 URL 하나로 요청을 만든다. `rawText` 는 넣지 않는다."""
        return AnalyzeMealRequest(
            meal_id=f"golden-{self.id}",
            meal_type=self.meal_type,
            eaten_at=dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))),
            stage=self.stage,
            image_url=self.image_url,
        )


def parse_case(data: dict[str, Any]) -> GoldenCase:
    return GoldenCase(
        id=data["id"],
        description=data["description"],
        tags=data.get("tags", []),
        image_url=data["imageUrl"],
        meal_type=MealType(data["mealType"]),
        stage=Stage(data["stage"]),
        expected=[ExpectedItem(names=e["names"], amount_g=e["amountG"]) for e in data["expected"]],
        may_also_contain=data.get("mayAlsoContain", []),
        amount_verified=data["amountVerified"],
        amount_tolerance=data.get("amountTolerance", DEFAULT_AMOUNT_TOLERANCE),
    )


def load_cases(directory: Path = CASES_DIR) -> list[GoldenCase]:
    """`_` 로 시작하는 파일(`_template.json`)은 케이스가 아니다."""
    return [
        parse_case(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
        if not path.name.startswith("_")
    ]


# ─────────────────────────── 검사 ───────────────────────────


@dataclass(frozen=True)
class Item:
    """AI 가 낸 항목 중 평가에 쓰는 것."""

    name: str
    amount: float
    unit: str


def parse_items(raw: dict[str, Any]) -> list[Item]:
    """`AnalyzeMealResponse` JSON → 평가할 항목."""
    return [
        Item(name=i["originalFoodName"], amount=i["estimatedAmount"], unit=i["unit"])
        for i in raw.get("items", [])
    ]


def _norm(name: str) -> str:
    return "".join(name.split())


@dataclass(frozen=True)
class CheckResult:
    violations: list[str]
    """비어 있어야 통과다."""
    matched: int
    """짝지은 정답 음식 수. 음식명 재현율 = matched / len(case.expected)."""


def check(case: GoldenCase, items: list[Item]) -> CheckResult:
    violations: list[str] = []
    remaining = list(items)
    matched = 0

    for expected in case.expected:
        allowed = {_norm(n) for n in expected.names}
        found = next((i for i in remaining if _norm(i.name) in allowed), None)
        if found is None:
            violations.append(f"빠뜨림: {expected.names[0]}")
            continue
        remaining.remove(found)
        matched += 1

        if found.unit.strip() != "g":
            violations.append(f"단위가 g 이 아니다: {found.name} {found.amount:g}{found.unit}")
            continue
        if not case.amount_verified:
            continue
        error = abs(found.amount - expected.amount_g) / expected.amount_g
        if error > case.amount_tolerance:
            violations.append(
                f"양 오차: {found.name} {found.amount:g}g "
                f"(정답 {expected.amount_g:g}g, 허용 ±{case.amount_tolerance:.0%})"
            )

    extra_ok = {_norm(n) for n in case.may_also_contain}
    violations += [f"사진에 없는 음식: {i.name}" for i in remaining if _norm(i.name) not in extra_ok]
    return CheckResult(violations, matched)
