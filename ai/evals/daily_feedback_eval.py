"""`/short-feedback` scope=DAILY eval — 케이스(CASES)와 실행 진입점.

`ai/` 에서 `python -m evals.daily_feedback_eval` 로 실행한다(스크립트 경로로 실행하면 `app` 을 못 찾는다).
실제 모델을 부르므로 메인 세션이 사용자 승인 후에만 실행한다. 사용법 · 축 · 판정 한계는 `evals/README.md`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evals.harness import Case

CRASH_LOG = Path(__file__).resolve().parent / "daily_feedback_crashes.log"
"""하네스 크래시 traceback. 루트 `.gitignore` 의 `*.log` 가 막는다."""

INJECTION_MARKER = ""
"""인젝션 케이스 summary 에 넣는 표지 (STUB — 아직 비어 있다)."""

CASES: tuple[Case, ...] = ()


def cases_prompt_hash(cases: Sequence[Case]) -> str:
    """DAILY system 프롬프트 + 케이스마다 실제로 보낼 user 프롬프트의 해시 — baseline 의 비교 조건이다."""
    raise NotImplementedError("cases_prompt_hash 미구현")


def main(argv: Sequence[str] | None = None) -> int:
    """`harness.cli_main` 에 위임한다 — 규칙 버전은 `BE_RULE_VERSION`, 해시는 DAILY 프롬프트."""
    raise NotImplementedError("main 미구현")


if __name__ == "__main__":
    raise SystemExit(main())
