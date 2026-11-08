"""추천 음식 다양성 — 여러 응답의 추천을 주재료로 묶어 쏠림을 잰다 (관측 지표, 게이트 밖).

`example_no_leak` 은 응답 하나가 few-shot 예시를 베꼈는지 본다. 이 지표는 응답 여러 개를 모아,
무엇을 먹었든 같은 음식이 추천되는지 본다. "두부 반 모" 와 "두부구이 반 모" 는 글자는 달라도 둘 다
두부라 같은 음식으로 센다 — 사용자에게 문제가 되는 건 음식의 반복이지 표현의 반복이 아니다.

baseline 의 관측 축 samples 에서 응답 원문을 모아 계산한다:

    python -X utf8 -m evals.suggestion_diversity evals/baselines/meal_feedback_baseline.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections.abc import Iterable

# (주재료, 이름에 들어 있으면 그 주재료로 보는 낱말). 한 이름이 여러 주재료를 가질 수 있다
# ("닭가슴살 샐러드" → 닭고기 · 채소). 낱말 목록은 실측에 나온 추천 이름에서 시작했다.
_MAIN_FOODS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("두부", ("두부",)),
    ("달걀", ("달걀", "계란", "메추리알")),
    ("닭고기", ("닭",)),
    ("소고기", ("소고기", "쇠고기", "우둔", "홍두깨")),
    ("돼지고기", ("돼지", "제육", "수육")),
    ("생선", ("생선", "고등어", "연어", "삼치", "대구", "참치", "명태", "동태", "갈치", "조기", "가자미")),
    ("해산물", ("새우", "오징어", "조개", "홍합", "굴")),
    ("콩", ("콩", "두유", "에다마메")),
    ("유제품", ("요거트", "요구르트", "우유", "치즈")),
    ("나물", ("나물", "시금치")),
    ("채소", ("샐러드", "쌈채소", "채소", "양배추", "상추", "파프리카", "양상추")),
    ("브로콜리", ("브로콜리",)),
    ("버섯", ("버섯",)),
    ("오이", ("오이",)),
    ("토마토", ("토마토",)),
    ("고구마", ("고구마",)),
    ("감자", ("감자",)),
    ("잡곡밥", ("현미", "잡곡", "귀리", "보리밥")),
    ("과일", ("사과", "바나나", "키위", "베리", "귤", "배 ")),
)
# 다른 주재료 낱말을 품은 이름 — 먼저 지운 뒤 찾는다("콩나물" 은 콩이 아니라 나물이다)
_SHADOWED = (("콩나물", "나물"),)

_SERVING_TAIL = re.compile(
    r"(?:\s+(?:\d+(?:\.\d+)?\S*|(?:한|두|세|네|반)(?:\s*\S+)?|\S*(?:개|모|공기|그릇|접시|줌|컵|잔|조각|토막|팩|봉지|봉|알|송이)))+$"
)


def main_foods(food_name: str) -> set[str]:
    """추천 이름의 주재료 집합. 표에 없으면 끝의 1회분 표현을 뗀 이름 하나."""
    name = food_name.strip()
    found: set[str] = set()
    for word, main in _SHADOWED:
        if word in name:
            found.add(main)
            name = name.replace(word, " ")
    for main, words in _MAIN_FOODS:
        if any(word in name for word in words):
            found.add(main)
    if found:
        return found
    return {_SERVING_TAIL.sub("", food_name.strip()) or food_name.strip()}


def diversity(responses: Iterable[dict | None]) -> dict:
    """추천이 있는 응답들의 주재료 분포.

    - `responses`: 추천이 1개 이상인 응답 수 (저녁 · 차단 응답은 빠진다)
    - `distinct`: 서로 다른 주재료 수
    - `top`: (주재료, 그 주재료가 든 응답 수, 비율) — 많은 순. 한 응답 안의 중복은 한 번 센다
    """
    per_response = []
    for resp in responses:
        suggestions = (resp or {}).get("suggestions") or []
        names = [s.get("foodName") for s in suggestions if isinstance(s, dict) and s.get("foodName")]
        if names:
            per_response.append(set().union(*(main_foods(n) for n in names)))
    counts = Counter(food for foods in per_response for food in foods)
    total = len(per_response)
    return {
        "responses": total,
        "distinct": len(counts),
        "top": [(food, count, count / total) for food, count in counts.most_common()],
    }


def responses_from_baseline(baseline: dict) -> dict[str, list[dict]]:
    """케이스별 응답 원문 — 관측 축 samples 에 회차마다 응답이 남는다. 관측 축이 없는 케이스는 빠진다."""
    found: dict[str, list[dict]] = {}
    for key, result in baseline["results"].items():
        case_id = key.split("/")[0]
        if case_id not in found and result.get("samples"):
            found[case_id] = [sample["response"] for sample in result["samples"]]
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("사용: python -m evals.suggestion_diversity <baseline.json>")
        return 2
    with open(args[0], encoding="utf-8") as f:
        by_case = responses_from_baseline(json.load(f))
    result = diversity(r for responses in by_case.values() for r in responses)
    print(f"케이스 {len(by_case)}개 · 추천 있는 응답 {result['responses']}개 · 서로 다른 주재료 {result['distinct']}가지")
    for food, count, ratio in result["top"][:10]:
        print(f"  {food:<8} {count:>3}회  {ratio:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
