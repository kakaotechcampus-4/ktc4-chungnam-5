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

1단계 `chungnam-clone` `evals/week06_eval.py` 하단부(실행 · 집계 · 비교 · 게이트 · CLI)에서 이식했다.
바뀐 점: 응답 하나를 여러 축이 판정한다 · n 은 케이스별 · 임계는 축별 · `compare` 는 줄 목록을 돌려준다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import _GATEWAY_PREFIX  # 모듈 상수만 읽는다 — `.env` 는 `get_settings()` 를 불러야 읽힌다

# ─────────────────────────── 타입 ───────────────────────────

Verdict = tuple[bool, str]
"""(통과?, note)."""

Check = Callable[[dict, dict], Verdict]
"""(응답 JSON camelCase, 요청 JSON camelCase) -> Verdict."""

ContractCheck = Callable[[int, object], Verdict]
"""(status_code, payload) -> Verdict."""

Grade = Literal["critical", "non_critical", "ambiguous"]


class FactoryCheck:
    """팩토리가 인자를 묶어 만드는 Check 의 공용 기반 — 같은 팩토리 · 같은 인자면 `==` 로 같은 값이다.

    클로저는 호출마다 새 함수라 같은 라벨로 만든 `Case` 끼리도 `==` 가 거짓이 된다. 그래서 팩토리는 값을 돌려준다.
    서브클래스는 판정 모듈에 `@dataclass(frozen=True)` 로 두고
    - 판정에 쓰는 인자를 불변 필드(tuple · frozenset)로 받는다 — 팩토리가 생성 시점에 복사 · 정규화해 넘긴다
    - `__name__` 을 클래스 속성으로 팩토리 이름에 맞춘다 — 빌더가 `check.__name__` 을 축 id 로 읽는다
    - `__call__(resp, req) -> Verdict` 를 구현한다

    `==` 는 dataclass 규칙대로 클래스가 같고 필드가 모두 같을 때만 참이다 — 다른 팩토리끼리는 인자가 같아도 다르다.
    `__module__` 은 서브클래스를 정의한 판정 모듈이다. 해시는 요구하지 않는다(`Case.request` 가 dict 라 `Case` 도 해시 불가).
    """

    def __call__(self, resp: dict, req: dict) -> Verdict:
        raise NotImplementedError(f"{type(self).__qualname__}.__call__ 미구현")


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


CONTRACT_AXIS_ID = "contract_parseable"
"""계약 축의 결과 키 이름. 계약은 항상 critical 이다."""

DEFAULT_PASS_RATIO = 0.66

_UNCONNECTED_STATUSES = frozenset({404, 501})
"""모델 미연결로 보는 status — 라우트가 없거나(404) 아직 구현되지 않았다(501). 결정 B."""

_MAX_NOTES = 2
"""키마다 남기는 실패 note 수. 원인을 보기엔 충분하고 baseline 이 부풀지 않는다."""

_RATIO_EPS = 1e-9
"""`judged * pass_ratio` 의 부동소수 오차(0.7 * 10 = 7.000000000000001)로 경계값이 떨어지지 않게 한다."""


# ─────────────────────────── 채널 · 키 ───────────────────────────


def default_post() -> Post:
    """`TestClient(app, raise_server_exceptions=False)` 로 `main.py` 를 거치는 Post 를 만든다.

    import 시점이 아니라 호출 시점에 앱을 만든다 — `evals.harness` 를 import 하는 것만으로
    설정(`.env`)을 읽지 않게 한다. 라우트가 예외로 죽어도 500 응답으로 받아 계약 실패로 판정한다.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)

    def post(path: str, request: dict) -> Observation:
        response = client.post(path, json=request)
        try:
            payload = response.json()
        except ValueError:  # JSON 이 아닌 본문 — 계약 판정이 None 을 실패로 본다
            payload = None
        return Observation(status_code=response.status_code, payload=payload)

    return post


def has_api_key() -> bool:
    """`app.config.get_settings()` 로 모델 키가 설정돼 있는지 확인한다.

    필수 설정(키 · 엔드포인트 URL)이 없으면 `Settings` 가 검증 오류를 낸다 — 그때는 키가 없는 것으로 본다.
    """
    from pydantic import ValidationError

    from app.config import get_settings

    try:
        settings = get_settings()
    except ValidationError:
        return False
    return bool(settings.LLM_API_KEY.get_secret_value().strip())


# ─────────────────────────── 실행 ───────────────────────────


def _empty_result(grade: Grade, pass_ratio: float | None) -> dict:
    return {
        "passed": 0,
        "n": 0,
        "errors": 0,
        "grade": grade,
        "pass_ratio": pass_ratio,
        "notes": [],
        "samples": [],
    }


def _add_note(result: dict, note: str) -> None:
    if len(result["notes"]) < _MAX_NOTES:
        result["notes"].append(redact(note))


def _crash_note(exc: BaseException) -> str:
    # 가린 뒤에 자른다 — 먼저 자르면 URL 앞부분이 패턴에 안 걸린 채 남는다.
    return f"ERR:{type(exc).__name__}:{redact(str(exc))[:60]}"


def _redact_obj(value: object) -> object:
    """응답 JSON 안의 문자열을 모두 가린다 (samples 저장용)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    return value


def run_case(
    case: Case, post: Post, *, n: int | None = None, crash_log: Path | None = None
) -> dict[str, dict]:
    """케이스를 n 회 실행하고 한 응답을 모든 축으로 판정한다.

    계약 실패 회차는 다른 축 분모에서 빠진다. 하네스 예외는 크래시(`errors`)로 세고 로그에 남긴다.

    - 계약 키: 모든 회차가 분모다. 5xx 는 판정된 실패이고, post · 계약 판정의 예외만 `errors` 다.
    - 다른 축: 계약을 통과한 회차만 분모(`n`)에 들어간다. 하네스 예외 회차도 분모에서만 뺀다 —
      축마다 `errors` 로 세면 `_crashes` 합이 축 수만큼 부풀려진다(게이트 2-e).
      판정 함수 자신의 예외는 그 축의 `errors` 다(그 축이 자기 이름으로 드러나게).
    - 관측(ambiguous) 축은 통과 회차의 응답도 `samples` 로 남긴다 — 사람이 읽고 판단하는 축이다.
    """
    rounds = n if n is not None else case.n
    contract_key = f"{case.id}/{CONTRACT_AXIS_ID}"
    contract = _empty_result("critical", None)
    axis_results = {axis.id: _empty_result(axis.grade, axis.pass_ratio) for axis in case.axes}
    seen: set[str] = set()  # 크래시 로그 중복 제거는 run_case 호출 단위다 (게이트 2-f)

    def _crash(exc: BaseException) -> None:
        if crash_log is not None:
            record_crash(crash_log, case.id, exc, seen)

    for _ in range(rounds):
        contract["n"] += 1
        try:
            observation = post(case.path, case.request)
            ok, note = case.contract(observation.status_code, observation.payload)
        except Exception as exc:  # noqa: BLE001 - 하네스 예외는 크래시로 세고 측정은 이어간다
            contract["errors"] += 1
            _add_note(contract, _crash_note(exc))
            _crash(exc)
            continue
        if not ok:
            _add_note(contract, note)
            continue  # 계약 실패 회차는 다른 축 분모에서 뺀다 (결정 A)
        contract["passed"] += 1

        response = observation.payload  # 계약을 통과했으니 응답 JSON(dict)이다
        for axis in case.axes:
            result = axis_results[axis.id]
            result["n"] += 1
            try:
                axis_ok, axis_note = axis.check(response, case.request)
            except Exception as exc:  # noqa: BLE001 - 판정 함수 버그도 그 축의 크래시로 센다
                result["errors"] += 1
                _add_note(result, _crash_note(exc))
                _crash(exc)
                continue
            result["passed"] += bool(axis_ok)
            if not axis_ok:
                _add_note(result, axis_note)
            if axis.grade == "ambiguous":
                result["samples"].append(
                    {"passed": bool(axis_ok), "note": redact(axis_note), "response": _redact_obj(response)}
                )

    results = {contract_key: contract}
    results.update({f"{case.id}/{axis_id}": result for axis_id, result in axis_results.items()})
    return results


def run(
    cases: Sequence[Case],
    post: Post,
    *,
    n: int | None = None,
    only: set[str] | None = None,
    crash_log: Path | None = None,
) -> dict[str, dict]:
    """케이스 전체(또는 `only` 에 든 것만)를 실행해 결과를 합친다.

    `only` 는 반복 수정 중 빠른 확인용 부분 실행이다. 골든셋의 값은 전체를 한 번에 재는 데 있으므로
    부분 실행 결과는 baseline 이 될 수 없다(`cli_main` 이 `--save` 와 함께 쓰지 못하게 막는다).
    """
    results: dict[str, dict] = {}
    for case in cases:
        if only is not None and case.id not in only:
            continue
        case_results = run_case(case, post, n=n, crash_log=crash_log)
        for key, result in case_results.items():
            _print_row(key, result)
        results.update(case_results)
    return results


def record_crash(log_path: Path, case_id: str, exc: BaseException, seen: set[str]) -> None:
    """크래시 traceback 을 로그에 남긴다. 같은 예외는 한 번만, URL · 토큰은 가린다.

    notes 에는 `ERR:{타입}:{메시지 60자}` 만 남으므로, 어느 프레임에서 터졌는지는 이 로그로 본다.
    같은 (예외 타입, 메시지) 조합은 한 번만 적어 로그가 부풀지 않게 한다.
    로그 파일은 루트 `.gitignore` 의 `*.log` 가 막는다.
    """
    key = f"{type(exc).__name__}:{exc}"
    if key in seen:
        return
    seen.add(key)
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{'=' * 72}\n[case] {case_id}\n[error] {redact(key)}\n{'-' * 72}\n")
            handle.write(redact(text))
    except OSError:  # 로그 실패가 측정을 막지 않게 한다
        pass


# ─────────────────────────── 집계 · 게이트 · 비교 ───────────────────────────


def judged_n(result: dict) -> int:
    """판정한 회차 수 — 크래시 회차를 분모에서 뺀다.

    예외로 죽은 회차는 그 축의 동작을 재지 못한 회차다. 분모에 남겨두면 판정 실패와 구분되지 않는다.
    """
    return max(0, result["n"] - result.get("errors", 0))


def _ratio_of(result: dict, default_pass_ratio: float) -> float:
    own = result.get("pass_ratio")
    return own if own is not None else default_pass_ratio


def _print_row(key: str, result: dict) -> None:
    judged = judged_n(result)
    mark = "OK " if judged and result["passed"] == judged else ("~~ " if result["passed"] else "XX ")
    tag = {"critical": " [critical]", "ambiguous": " [관측]"}.get(result["grade"], "")
    err = f" (크래시 {result['errors']}회 제외)" if result.get("errors") else ""
    note = f"   {result['notes'][0]}" if result["notes"] else ""
    print(f"  {mark}{key:40} {result['passed']}/{judged}{err}{tag}{note}")


def _total_errors(results: dict[str, dict]) -> int:
    return sum(result.get("errors", 0) for result in results.values())


def gate(
    results: dict[str, dict], *, default_pass_ratio: float = DEFAULT_PASS_RATIO, max_errors: int = 0
) -> tuple[bool, list[str]]:
    """등급별 기준으로 게이트를 판정한다. (PASS?, 실패 사유 줄 목록).

    크래시는 축 분모에서 빠지고 `_crashes` 라는 별도 항목으로 게이트를 실패시킨다.
    숨기는 것이 아니라 엉뚱한 축이 아니라 **자기 이름으로** 드러나게 하는 것이다
    (1단계 실측: 원인 미규명 예외가 실행마다 무작위 축에 꽂혀 매번 다른 축이 회귀한 것처럼 보였다).
    `--max-errors` 로 알려진 크래시를 명시적으로 인정할 수 있고, 기본값은 0(무관용)이다.
    """
    fails: list[str] = []
    for key, result in results.items():
        if result["grade"] == "ambiguous":
            continue
        judged = judged_n(result)
        if judged == 0:
            fails.append(f"{key}: 판정한 회차가 없어 판정 불가 (n={result['n']}, 크래시 {result.get('errors', 0)}회)")
            continue
        if result["grade"] == "critical":
            if result["passed"] < judged:
                fails.append(f"{key}: critical {result['passed']}/{judged}")
            continue
        ratio = _ratio_of(result, default_pass_ratio)
        if result["passed"] < judged * ratio - _RATIO_EPS:
            fails.append(f"{key}: {result['passed']}/{judged} < {ratio:.0%}")

    total_errors = _total_errors(results)
    if total_errors > max_errors:
        hit = sorted(key for key, result in results.items() if result.get("errors"))
        fails.append(f"_crashes: 실행 중 크래시 {total_errors}회 (허용 {max_errors}회) — 걸린 키: {', '.join(hit)}")
    return (not fails), fails


def compare(
    current: dict[str, dict], baseline_doc: dict, *, rule_version: str, prompt_hash: str
) -> list[str]:
    """baseline 과 통과율(횟수 아님)로 비교해 보고 줄 목록을 돌려준다.

    1단계에서 통과 횟수를 그대로 비교했더니, n=3 baseline 과 n=5 실행에서 그대로인 축이
    전부 "↑ 3 -> 5" 로 찍혀 개선으로 오독됐다. n 은 케이스별이라 키마다 다를 수 있으므로,
    n 이 다른 키에는 그 키에 경고를 붙이고 통과율로 비교한다.
    규칙 버전 · 프롬프트 해시가 다르면 다른 조건으로 잰 baseline 이라 맨 앞에 경고한다.
    baseline 에만 있는 키는 "제거됨" 으로 끝에 적는다.
    """
    lines: list[str] = []
    base_rule = baseline_doc.get("rule_version")
    if base_rule != rule_version:
        lines.append(f"⚠️ 규칙 버전이 다르다 (baseline {base_rule} vs 이번 {rule_version}) — 다른 규칙으로 잰 baseline 이다")
    base_hash = baseline_doc.get("prompt_hash")
    if base_hash != prompt_hash:
        lines.append(f"⚠️ 프롬프트 해시가 다르다 (baseline {base_hash} vs 이번 {prompt_hash}) — 프롬프트가 바뀐 뒤의 비교다")

    base = baseline_doc.get("results", {})
    for key, result in current.items():
        cur_judged = judged_n(result)
        previous = base.get(key)
        if previous is None:
            lines.append(f"+ {key}: (신규) {result['passed']}/{cur_judged}")
            continue
        base_judged = judged_n(previous)
        if previous["n"] != result["n"]:
            lines.append(f"⚠️ {key}: n 이 다르다 (baseline n={previous['n']} vs 이번 n={result['n']}) — 통과율로 비교한다")
        if cur_judged == 0 or base_judged == 0:
            if cur_judged != base_judged:
                lines.append(
                    f"? {key}: 판정 회차 0 이라 비교 불가 "
                    f"(baseline {previous['passed']}/{base_judged} vs 이번 {result['passed']}/{cur_judged})"
                )
            continue
        base_ratio = previous["passed"] / base_judged
        cur_ratio = result["passed"] / cur_judged
        if abs(base_ratio - cur_ratio) < _RATIO_EPS:
            continue
        arrow = "↑" if cur_ratio > base_ratio else "↓"
        lines.append(
            f"{arrow} {key}: {previous['passed']}/{base_judged} ({base_ratio:.0%}) "
            f"-> {result['passed']}/{cur_judged} ({cur_ratio:.0%})"
        )
    # baseline 에만 있는 키 — 케이스 · 축을 지우면 그 키의 하락이 비교에서 조용히 사라진다
    for key, previous in base.items():
        if key not in current:
            lines.append(f"- {key}: (제거됨) baseline {previous['passed']}/{judged_n(previous)} — 이번 결과에 없다")
    return lines


def make_baseline(results: dict[str, dict], *, rule_version: str, prompt_hash: str) -> dict:
    """저장할 baseline 문서를 만든다 — 규칙 버전 · 프롬프트 해시 · 키별 n · 결과.

    키별 n 은 각 결과 칸의 `n` 에 들어 있다. notes · samples 는 `run_case` 가 이미 가렸다.
    """
    return {
        "rule_version": rule_version,
        "prompt_hash": prompt_hash,
        "results": {key: dict(result) for key, result in results.items()},
    }


def prompt_hash(*texts: str) -> str:
    """프롬프트 텍스트들의 결정적 해시. 한 글자만 달라도 바뀐다.

    텍스트마다 길이를 앞에 붙여 경계가 바뀐 경우(`"ab", "c"` vs `"a", "bc"`)도 다른 해시가 되게 한다.
    """
    digest = hashlib.sha256()
    for text in texts:
        encoded = text.encode("utf-8")
        digest.update(f"{len(encoded)}:".encode())
        digest.update(encoded)
    return digest.hexdigest()


_URL_RE = re.compile(r"https?://[^\s\"'<>()]+")
# 스킴 없이 적힌 게이트웨이 경로 — 호스트는 `app.config` 의 게이트웨이 접두가 정본이다
_GATEWAY_HOST = _GATEWAY_PREFIX.split("://", 1)[-1]
_GATEWAY_PATH_RE = re.compile(re.escape(_GATEWAY_HOST) + r"[^\s\"'<>()]+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s\"'<>()]+")


def redact(text: str) -> str:
    """엔드포인트 URL · Bearer 토큰을 가린다. 일반 문장은 그대로 둔다.

    게이트웨이는 URL 자체가 모델을 고정하는 비밀이다(`app/config.py`) — URL 은 통째로 가린다.
    """
    text = _URL_RE.sub("<URL>", text)
    text = _GATEWAY_PATH_RE.sub(_GATEWAY_HOST + "<가림>", text)
    return _BEARER_RE.sub("Bearer <가림>", text)


# ─────────────────────────── CLI ───────────────────────────


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="eval — 실제 모델 N회 실행 → 통과율 → baseline 비교 → 게이트")
    ap.add_argument("--n", type=int, default=None, help="케이스당 반복 횟수(1 이상). 주면 케이스별 n 을 덮어쓴다")
    ap.add_argument(
        "--pass-ratio", type=float, default=DEFAULT_PASS_RATIO, help="자기 임계가 없는 non-critical 축의 합격 통과율"
    )
    ap.add_argument("--save", type=Path, help="결과를 baseline JSON 으로 저장")
    ap.add_argument("--baseline", type=Path, help="이 baseline 과 비교")
    ap.add_argument(
        "--max-errors",
        type=int,
        default=0,
        help="허용할 크래시 횟수. 기본 0(무관용). 알려진 크래시를 명시적으로 인정할 때만 올린다",
    )
    ap.add_argument("--only", help="쉼표로 구분한 case id 만 실행 (수정 중 빠른 확인용. baseline 저장 불가)")
    return ap


def _replay_first(post: Post, first: Observation | BaseException) -> Post:
    """첫 호출에는 probe 결과를 돌려주고, 그다음부터 실제 post 로 넘기는 Post.

    probe 를 첫 선택 케이스의 1회차로 재사용해 post 총수가 케이스 수 × n 이 되게 한다(게이트 2-a).
    probe 가 예외였으면 그 회차에서 다시 던져 크래시로 센다.
    """
    used = False

    def replay(path: str, request: dict) -> Observation:
        nonlocal used
        if not used:
            used = True
            if isinstance(first, BaseException):
                raise first
            return first
        return post(path, request)

    return replay


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
    """CLI 진입점. 게이트 PASS 0 / FAIL 1 / 오용 · 케이스 0개 2 / 키 없음 · 미연결 SKIP 0.

    옵션: `--n` · `--pass-ratio` · `--save` · `--baseline` · `--max-errors` · `--only`.
    순서: 인자 해석(`--help` 는 여기서 0) → 케이스 0개 검사 → 오용 검사 → 키 확인 → 미연결 probe → 실행 → 비교 → 게이트 → 저장.
    케이스 0개는 키 확인 · post 생성 전에 2 로 끝낸다 — 그대로 돌리면 0/0 으로 게이트 PASS 가 찍히고
    `--save` 가 빈 baseline 을 만든다. 키 확인 뒤에 막으면 키 없는 PC 에서 SKIP(0) 으로 묻힌다.
    `--only` 검사보다 먼저라 0개 문제가 "알 수 없는 case id" 로 보이지 않는다.
    """
    args = _parser().parse_args(argv)
    if not cases:
        print("측정할 케이스가 없다 — 0/0 으로 게이트 PASS 를 찍거나 빈 baseline 을 저장하지 않는다. 실행하지 않는다.")
        return 2
    if args.n is not None and args.n < 1:
        print(f"--n 은 1 이상이어야 한다 (받은 값 {args.n}).")
        return 2

    only = {case_id.strip() for case_id in args.only.split(",") if case_id.strip()} if args.only else None
    if only:
        unknown = only - {case.id for case in cases}
        if unknown:
            print(f"알 수 없는 case id: {sorted(unknown)}")
            return 2
        if args.save:
            print("--only 는 부분 실행이라 baseline 이 될 수 없다. --save 없이 돌리고, 전체 실행으로 저장한다.")
            return 2

    if not has_key():
        print("SKIP: 모델 설정(키 · 엔드포인트 URL) 없음 — eval 은 실제 모델이 필요하다.")
        print("      키 없는 결정적 검증은 ai/tests/test_eval_*.py 가 담당한다.")
        return 0

    post = post_factory()
    selected = [case for case in cases if only is None or case.id in only]

    # 미연결 probe — 첫 선택 케이스의 1회차. 모델이 없는데 케이스 수 × n 번 501 을 받을 이유가 없다.
    if selected:
        first = selected[0]
        try:
            probe: Observation | BaseException = post(first.path, first.request)
        except Exception as exc:  # noqa: BLE001 - probe 예외는 그 회차의 크래시로 센다 (게이트 2-a)
            probe = exc
        if isinstance(probe, Observation) and probe.status_code in _UNCONNECTED_STATUSES:
            print(f"SKIP: 미연결 — POST {first.path} → {probe.status_code}. 모델이 연결되지 않아 측정하지 않는다.")
            if probe.status_code == 404:
                print("      404 는 경로 오타일 수도 있다 — 케이스의 path 를 확인한다.")
            return 0
        post = _replay_first(post, probe)

    scope = f" | 부분실행={sorted(only)}" if only else ""
    n_label = args.n if args.n is not None else "케이스별"
    print(f"채널=TestClient(app.main) | 규칙={rule_version} | N={n_label}{scope}\n")
    # 이미 고른 목록을 넘긴다 — probe 가 `selected[0]` 의 1회차라는 것이 실행 순서로 보장된다
    results = run(selected, post, n=args.n, crash_log=crash_log)

    total = sum(result["passed"] for result in results.values())
    denom = sum(judged_n(result) for result in results.values())
    total_errors = _total_errors(results)
    print(f"\n총 통과: {total}/{denom}" + (f"  (크래시 {total_errors}회는 분모에서 제외)" if total_errors else ""))

    if args.baseline:
        print("\n=== baseline 대비 변화 ===")
        if args.baseline.exists():
            baseline_doc = json.loads(args.baseline.read_text(encoding="utf-8"))
            lines = compare(results, baseline_doc, rule_version=rule_version, prompt_hash=prompt_hash)
            for line in lines or ["(변화 없음)"]:
                print(f"  {line}")
        else:
            print(f"  baseline 파일이 없다: {args.baseline}")

    ok, fails = gate(results, default_pass_ratio=args.pass_ratio, max_errors=args.max_errors)
    print("\n=== 게이트 ===")
    if ok:
        print(
            "  PASS — 모든 critical 만점 + non-critical 임계 충족"
            + (f" (크래시 {total_errors}회 인정됨)" if total_errors else "")
        )
    else:
        print("  FAIL")
        for line in fails:
            print(f"    - {line}")

    if args.save:
        doc = make_baseline(results, rule_version=rule_version, prompt_hash=prompt_hash)
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nbaseline 저장: {args.save}")

    return 0 if ok else 1
