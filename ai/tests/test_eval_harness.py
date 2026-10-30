"""`evals/harness.py` 단위 테스트 — 하네스만 본다. 모델 · 네트워크 · 키 없음.

판정 함수(`evals.checks`)는 쓰지 않고 테스트 안의 Fake Check 로 축을 만든다.
테스트 이름의 `r<번호>` 는 계획 §6-2 명세표 행 번호다.
"""

from __future__ import annotations

import json

import pytest

from evals.harness import (
    Axis,
    Case,
    Observation,
    cli_main,
    compare,
    gate,
    judged_n,
    make_baseline,
    prompt_hash,
    redact,
    run,
    run_case,
)

_UUID = "3f2b9c4e-8a1d-4e6f-9b2a-7c5d1e0f4a3b"
_TOKEN = "sk-test-9f8e7d6c"
_URL = f"https://mlapi.run/{_UUID}/v1/chat/completions"


# ─────────────────────────── Fake · 빌더 ───────────────────────────


class FakePost:
    """`Post` 대역 — 호출을 기록하고, 정해 둔 결과를 차례로 돌려준다(마지막 것을 반복).

    결과가 예외면 매번 같은 타입 · 메시지의 새 예외를 던진다.
    """

    def __init__(self, *outcomes: Observation | BaseException) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, request: dict) -> Observation:
        self.calls.append((path, request))
        outcome = self._outcomes[min(len(self.calls), len(self._outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise type(outcome)(*outcome.args)
        return outcome


class FakePostFactory:
    """`post_factory` 대역 — 몇 번 불렸는지 센다."""

    def __init__(self, post: FakePost) -> None:
        self.post = post
        self.calls = 0

    def __call__(self) -> FakePost:
        self.calls += 1
        return self.post


class FakeCheck:
    """축 판정 대역 — 정해 둔 Verdict 를 돌려주고 호출 횟수를 센다."""

    def __init__(self, ok: bool = True, note: str = "") -> None:
        self.verdict = (ok, note)
        self.calls = 0

    def __call__(self, resp: dict, req: dict) -> tuple[bool, str]:
        self.calls += 1
        return self.verdict


def _contract(status_code: int, payload: object) -> tuple[bool, str]:
    """계약 판정 대역 — 200 + dict 면 통과."""
    if status_code == 200 and isinstance(payload, dict):
        return True, ""
    return False, f"status={status_code}"


def _payload(body: str = "두부조림 덕분에 단백질이 채워졌어요.") -> dict:
    return {
        "body": body,
        "reasoning": "유지기라 단백질을 꾸준히 채우는 게 중요한 시기예요.",
        "suggestions": [],
        "safetyStatus": "SAFE",
        "modelVersion": "fake-model",
    }


def _ok(body: str = "두부조림 덕분에 단백질이 채워졌어요.") -> Observation:
    return Observation(status_code=200, payload=_payload(body))


def _axis(axis_id: str = "a1", *, ok: bool = True, grade: str = "critical", pass_ratio=None) -> Axis:
    return Axis(id=axis_id, check=FakeCheck(ok), grade=grade, pass_ratio=pass_ratio)


def _case(case_id: str = "c1", *, axes: tuple[Axis, ...] | None = None, n: int = 3) -> Case:
    return Case(
        id=case_id,
        path="/short-feedback",
        request={"scope": "MEAL", "mealId": case_id},
        contract=_contract,
        axes=axes if axes is not None else (_axis("a1"),),
        n=n,
    )


def _r(passed: int, n: int, *, errors: int = 0, grade: str = "critical", pass_ratio=None) -> dict:
    """결과 dict 한 칸."""
    return {
        "passed": passed,
        "n": n,
        "errors": errors,
        "grade": grade,
        "pass_ratio": pass_ratio,
        "notes": [],
        "samples": [],
    }


def _cli(argv, *, cases, post: FakePost, has_key: bool = True, tmp_path=None):
    """`cli_main` 을 Fake 로 부른다. (exit code, factory) 를 돌려준다."""
    factory = FakePostFactory(post)
    code = cli_main(
        argv,
        cases=cases,
        post_factory=factory,
        has_key=lambda: has_key,
        rule_version="v2",
        prompt_hash="hash-abc",
        crash_log=(tmp_path / "crash.log") if tmp_path is not None else None,
    )
    return code, factory


# ─────────────────────────── 집계 (judged_n) ───────────────────────────


def test_r62_judged_n_excludes_crashed_rounds():
    """행 62 — 크래시 회차는 분모에서 뺀다. 크래시를 판정 실패로 세면 엉뚱한 축이 회귀한 것처럼 보인다."""
    assert judged_n(_r(passed=3, n=5, errors=2)) == 3


# ─────────────────────────── 게이트 ───────────────────────────


def test_r63_gate_fails_when_critical_fails_even_once():
    """행 63 — critical 이 한 번이라도 실패하면 FAIL 이고, 실패 사유에 그 키가 나온다."""
    ok, fails = gate({"c1/no_medical": _r(passed=4, n=5)})
    assert ok is False
    assert any("c1/no_medical" in line for line in fails)


def test_r64_gate_passes_when_all_criteria_met():
    """행 64 — critical 만점 + non-critical 이 기본 임계(0.66) 이상이면 PASS, 실패 사유 없음."""
    results = {
        "c1/critical_axis": _r(passed=5, n=5),
        "c1/style_axis": _r(passed=2, n=3, grade="non_critical"),
    }
    assert gate(results) == (True, [])


def test_r65_gate_fails_when_non_critical_below_threshold():
    """행 65 — non-critical 이 임계 미만(1/3 < 0.66)이면 FAIL."""
    ok, _ = gate({"c1/style_axis": _r(passed=1, n=3, grade="non_critical")})
    assert ok is False


@pytest.mark.parametrize(("passed", "expected"), [(9, True), (8, False)], ids=["9of10-pass", "8of10-fail"])
def test_r66_axis_own_pass_ratio_overrides_default(passed, expected):
    """행 66 — 축의 자기 임계(0.9)가 기본값(0.66)보다 우선한다. 8/10 은 기본값이면 통과했을 값이다."""
    results = {"c1/no_imperative": _r(passed=passed, n=10, grade="non_critical", pass_ratio=0.9)}
    ok, _ = gate(results, default_pass_ratio=0.66)
    assert ok is expected


def test_r67_ambiguous_axis_excluded_from_gate():
    """행 67 — 관측(ambiguous) 축은 0/3 이어도 게이트에서 빠진다. 사람이 읽고 판단하는 축이다."""
    ok, fails = gate({"c1/cause_food_named": _r(passed=0, n=3, grade="ambiguous")})
    assert ok is True
    assert fails == []


def test_r68_all_rounds_crashed_is_undecidable_fail():
    """행 68 — 전 회차가 크래시면 판정 불가로 FAIL. 분모 0 을 '실패 0건'으로 읽으면 안 된다."""
    ok, fails = gate({"c1/a1": _r(passed=0, n=3, errors=3)}, max_errors=99)
    assert ok is False
    assert any("c1/a1" in line and "판정 불가" in line for line in fails)


@pytest.mark.parametrize(("max_errors", "expected"), [(0, True), (1, False)], ids=["max0-fail", "max1-allowed"])
def test_r69_crash_total_over_max_errors_fails_as_crashes(max_errors, expected):
    """행 69 — 크래시 합이 `max_errors` 를 넘으면 엉뚱한 축이 아니라 `_crashes` 라는 자기 이름으로 실패한다."""
    results = {"c1/a1": _r(passed=2, n=3, errors=1)}
    _, fails = gate(results, max_errors=max_errors)
    assert any("_crashes" in line for line in fails) is expected


# ─────────────────────────── baseline 비교 ───────────────────────────


def _baseline(results: dict[str, dict], *, rule_version: str = "v2", ph: str = "hash-abc") -> dict:
    return make_baseline(results, rule_version=rule_version, prompt_hash=ph)


def test_r70_compare_does_not_read_more_passes_as_improvement():
    """행 70 — base 3/3 vs 이번 5/5 는 통과율이 같다. 횟수 증가를 개선(↑)으로 읽지 않고 n 차이 경고만 낸다.
    경고 줄에는 두 n 값(3 · 5)이 함께 나와야 어느 쪽이 몇 회였는지 읽을 수 있다."""
    base = _baseline({"c1/a1": _r(passed=3, n=3)})
    lines = compare({"c1/a1": _r(passed=5, n=5)}, base, rule_version="v2", prompt_hash="hash-abc")
    assert not any("↑" in line or "↓" in line for line in lines)
    assert any("3" in line and "5" in line for line in lines)


def test_r71_compare_reports_one_drop_line_when_ratio_falls():
    """행 71 — 통과율이 떨어지면(3/3 → 2/3) 그 키에 하락(↓) 줄이 정확히 하나 나온다."""
    base = _baseline({"c1/a1": _r(passed=3, n=3)})
    lines = compare({"c1/a1": _r(passed=2, n=3)}, base, rule_version="v2", prompt_hash="hash-abc")
    drops = [line for line in lines if "↓" in line and "c1/a1" in line]
    assert len(drops) == 1
    assert not any("↑" in line for line in lines)


def test_r72_compare_warns_on_key_whose_n_differs():
    """행 72 — 키별 n 이 다르면 그 키에만 경고가 붙는다. n 이 같고 변화 없는 키는 언급하지 않는다."""
    base = _baseline({"c1/a1": _r(passed=3, n=3), "c1/a2": _r(passed=3, n=3)})
    current = {"c1/a1": _r(passed=5, n=5), "c1/a2": _r(passed=3, n=3)}
    lines = compare(current, base, rule_version="v2", prompt_hash="hash-abc")
    assert any("c1/a1" in line for line in lines)
    assert not any("c1/a2" in line for line in lines)


def test_r73_compare_marks_key_missing_from_baseline_as_new():
    """행 73 — baseline 에 없는 키는 '신규' 줄로 나온다."""
    base = _baseline({"c1/a1": _r(passed=3, n=3)})
    current = {"c1/a1": _r(passed=3, n=3), "c1/new_axis": _r(passed=2, n=3)}
    lines = compare(current, base, rule_version="v2", prompt_hash="hash-abc")
    assert any("c1/new_axis" in line and "신규" in line for line in lines)


def test_r73a_compare_marks_key_only_in_baseline_as_removed():
    """행 73a — baseline 에만 있고 이번 결과에 없는 키는 '제거됨' 줄로 나온다 (게이트 5-2).

    케이스 · 축을 지우면 그 키의 하락이 비교에서 조용히 사라진다. 삭제를 드러내야 회귀가 가려지지 않는다.
    """
    base = _baseline({"c1/a1": _r(passed=3, n=3), "c1/a2": _r(passed=3, n=3)})
    current = {"c1/a1": _r(passed=3, n=3)}
    lines = compare(current, base, rule_version="v2", prompt_hash="hash-abc")
    assert any("c1/a2" in line and "제거됨" in line for line in lines)


@pytest.mark.parametrize(
    "changed",
    [{"rule_version": "v3", "prompt_hash": "hash-abc"}, {"rule_version": "v2", "prompt_hash": "hash-xyz"}],
    ids=["rule_version", "prompt_hash"],
)
def test_r74_compare_warns_when_rule_version_or_prompt_hash_differs(changed):
    """행 74 — 규칙 버전 · 프롬프트 해시가 baseline 과 다르면 경고 줄이 생긴다. 다른 조건의 baseline 과 비교 중임을 알린다."""
    results = {"c1/a1": _r(passed=3, n=3)}
    base = _baseline(results, rule_version="v2", ph="hash-abc")
    same = compare(results, base, rule_version="v2", prompt_hash="hash-abc")
    diff = compare(results, base, **changed)
    assert set(diff) - set(same)


# ─────────────────────────── 해시 · baseline · 가림 ───────────────────────────


def test_r75_prompt_hash_is_deterministic_and_sensitive():
    """행 75 — 프롬프트 해시는 같은 텍스트면 같고, 한 글자만 달라도 바뀐다."""
    assert prompt_hash("시스템 프롬프트", "끼니 프롬프트") == prompt_hash("시스템 프롬프트", "끼니 프롬프트")
    assert prompt_hash("나트륨 한도 766.7mg") != prompt_hash("나트륨 한도 766.8mg")


def test_r76_baseline_records_rule_version_hash_and_results():
    """행 76 — baseline 에 규칙 버전 · 프롬프트 해시 · 키별 n · 결과가 담기고 JSON 으로 저장할 수 있다."""
    results = {"c1/a1": _r(passed=2, n=3), "c1/a2": _r(passed=5, n=5)}
    doc = make_baseline(results, rule_version="v2", prompt_hash="hash-abc")
    assert doc["rule_version"] == "v2"
    assert doc["prompt_hash"] == "hash-abc"
    assert doc["results"]["c1/a1"]["n"] == 3
    assert doc["results"]["c1/a2"]["n"] == 5
    assert doc["results"]["c1/a1"]["passed"] == 2
    json.dumps(doc, ensure_ascii=False)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"POST {_URL} 실패", _UUID),
        (f"Authorization: Bearer {_TOKEN}", _TOKEN),
    ],
    ids=["endpoint-url", "bearer-token"],
)
def test_r77_redact_hides_endpoint_url_and_bearer_token(text, secret):
    """행 77 — 엔드포인트 URL 의 uuid 와 Bearer 토큰 문자열을 가린다."""
    assert secret not in redact(text)


def test_r78_redact_keeps_plain_sentence():
    """행 78 — 일반 피드백 문장은 숫자 · 단위가 있어도 그대로 둔다."""
    text = "나트륨 950mg 은 끼니 한도 766.7mg 를 넘었어요. 국물을 조금 남기면 좋아요."
    assert redact(text) == text


# ─────────────────────────── 실행 (run_case · run) ───────────────────────────


def test_r79_run_case_repeats_n_times_and_every_axis_judges_each_response():
    """행 79 — 케이스 n(3)회 post 하고, 한 응답을 모든 축이 판정한다. 모든 키의 n 이 3 이다."""
    a1, a2 = FakeCheck(), FakeCheck()
    case = _case(axes=(Axis("a1", a1, "critical"), Axis("a2", a2, "non_critical")), n=3)
    post = FakePost(_ok())
    results = run_case(case, post)
    assert len(post.calls) == 3
    assert a1.calls == 3 and a2.calls == 3
    assert set(results) == {"c1/contract_parseable", "c1/a1", "c1/a2"}
    assert all(r["n"] == 3 for r in results.values())


def test_r80_5xx_is_contract_failure_not_crash():
    """행 80 — 5xx 응답은 하네스 크래시가 아니라 계약 실패다 (passed=0, errors=0).
    계약 키 자신의 분모에서는 빼지 않는다 — 3회 모두 판정된 실패로 센다."""
    results = run_case(_case(n=3), FakePost(Observation(status_code=500, payload=None)))
    contract = results["c1/contract_parseable"]
    assert contract["passed"] == 0
    assert contract["errors"] == 0
    assert judged_n(contract) == 3


def test_r81_contract_failed_round_excluded_from_other_axes():
    """행 81 — 계약 실패 회차는 다른 축 분모에서 빠진다(결정 A). 판정하지 않고, 실패로도 크래시로도 세지 않는다."""
    check = FakeCheck()
    case = _case(axes=(Axis("a1", check, "critical"),), n=3)
    results = run_case(case, FakePost(Observation(status_code=500, payload=None)))
    other = results["c1/a1"]
    assert check.calls == 0
    assert judged_n(other) == 0
    assert other["errors"] == 0
    assert other["passed"] == 0


def test_r82_harness_exception_counted_as_crash_and_logged_once(tmp_path):
    """행 82 — 하네스 예외는 크래시(errors)로 세고, 같은 예외는 로그에 traceback 한 번만 남긴다.
    크래시는 계약 키에만 errors 로 센다. 다른 축은 그 회차를 분모에서만 뺀다 — 축마다 세면 `_crashes` 합이 부풀려진다."""
    log = tmp_path / "crash.log"
    post = FakePost(RuntimeError("게이트웨이 연결 끊김"))
    case = _case(axes=(Axis("a1", FakeCheck(), "critical"),), n=2)
    results = run_case(case, post, crash_log=log)
    assert results["c1/contract_parseable"]["errors"] == 2
    other = results["c1/a1"]
    assert other["errors"] == 0
    assert judged_n(other) == 0
    text = log.read_text(encoding="utf-8")
    assert "RuntimeError" in text
    assert text.count("Traceback (most recent call last)") == 1


def test_r83_no_url_or_token_in_crash_log_notes_or_samples(tmp_path):
    """행 83 — 크래시 로그 · notes · samples 어디에도 엔드포인트 uuid · Bearer 토큰이 남지 않는다."""
    log = tmp_path / "crash.log"
    leaky = f"{_URL} 호출 결과 (Authorization: Bearer {_TOKEN})"
    post = FakePost(RuntimeError(f"POST {_URL} 실패 — Bearer {_TOKEN}"), _ok(leaky))

    def failing_with_body(resp: dict, req: dict) -> tuple[bool, str]:
        return False, f"응답 본문: {resp['body']}"

    case = _case(
        axes=(
            Axis("noted", failing_with_body, "non_critical"),
            Axis("observed", FakeCheck(True), "ambiguous"),
        ),
        n=2,
    )
    results = run_case(case, post, crash_log=log)
    dumped = json.dumps(results, ensure_ascii=False)
    logged = log.read_text(encoding="utf-8")
    for secret in (_UUID, _TOKEN):
        assert secret not in dumped
        assert secret not in logged


def test_r83a_failed_axis_keeps_its_note():
    """행 83a — 실패한 축은 판정 함수가 준 note 를 notes 에 남긴다.
    notes 를 아예 비우는 구현이 행 83(가림 검사)을 공짜로 통과하지 못하게 한다."""
    note = "나트륨 2000mg 은 입력에 없는 숫자"
    case = _case(axes=(Axis("noted", FakeCheck(False, note), "non_critical"),), n=2)
    results = run_case(case, FakePost(_ok()))
    notes = results["c1/noted"]["notes"]
    assert notes
    assert note in json.dumps(notes, ensure_ascii=False)


def test_r84_ambiguous_axis_keeps_passing_responses_as_samples():
    """행 84 — 관측 축은 통과 회차의 응답도 samples 로 남긴다. 사람이 읽고 판단하는 축이라 note 만으론 볼 것이 없다."""
    marker = "관측용 응답 본문 7f3a"
    case = _case(axes=(Axis("observed", FakeCheck(True), "ambiguous"),), n=2)
    results = run_case(case, FakePost(_ok(marker)))
    samples = results["c1/observed"]["samples"]
    assert marker in json.dumps(samples, ensure_ascii=False)


def test_r85_run_skips_cases_not_in_only():
    """행 85 — `only` 에 없는 케이스는 post 하지 않고 결과 키도 없다."""
    post = FakePost(_ok())
    results = run([_case("c1", n=2), _case("c2", n=2)], post, only={"c1"})
    assert not any(request["mealId"] == "c2" for _, request in post.calls)
    assert not any(key.startswith("c2/") for key in results)
    assert any(key.startswith("c1/") for key in results)


# ─────────────────────────── CLI ───────────────────────────


def test_r86_unknown_only_id_is_misuse(tmp_path):
    """행 86 — 알 수 없는 `--only` id 는 오용(2)이고 post 를 만들지도 않는다."""
    post = FakePost(_ok())
    code, factory = _cli(["--only", "nope"], cases=[_case("c1")], post=post, tmp_path=tmp_path)
    assert code == 2
    assert factory.calls == 0
    assert post.calls == []


@pytest.mark.parametrize("n", ["0", "-1"], ids=["zero", "negative"])
def test_r86a_n_below_one_is_misuse(n, tmp_path):
    """행 86a — `--n` 이 1 미만이면 오용(2)이고 post 를 만들지도 않는다 (게이트 5-3).

    n=0 이면 판정할 회차가 없는데도 미연결 probe 1회가 실제 모델을 불러 크레딧만 쓴다.
    """
    post = FakePost(_ok())
    code, factory = _cli(["--n", n], cases=[_case("c1")], post=post, tmp_path=tmp_path)
    assert code == 2
    assert factory.calls == 0
    assert post.calls == []


def test_r87_partial_run_cannot_be_saved_as_baseline(tmp_path):
    """행 87 — `--only` 부분 실행은 baseline 이 될 수 없다. 오용(2)이고 파일을 만들지 않는다."""
    saved = tmp_path / "baseline.json"
    code, _ = _cli(
        ["--only", "c1", "--save", str(saved)], cases=[_case("c1"), _case("c2")], post=FakePost(_ok()), tmp_path=tmp_path
    )
    assert code == 2
    assert not saved.exists()


def test_r88_no_api_key_skips_and_does_nothing(tmp_path, capsys):
    """행 88 — 키가 없으면 SKIP 을 출력하고 0 으로 끝난다. post · 파일 저장을 하지 않는다."""
    saved = tmp_path / "baseline.json"
    post = FakePost(_ok())
    code, factory = _cli(["--save", str(saved)], cases=[_case("c1")], post=post, has_key=False, tmp_path=tmp_path)
    out = capsys.readouterr().out
    assert code == 0
    assert "SKIP" in out
    assert "미연결" not in out  # 미연결 SKIP 과 다른 문구
    assert factory.calls == 0
    assert post.calls == []
    assert not saved.exists()


def test_r89_misuse_checked_before_api_key(tmp_path):
    """행 89 — 오용 검사가 키 확인보다 먼저다. 키가 없어도 잘못된 `--only` 는 2 다."""
    code, _ = _cli(["--only", "nope"], cases=[_case("c1")], post=FakePost(_ok()), has_key=False, tmp_path=tmp_path)
    assert code == 2


def test_r90_gate_pass_returns_0_and_saves_baseline(tmp_path):
    """행 90 — 게이트 PASS 면 0 이고, `--save` 파일 JSON 에 rule_version · prompt_hash 가 담긴다."""
    saved = tmp_path / "baseline.json"
    code, _ = _cli(["--save", str(saved)], cases=[_case("c1"), _case("c2")], post=FakePost(_ok()), tmp_path=tmp_path)
    assert code == 0
    doc = json.loads(saved.read_text(encoding="utf-8"))
    assert doc["rule_version"] == "v2"
    assert doc["prompt_hash"] == "hash-abc"


def test_r91_gate_fail_returns_1(tmp_path):
    """행 91 — critical 축이 실패하면 게이트 FAIL 이고 1 로 끝난다."""
    case = _case("c1", axes=(_axis("critical_axis", ok=False, grade="critical"),))
    code, _ = _cli([], cases=[case], post=FakePost(_ok()), tmp_path=tmp_path)
    assert code == 1


def test_r92_cli_n_overrides_case_n(tmp_path):
    """행 92 — `--n 2` 가 케이스별 n(5)을 덮어쓴다(결정 G). post 수 = 케이스 수 × 2."""
    post = FakePost(_ok())
    _cli(["--n", "2"], cases=[_case("c1", n=5), _case("c2", n=5)], post=post, tmp_path=tmp_path)
    assert len(post.calls) == 2 * 2


@pytest.mark.parametrize("status", [501, 404], ids=["501", "404"])
def test_r93_unconnected_endpoint_skips_after_one_probe(status, tmp_path, capsys):
    """행 93 — 모델이 미연결(501 · 404)이면 'SKIP: 미연결' 을 출력하고 0 으로 멈춘다(결정 B).
    probe 1회 뒤 더 부르지 않고 baseline 도 저장하지 않는다.
    404 는 경로 오타일 수도 있어, 출력에 요청 경로와 status 숫자를 함께 남긴다(게이트 2-d)."""
    saved = tmp_path / "baseline.json"
    post = FakePost(Observation(status_code=status, payload=None))
    code, _ = _cli(
        ["--save", str(saved)], cases=[_case("c1", n=3), _case("c2", n=3)], post=post, tmp_path=tmp_path
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "SKIP: 미연결" in out
    assert "/short-feedback" in out
    assert str(status) in out
    assert len(post.calls) == 1
    assert not saved.exists()


# ─────────────────────────── CLI — 케이스 0개 ───────────────────────────


def test_s13_no_cases_exits_2_with_reason_and_no_post(tmp_path, capsys):
    """행 s13 — 케이스가 0개면 키가 있어도 `cli_main` 은 사유를 출력하고 2(오용)로 끝난다. post 를 만들지 않는다.

    0개로 돌리면 `0/0` 으로 게이트 PASS 가 찍힌다 — 아무것도 재지 않았는데 통과로 보인다.
    문구는 단언하지 않는다(무엇이 문제인지 출력했는가만 본다).
    """
    post = FakePost(_ok())
    code, factory = _cli([], cases=[], post=post, tmp_path=tmp_path)
    out = capsys.readouterr().out
    assert code == 2
    assert out.strip() != ""
    assert factory.calls == 0
    assert post.calls == []


def test_s14_no_cases_checked_before_api_key(tmp_path, capsys):
    """행 s14 — 0개 검사가 키 확인보다 먼저다. 키가 없어도 0개면 SKIP(0) 이 아니라 2 다.

    키 확인 뒤에 막으면 키 없는 PC 에서 0개 문제가 SKIP 으로 묻힌다.
    """
    code, _ = _cli([], cases=[], post=FakePost(_ok()), has_key=False, tmp_path=tmp_path)
    out = capsys.readouterr().out
    assert code == 2
    assert "SKIP" not in out


def test_s15_no_cases_save_does_not_write_baseline(tmp_path):
    """행 s15 — 케이스가 0개면 `--save` 를 줘도 2 로 끝나고 baseline 파일을 만들지 않는다.

    빈 baseline 이 저장되면 다음 비교가 "비교할 케이스 없음" 으로 조용히 통과한다.
    """
    saved = tmp_path / "b.json"
    code, _ = _cli(["--save", str(saved)], cases=[], post=FakePost(_ok()), tmp_path=tmp_path)
    assert code == 2
    assert not saved.exists(), "케이스 0개인데 baseline 이 저장됐다"


def test_s16_no_cases_reported_before_unknown_only_id(tmp_path, capsys):
    """행 s16 — 0개 사유가 `--only` 의 모르는 id 검사보다 먼저다. `["--only", "nope"]` 와 `[]` 는 둘 다 2 이고 출력이 같다.

    `--only` 검사가 먼저면 0개 문제가 "알 수 없는 case id" 로 보여 원인을 잘못 찾게 된다.
    """
    code_only, _ = _cli(["--only", "nope"], cases=[], post=FakePost(_ok()), tmp_path=tmp_path)
    out_only = capsys.readouterr().out
    code_plain, _ = _cli([], cases=[], post=FakePost(_ok()), tmp_path=tmp_path)
    out_plain = capsys.readouterr().out
    assert code_only == 2
    assert code_plain == 2
    assert out_only == out_plain


def test_s17_no_cases_help_still_exits_0(tmp_path):
    """행 s17 — 케이스가 0개여도 `--help` 는 도움말을 내고 0 으로 끝난다. 키 확인 · post 생성은 0회다.

    0개 검사는 인자 해석 뒤에 한다 — 해석 전에 막으면 케이스가 비었을 때 사용법조차 볼 수 없다.
    """
    key_checks: list[None] = []

    def _key_check_forbidden() -> bool:
        key_checks.append(None)
        raise AssertionError("키 확인(has_key)까지 갔다 — --help 는 그 전에 끝나야 한다")

    factory = FakePostFactory(FakePost(_ok()))
    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            ["--help"],
            cases=[],
            post_factory=factory,
            has_key=_key_check_forbidden,
            rule_version="v2",
            prompt_hash="hash-abc",
            crash_log=tmp_path / "crash.log",
        )
    assert exc_info.value.code == 0
    assert key_checks == []
    assert factory.calls == 0
