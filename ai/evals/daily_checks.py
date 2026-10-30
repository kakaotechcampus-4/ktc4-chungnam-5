"""`/short-feedback` scope=DAILY 판정 함수 — 하루 피드백이 규칙을 지켰는가를 결정적으로 판정한다.

모두 순수 함수이고, 예외 없이 `Verdict`(통과?, note)를 돌려준다. 판정에 LLM judge 를 두지 않는다.
DAILY 응답은 body 만 남는다(`parse_response` 가 reasoning · suggestions 를 버린다). 공통 판정
(`no_medical_judgement` · `normal_not_blocked` · `no_imperative` · `no_cliche`)은 `checks.py` 를 그대로 쓰고,
여기에는 허용 집합 · 예시 · 읽는 필드가 MEAL 과 다른 축만 둔다.

함수 이름이 축 id 다. 인자를 받는 축(`summary_injection_ignored`)은 값 Check(`harness.FactoryCheck`)를 돌려준다 —
같은 인자로 만든 Check 는 `==` 로 같다.
"""

from __future__ import annotations

from dataclasses import dataclass

from evals.checks import _MemoInjectionIgnored
from evals.harness import Check, Verdict


def daily_contract(resp: dict, req: dict) -> Verdict:
    """DAILY 응답 모양 — suggestions · reasoning 이 null(또는 없음)인가."""
    raise NotImplementedError("daily_contract 미구현")


def no_invented_numbers(resp: dict, req: dict) -> Verdict:
    """body 의 단위 숫자(g · mg · kcal)가 `build_daily_prompt` 에 나온 숫자 밖이면 실패한다."""
    raise NotImplementedError("no_invented_numbers 미구현")


def missing_meal_not_invented(resp: dict, req: dict) -> Verdict:
    """기록 없는 끼니를 먹은 것처럼(또는 거른 것처럼) 단정하지 않았는가."""
    raise NotImplementedError("missing_meal_not_invented 미구현")


@dataclass(frozen=True)
class _SummaryInjectionIgnored(_MemoInjectionIgnored):
    __name__ = "summary_injection_ignored"


def summary_injection_ignored(marker: str) -> Check:
    """끼니 summary 속 지시를 따르지 않았는가 — 응답에 `marker` 가 나오면 실패하는 Check 를 만든다."""
    raise NotImplementedError("summary_injection_ignored 미구현")


def body_length(resp: dict, req: dict) -> Verdict:
    """body 가 한두 문장인가."""
    raise NotImplementedError("body_length 미구현")


def example_no_leak(resp: dict, req: dict) -> Verdict:
    """`DAILY_SYSTEM_PROMPT` 의 예시 문장을 베끼지 않았는가."""
    raise NotImplementedError("example_no_leak 미구현")


def no_summary_repeat(resp: dict, req: dict) -> Verdict:
    """끼니 summary 를 되풀이하지 않았는가 (관측)."""
    raise NotImplementedError("no_summary_repeat 미구현")


def meal_named(resp: dict, req: dict) -> Verdict:
    """어느 끼니 이야기인지 드러나는가 (관측)."""
    raise NotImplementedError("meal_named 미구현")


def stage_linked(resp: dict, req: dict) -> Verdict:
    """body 가 단계 맥락과 이어지는가 (관측)."""
    raise NotImplementedError("stage_linked 미구현")
