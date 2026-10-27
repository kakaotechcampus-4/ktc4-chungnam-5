"""끼니 피드백(`/short-feedback` `scope=MEAL`) golden dataset — 케이스 로더와 hard 검사기.

모델 출력은 매번 문장이 달라서 정답 문장과 비교하지 않는다. 케이스마다 **입력 + 지켜야 할 속성**을
두고, 속성을 두 갈래로 본다.

- hard — 코드로 판정한다. 이 파일의 `check` 가 맡는다. 시스템 프롬프트의 규칙 중 기계적으로 잴 수 있는 것
  (금지 표현·명령형·의료 표현·지어낸 숫자·Quantity 방향) + 케이스별 `mustMentionAny` · `mustNotContain`
- soft — LLM judge 가 루브릭(`soft`)으로 판정한다. 아직 judge 는 없다. 루브릭만 데이터에 적어 둔다

`expectedJudgements` 는 모델이 아니라 `judge_nutrients` 의 기대값이다. BE 채점 기준이 바뀌었는데
AI 쪽 사본(`short_feedback.py`)을 안 맞추면 여기서 먼저 깨진다.

케이스 입력은 피드백 요청 단계의 모양을 따른다.
- 양은 항상 g 이다 (`unit: "g"`)
- 음식은 이미 food_refs 에 매핑돼 `nutrition` 객체가 있다. 다만 DB 의 성분 컬럼은 비어 있을 수
  있어서, 그 안의 개별 값(`fiberG` 등)이 null 인 케이스는 둔다 — 판정 못 한 영양소를 짐작하는지 본다

간식(SNACK) 끼니는 아직 다루지 않는다. 케이스는 아침·점심·저녁 끼니만 둔다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from app.schemas import ShortFeedbackRequest
from app.short_feedback import (
    _FIBER_G_PER_DAY,
    _SODIUM_MG_PER_DAY,
    MAX_SUGGESTIONS,
    MEALS_PER_DAY,
    FeedbackDraft,
)

CASES_DIR: Final = Path(__file__).parent / "golden" / "meal_feedback"


@dataclass(frozen=True)
class GoldenCase:
    id: str
    description: str
    tags: list[str]
    request: ShortFeedbackRequest
    expected_judgements: dict[str, str]
    must_mention_any: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    reference: dict[str, Any] | None = None


def load_cases(directory: Path = CASES_DIR) -> list[GoldenCase]:
    cases = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        hard = data.get("hard", {})
        cases.append(
            GoldenCase(
                id=data["id"],
                description=data["description"],
                tags=data.get("tags", []),
                request=ShortFeedbackRequest.model_validate(data["input"]),
                expected_judgements=data["expectedJudgements"],
                must_mention_any=hard.get("mustMentionAny", []),
                must_not_contain=hard.get("mustNotContain", []),
                soft=data.get("soft", []),
                reference=data.get("reference"),
            )
        )
    return cases


# ─────────────────────────── 공통 규칙 ───────────────────────────
# `MEAL_SYSTEM_PROMPT` 의 "쓰지 않는 것" 을 옮겼다. 프롬프트 규칙이 바뀌면 여기도 맞춘다.

FORBIDDEN_PHRASES: Final = ["균형 잡힌", "건강한 식습관", "골고루", "적당히", "도움이 됩니다"]
IMPERATIVE: Final = re.compile(r"(드세요|하세요|피하세요|드십시오|하십시오)")
# 약·증상·질병 판단. "용량"·"부작용" 은 단계 설명(`STAGE_RISK`)에 원래 있는 말이라 넣지 않는다 —
# 용량을 바꾸라는 식의 판단은 그 상황을 만든 케이스의 `mustNotContain` 으로 잡는다.
MEDICAL_TERMS: Final = ["복용", "처방", "증상", "질병", "질환", "진료", "병원", "의사"]
# 평소 열량을 몰라 과식·소식 방향을 말할 수 없다. body 만 본다 — reasoning 은 단계 위험
# ("식욕이 돌아와 많이 먹게 되는") 을 설명하느라 같은 말을 쓸 수 있다.
# "양이 적" 처럼 짧게 잡으면 "식이섬유 양이 적었어요" 도 걸린다 — 끼니량을 가리키는 말만 둔다.
QUANTITY_DIRECTION: Final = [
    "과식", "소식", "많이 드셨", "적게 드셨", "많이 먹었", "적게 먹었", "먹은 양이 많", "먹은 양이 적", "식사량이",
]
# 포만감 축을 못 쟀으면 body·reasoning 에서 포만감을 평가하지 않는다.
SATIETY_TERMS: Final = ["포만감", "배고", "허기"]

_NUMBER_WITH_UNIT: Final = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(kcal|mg|g)(?![a-zA-Z])")


def _allowed_numbers(req: ShortFeedbackRequest) -> set[float]:
    """출력에 나와도 되는 숫자 — 입력에 있는 값과 판정에 쓰인 끼니 목표·한도, 그 합계."""
    values: set[float] = {
        round(_FIBER_G_PER_DAY[req.stage] / MEALS_PER_DAY, 1),
        round(_SODIUM_MG_PER_DAY / MEALS_PER_DAY, 1),
    }
    for attr in ("kcal", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg"):
        per_item = [getattr(i.nutrition, attr) for i in req.items if i.nutrition is not None]
        known = [v for v in per_item if v is not None]
        values.update(known)
        if known:
            values.add(round(sum(known), 1))
    values.update(i.amount for i in req.items if i.amount is not None)
    return values


def _invented_numbers(text: str, allowed: set[float]) -> list[str]:
    found = []
    for match in _NUMBER_WITH_UNIT.finditer(text):
        value = float(match.group(1).replace(",", ""))
        if not any(abs(value - a) < 0.05 for a in allowed):
            found.append(match.group(0))
    return found


def check(case: GoldenCase, draft: FeedbackDraft) -> list[str]:
    """hard 속성 위반 목록. 빈 목록이면 통과다."""
    req = case.request
    violations: list[str] = []
    suggestions = draft.suggestions or []
    prose = " ".join([draft.body, draft.reasoning or "", *(s.advice for s in suggestions)])

    if not 1 <= len(suggestions) <= MAX_SUGGESTIONS:
        violations.append(f"제안 개수 {len(suggestions)} — 1~{MAX_SUGGESTIONS}개여야 한다")
    names = [s.food_name for s in suggestions]
    if len(set(names)) != len(names):
        violations.append(f"제안 음식이 겹친다: {names}")

    everything = " ".join([prose, *names])
    violations += [f"금지 표현: {p}" for p in FORBIDDEN_PHRASES if p in everything]
    violations += [f"명령형: {m}" for m in IMPERATIVE.findall(everything)]
    violations += [f"의료 표현: {t}" for t in MEDICAL_TERMS if t in everything]
    violations += [f"Quantity 방향: {t}" for t in QUANTITY_DIRECTION if t in draft.body]

    # 제안 음식명의 "닭가슴살 100g" 같은 1회분 표현은 지어낸 성분이 아니라 숫자 검사에서 뺀다.
    violations += [f"입력에 없는 숫자: {n}" for n in _invented_numbers(prose, _allowed_numbers(req))]

    if req.qqs.satiety is None:
        judged = f"{draft.body} {draft.reasoning or ''}"
        violations += [f"측정 못 한 포만감 축 언급: {t}" for t in SATIETY_TERMS if t in judged]

    if case.must_mention_any and not any(w in draft.body for w in case.must_mention_any):
        violations.append(f"body 가 원인 음식을 짚지 않았다 — {case.must_mention_any} 중 하나가 있어야 한다")
    violations += [f"케이스 금지어: {w}" for w in case.must_not_contain if w in everything]

    return violations
