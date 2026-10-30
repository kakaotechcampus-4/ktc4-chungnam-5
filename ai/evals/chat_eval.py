"""`/chat` eval — `ai-stub` 계약 기준으로 만든 케이스(CASES)와 실행 진입점.

`ai/` 에서 `python -m evals.chat_eval` 로 실행한다(스크립트 경로로 실행하면 `app` 을 못 찾는다).
실제 모델을 부르므로 메인 세션이 사용자 승인 후에만 실행한다. 사용법 · 축 · 판정 한계는 `evals/README.md`.

AI 에는 아직 `/chat` 라우트가 없다 — 라우트가 생기기 전까지 실행은 `SKIP: 미연결`(404)로 끝난다.
이전 턴은 요청 JSON 의 history 에 고정 데이터로 넣는다(실제 모델로 재생하지 않는다).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evals.harness import Case

RULE_VERSION: str = "chat-cases-v1"
"""baseline 비교 조건. chat 은 BE 규칙 버전이 없어 케이스 · 판정을 바꾸면 이 값을 올린다."""

CRASH_LOG = Path(__file__).resolve().parent / "chat_crashes.log"
"""하네스 크래시 traceback. 루트 `.gitignore` 의 `*.log` 가 막는다."""

CASES: tuple[Case, ...] = ()


# ─────────────────────────── 실행 ───────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    """`harness.cli_main` 에 위임한다 — import 만으로 `.env` 를 읽지 않는다."""
    raise NotImplementedError("main 미구현")


if __name__ == "__main__":
    raise SystemExit(main())
