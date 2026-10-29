"""`/analyze-meal` eval — 사진 fixture 라벨로 만든 케이스(CASES)와 실행 진입점.

`ai/` 에서 `python -m evals.analyze_meal_eval` 로 실행한다(스크립트 경로로 실행하면 `app` 을 못 찾는다).
실제 모델을 부르므로 메인 세션이 사용자 승인 후에만 실행한다. 사용법 · 축 · 판정 한계는 `evals/README.md`.

케이스는 **사진** 단위다. 라벨(`LABELS[파일이름]`)이 그 사진에 붙일 축과 기대값을 정하고,
`build_cases` 가 라벨을 Case 로 바꾼다 — 사진을 추가할 때 코드를 고치지 않고 라벨만 더한다.
이미지 GET 은 `fixture_server` 의 로컬 서버가 대신하고, 모델 POST 는 실제다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from evals.harness import Case

FIXTURE_DIR: Path = Path(__file__).resolve().parent / "fixtures" / "analyze_meal"
"""사진 fixture 디렉터리. Dockerfile 이 복사하지 않으므로 이미지에 들어가지 않는다."""

RULE_VERSION: str = "analyze-meal-labels-v1"
"""baseline 비교 조건. analyze-meal 은 BE 규칙 버전이 없어 라벨 · 판정을 바꾸면 이 값을 올린다."""

LABELS: dict[str, dict] = {}
"""사진별 라벨. 키 = fixture 파일 이름. 팀원이 채운다 — 형식은 `evals/README.md`."""


# ─────────────────────────── 빌더 ───────────────────────────


def build_cases(labels: Mapping[str, Mapping], *, n: int = 3) -> tuple[Case, ...]:
    """라벨 하나를 Case 하나로 바꾼다. 잘못된 라벨은 파일 이름을 담은 ValueError."""
    raise NotImplementedError("build_cases 미구현")


# STUB: `build_cases` 가 미구현이라 import 시점에 부르면 터진다. GREEN 에서 `build_cases(LABELS)` 로 바꾼다
CASES: tuple[Case, ...] = ()


# ─────────────────────────── 실행 ───────────────────────────


def cases_prompt_hash(cases: Sequence[Case]) -> str:
    """케이스가 쓰는 mealType 각각의 `_build_prompt` 해시 — baseline 의 비교 조건이다."""
    raise NotImplementedError("cases_prompt_hash 미구현")


def main(argv: Sequence[str] | None = None) -> int:
    """`serve_fixtures` 로 서버를 띄운 채 `harness.cli_main` 에 위임한다.

    채널 · 키 확인 · 서버는 호출 시점에 만든다 — import 만으로 `.env` 를 읽거나 서버를 띄우지 않는다.
    """
    raise NotImplementedError("main 미구현")


if __name__ == "__main__":
    raise SystemExit(main())
