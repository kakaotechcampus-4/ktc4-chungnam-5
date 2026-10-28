"""`/short-feedback` scope=MEAL eval — 케이스(CASES)와 실행 진입점.

`ai/` 에서 `python -m evals.meal_feedback_eval` 로 실행한다(스크립트 경로로 실행하면 `app` 을 못 찾는다).
실제 모델을 부르므로 메인 세션이 사용자 승인 후에만 실행한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from evals.harness import Case

CASES: tuple[Case, ...] = ()


def main(argv: Sequence[str] | None = None) -> int:
    """`harness.cli_main` 에 위임한다 — 규칙 버전은 `BE_RULE_VERSION`, 해시는 MEAL 프롬프트."""
    raise NotImplementedError("main 미구현")


if __name__ == "__main__":
    raise SystemExit(main())
