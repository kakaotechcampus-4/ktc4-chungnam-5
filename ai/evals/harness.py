"""eval 하네스 — 케이스를 N회 실행해 축별 통과율을 모으고, baseline 과 비교하고, 게이트를 판정한다.

채널은 `TestClient(app, raise_server_exceptions=False)` 다. 모듈 함수를 직접 부르면 라우트의
래핑·가드레일을 건너뛰기 때문이다. 5xx 응답은 판정 대상(`contract_parseable` 실패)이고,
하네스 자신의 예외만 크래시로 센다.

등급:
- `critical` — 1회 실패면 게이트 FAIL
- `non_critical` — 축의 `pass_ratio`(없으면 CLI `--pass-ratio`, 기본 0.66) 이상이면 통과
- `ambiguous` — 관측. 게이트에서 빠지고 통과 회차 응답도 `samples` 로 남긴다

결과 dict 키는 `"<case_id>/<axis_id>"`(계약은 `"<case_id>/contract_parseable"`),
값은 `{"passed", "n", "errors", "grade", "pass_ratio", "notes", "samples"}` 다.
엔드포인트 URL · API 키는 결과 · 로그 · baseline 어디에도 남기지 않는다(`redact`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# ─────────────────────────── 타입 ───────────────────────────

Verdict = tuple[bool, str]
"""(통과?, note)."""

Check = Callable[[dict, dict], Verdict]
"""(응답 JSON camelCase, 요청 JSON camelCase) -> Verdict."""

ContractCheck = Callable[[int, object], Verdict]
"""(status_code, payload) -> Verdict."""

Grade = Literal["critical", "non_critical", "ambiguous"]


@dataclass(frozen=True)
class Observation:
    """한 회차의 응답. 하네스가 판정하는 단위다."""

    status_code: int
    payload: object
    """JSON 파싱 결과. 파싱 실패면 None."""


Post = Callable[[str, dict], Observation]
"""(path, 요청 JSON) -> Observation."""


@dataclass(frozen=True)
class Axis:
    """판정 축 하나 — 같은 응답을 여러 축이 판정한다."""

    id: str
    check: Check
    grade: Grade
    pass_ratio: float | None = None
    """None 이면 CLI `--pass-ratio`(기본 0.66)를 쓴다."""


@dataclass(frozen=True)
class Case:
    """요청 하나와 그 응답을 판정할 축들. 요청 JSON 이 seed 다."""

    id: str
    path: str
    """예: "/short-feedback"."""
    request: dict
    contract: ContractCheck
    axes: tuple[Axis, ...]
    n: int = 3


# ─────────────────────────── 채널 · 키 ───────────────────────────


def default_post() -> Post:
    """`TestClient(app, raise_server_exceptions=False)` 로 `main.py` 를 거치는 Post 를 만든다.

    import 시점이 아니라 호출 시점에 앱을 만든다.
    """
    raise NotImplementedError("default_post 미구현")


def has_api_key() -> bool:
    """`app.config.get_settings()` 로 모델 키가 설정돼 있는지 확인한다."""
    raise NotImplementedError("has_api_key 미구현")


# ─────────────────────────── 실행 ───────────────────────────


def run_case(
    case: Case, post: Post, *, n: int | None = None, crash_log: Path | None = None
) -> dict[str, dict]:
    """케이스를 n 회 실행하고 한 응답을 모든 축으로 판정한다.

    계약 실패 회차는 다른 축 분모에서 빠진다. 하네스 예외는 크래시(`errors`)로 세고 로그에 남긴다.
    """
    raise NotImplementedError("run_case 미구현")


def run(
    cases: Sequence[Case],
    post: Post,
    *,
    n: int | None = None,
    only: set[str] | None = None,
    crash_log: Path | None = None,
) -> dict[str, dict]:
    """케이스 전체(또는 `only` 에 든 것만)를 실행해 결과를 합친다."""
    raise NotImplementedError("run 미구현")


def record_crash(log_path: Path, case_id: str, exc: BaseException, seen: set[str]) -> None:
    """크래시 traceback 을 로그에 남긴다. 같은 예외는 한 번만, URL · 토큰은 가린다."""
    raise NotImplementedError("record_crash 미구현")


# ─────────────────────────── 집계 · 게이트 · 비교 ───────────────────────────


def judged_n(result: dict) -> int:
    """판정한 회차 수 — 크래시 회차를 분모에서 뺀다."""
    raise NotImplementedError("judged_n 미구현")


def gate(
    results: dict[str, dict], *, default_pass_ratio: float = 0.66, max_errors: int = 0
) -> tuple[bool, list[str]]:
    """등급별 기준으로 게이트를 판정한다. (PASS?, 실패 사유 줄 목록)."""
    raise NotImplementedError("gate 미구현")


def compare(
    current: dict[str, dict], baseline_doc: dict, *, rule_version: str, prompt_hash: str
) -> list[str]:
    """baseline 과 통과율(횟수 아님)로 비교해 보고 줄 목록을 돌려준다."""
    raise NotImplementedError("compare 미구현")


def make_baseline(results: dict[str, dict], *, rule_version: str, prompt_hash: str) -> dict:
    """저장할 baseline 문서를 만든다 — 규칙 버전 · 프롬프트 해시 · 키별 n · 결과."""
    raise NotImplementedError("make_baseline 미구현")


def prompt_hash(*texts: str) -> str:
    """프롬프트 텍스트들의 결정적 해시. 한 글자만 달라도 바뀐다."""
    raise NotImplementedError("prompt_hash 미구현")


def redact(text: str) -> str:
    """엔드포인트 URL · Bearer 토큰을 가린다. 일반 문장은 그대로 둔다."""
    raise NotImplementedError("redact 미구현")


# ─────────────────────────── CLI ───────────────────────────


def cli_main(
    argv: Sequence[str] | None,
    *,
    cases: Sequence[Case],
    post_factory: Callable[[], Post],
    has_key: Callable[[], bool],
    rule_version: str,
    prompt_hash: str,
    crash_log: Path | None = None,
) -> int:
    """CLI 진입점. 게이트 PASS 0 / FAIL 1 / 오용 2 / 키 없음 · 미연결 SKIP 0.

    옵션: `--n` · `--pass-ratio` · `--save` · `--baseline` · `--max-errors` · `--only`.
    """
    raise NotImplementedError("cli_main 미구현")
