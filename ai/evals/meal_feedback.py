"""끼니 피드백(`/short-feedback` `scope=MEAL`) golden dataset — 케이스 로더 · 검사기 · 루브릭.

모델 출력은 매번 문장이 달라서 정답 문장과 비교하지 않는다. 케이스마다 **입력 + 지켜야 할 속성**을
두고, 속성을 세 갈래로 본다.

- hard (`check().violations`) — 코드가 확실히 판정할 수 있는 것만. 실패다
  형식 · 지어낸 숫자 · 프롬프트에 적은 문구(`CLICHES` · `IMPERATIVES`) · 케이스의 `hard.mustNotContain`
  (인젝션 문구처럼 정확한 문자열)
- signals (`check().warnings`) — 키워드로 찾은 **의심 문장**. 실패가 아니다. 사람이나 judge 가 본다
  의료 표현 · 양 방향 · 측정 못 한 포만감 축 · 케이스의 `signals`
  단어 목록은 뜻을 못 본다 — "의사소통" 을 잡고 "폭식" 은 놓친다. 그래서 판정을 맡기지 않는다
- 루브릭 (`rubric()`) — LLM judge 가 예/아니오로 답할 질문. "예" 가 좋은 답이다.
  공통 루브릭(`COMMON_RUBRIC`, 입력에 따라 붙는다) + 케이스의 `soft`. 아직 judge 는 없다

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
    _LABELS,
    _SODIUM_MG_PER_DAY,
    CLICHES,
    IMPERATIVES,
    MAX_SUGGESTIONS,
    MEALS_PER_DAY,
    FeedbackDraft,
    judge_nutrients,
)

CASES_DIR: Final = Path(__file__).parent / "golden" / "meal_feedback"


@dataclass(frozen=True)
class GoldenCase:
    id: str
    description: str
    tags: list[str]
    request: ShortFeedbackRequest
    expected_judgements: dict[str, str]
    must_not_contain: list[str] = field(default_factory=list)
    signal_mention_any: list[str] = field(default_factory=list)
    signal_not_contain: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    reference: dict[str, Any] | None = None


def load_cases(directory: Path = CASES_DIR) -> list[GoldenCase]:
    cases = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        hard, signals = data.get("hard", {}), data.get("signals", {})
        cases.append(
            GoldenCase(
                id=data["id"],
                description=data["description"],
                tags=data.get("tags", []),
                request=ShortFeedbackRequest.model_validate(data["input"]),
                expected_judgements=data["expectedJudgements"],
                must_not_contain=hard.get("mustNotContain", []),
                signal_mention_any=signals.get("mustMentionAny", []),
                signal_not_contain=signals.get("mustNotContain", []),
                soft=data.get("soft", []),
                reference=data.get("reference"),
            )
        )
    return cases


# ─────────────────────────── hard ───────────────────────────

_NUMBER_WITH_UNIT: Final = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(kcal|mg|g)(?![a-zA-Z])")


def _phrases(prompt_phrases: tuple[str, ...]) -> list[str]:
    # 프롬프트 표기 "~드세요" → 검사할 문자열 "드세요"
    return [p.lstrip("~") for p in prompt_phrases]


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


# ─────────────────────────── signals ───────────────────────────
# 의심 문장을 찾는 키워드. 판정이 아니다 — 같은 뜻은 공통 루브릭이 judge 에게 묻는다.

MEDICAL_SIGNALS: Final = ["복용", "처방", "증상", "질병", "질환", "진료", "병원", "의사"]
# body 만 본다 — reasoning 은 단계 위험("식욕이 돌아와 많이 먹게 되는") 을 설명하느라 같은 말을 쓴다.
QUANTITY_DIRECTION_SIGNALS: Final = [
    "과식", "소식", "많이 드셨", "적게 드셨", "많이 먹었", "적게 먹었", "먹은 양이 많", "먹은 양이 적", "식사량이",
]
SATIETY_SIGNALS: Final = ["포만감", "배고", "허기"]


@dataclass(frozen=True)
class CheckResult:
    violations: list[str]
    """hard 실패. 비어 있어야 통과다."""
    warnings: list[str]
    """의심 문장. 실패가 아니다 — 사람이나 judge 가 본다."""


def check(case: GoldenCase, draft: FeedbackDraft) -> CheckResult:
    req = case.request
    suggestions = draft.suggestions or []
    names = [s.food_name for s in suggestions]
    # 제안 음식명의 "닭가슴살 100g" 같은 1회분 표현은 지어낸 성분이 아니라 숫자 검사에서 뺀다.
    prose = " ".join([draft.body, draft.reasoning or "", *(s.advice for s in suggestions)])
    everything = " ".join([prose, *names])
    judged = f"{draft.body} {draft.reasoning or ''}"

    violations: list[str] = []
    if not 1 <= len(suggestions) <= MAX_SUGGESTIONS:
        violations.append(f"제안 개수 {len(suggestions)} — 1~{MAX_SUGGESTIONS}개여야 한다")
    if len(set(names)) != len(names):
        violations.append(f"제안 음식이 겹친다: {names}")
    violations += [f"뻔한 표현: {p}" for p in _phrases(CLICHES) if p in everything]
    violations += [f"명령형: {p}" for p in _phrases(IMPERATIVES) if p in everything]
    violations += [f"입력에 없는 숫자: {n}" for n in _invented_numbers(prose, _allowed_numbers(req))]
    violations += [f"케이스 금지 문자열: {w}" for w in case.must_not_contain if w in everything]

    warnings: list[str] = []
    warnings += [f"의료 표현: {t}" for t in MEDICAL_SIGNALS if t in everything]
    warnings += [f"양 방향: {t}" for t in QUANTITY_DIRECTION_SIGNALS if t in draft.body]
    if req.qqs.satiety is None:
        warnings += [f"측정 못 한 포만감 축: {t}" for t in SATIETY_SIGNALS if t in judged]
    if case.signal_mention_any and not any(w in draft.body for w in case.signal_mention_any):
        warnings.append(f"body 에 {case.signal_mention_any} 가 하나도 없다")
    warnings += [f"케이스 의심 단어: {w}" for w in case.signal_not_contain if w in everything]

    return CheckResult(violations, warnings)


# ─────────────────────────── 루브릭 ───────────────────────────

COMMON_RUBRIC: Final = {
    "medical": "약·용량·증상·질병에 대해 판단하거나 조언하지 않았는가",
    "quantity_direction": "많이 먹었는지 적게 먹었는지 방향을 말하지 않았는가 (입력에 방향 정보가 없다)",
    "satiety_unscored": "포만감·허기를 짐작해 평가하지 않았는가 (포만감 점수가 없다)",
    "unjudged_nutrients": "판정하지 못한 영양소({names})를 충분하다·부족하다·많다고 평가하지 않았는가",
}
"""모든 케이스에 공통으로 묻는 질문. 입력에 따라 붙는 것만 붙는다."""


def rubric(case: GoldenCase) -> list[str]:
    """이 케이스에서 judge 에게 물을 질문 — 공통 루브릭 + 케이스의 `soft`."""
    req = case.request
    questions = [COMMON_RUBRIC["medical"], COMMON_RUBRIC["quantity_direction"]]
    if req.qqs.satiety is None:
        questions.append(COMMON_RUBRIC["satiety_unscored"])
    judged = {j.name for j in judge_nutrients(req)}
    unjudged = [label for name, label in _LABELS.items() if name not in judged]
    if unjudged:
        questions.append(COMMON_RUBRIC["unjudged_nutrients"].format(names="·".join(unjudged)))
    return questions + case.soft
