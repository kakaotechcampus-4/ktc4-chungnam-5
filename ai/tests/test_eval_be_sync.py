"""AI 사본 상수(`app/short_feedback.py`)가 BE 정본과 같은지 — BE 파일을 **import 하지 않고** AST 로 읽는다.

ai 는 BE 에 의존하지 않는다(절대 규칙 1). 그래서 BE 소스를 텍스트로 읽어 최상위 대입문만 해석한다.
BE 가 기준을 바꾸고 AI 사본을 안 맞추면 점수와 문장이 조용히 어긋난다 — 그 drift 를 여기서 잡는다.
테스트 이름의 `r<번호>` 는 계획 §6-4 명세표 행 번호다.

BE 파일이 없으면 skip 이 아니라 실패한다 (결정 H). 판독기가 상수를 못 찾으면 예외를 낸다 — 조용히 통과하지 않는다.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from app.schemas import Stage
from app.short_feedback import BE_RULE_VERSION, MEALS_PER_DAY, _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY

_REPO = Path(__file__).resolve().parents[2]
_EVALUATION = _REPO / "backend" / "app" / "services" / "evaluation"
_INIT = _EVALUATION / "__init__.py"
_RULE_ENGINE = _EVALUATION / "rule_engine.py"
_STAGE_PROFILE = _EVALUATION / "stage_profile.py"


# ─────────────────────────── 판독기 ───────────────────────────


def _read(path: Path) -> str:
    """BE 소스를 읽는다. 파일이 없으면 FileNotFoundError 그대로 — skip 하지 않는다 (결정 H)."""
    return path.read_text(encoding="utf-8")


def _assignments(source: str) -> dict[str, ast.expr]:
    """최상위 `X = ...` · `X: T = ...` 의 이름 → 값 식."""
    found: dict[str, ast.expr] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            found[node.target.id] = node.value
    return found


_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


def _eval(node: ast.expr, names: dict[str, ast.expr], seen: frozenset[str] = frozenset()) -> object:
    """필요한 만큼만 해석한다 — 상수 · 같은 파일 이름 · `Decimal("…")` · 사칙연산 · 단항 음수.

    모르는 식은 ValueError 다. 해석 못 한 값을 None 등으로 넘기면 비교가 조용히 엉뚱해진다.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in seen or node.id not in names:
            raise ValueError(f"이름 {node.id!r} 를 이 파일에서 해석할 수 없다")
        return _eval(names[node.id], names, seen | {node.id})
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Decimal"
        and len(node.args) == 1
        and not node.keywords
    ):
        return Decimal(_eval(node.args[0], names, seen))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left, names, seen), _eval(node.right, names, seen))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, names, seen)
    raise ValueError(f"해석하지 않는 식: {ast.dump(node)}")


def _const(source: str, name: str) -> object:
    """최상위 상수 `name` 의 값. 없으면 LookupError."""
    names = _assignments(source)
    if name not in names:
        raise LookupError(f"상수 {name!r} 가 없다")
    return _eval(names[name], names)


def _stage_table(source: str) -> tuple[dict[str, ast.Call], dict[str, ast.expr]]:
    """`STAGE_PROFILES` 의 단계 이름 → `DailyTargets(단백질, 식이섬유, 나트륨)` 호출식, 그리고 파일의 이름표.

    모양: `{MedicationStage.X: StageProfile(<band>, DailyTargets(p, fiber, sodium)), ...}`.
    모양이 다르면 ValueError — 못 읽은 단계를 빼고 넘어가지 않는다.
    """
    names = _assignments(source)
    if "STAGE_PROFILES" not in names:
        raise LookupError("상수 'STAGE_PROFILES' 가 없다")
    table = names["STAGE_PROFILES"]
    if not isinstance(table, ast.Dict) or not table.keys:
        raise ValueError("STAGE_PROFILES 가 dict 리터럴이 아니다")
    stages: dict[str, ast.Call] = {}
    for key, value in zip(table.keys, table.values, strict=True):
        if not isinstance(key, ast.Attribute):
            raise ValueError(f"단계 키 모양이 다르다: {ast.dump(key) if key else None}")
        targets = value.args[1] if isinstance(value, ast.Call) and len(value.args) == 2 else None
        if not (
            isinstance(targets, ast.Call)
            and isinstance(targets.func, ast.Name)
            and targets.func.id == "DailyTargets"
            and len(targets.args) == 3
        ):
            raise ValueError(f"{key.attr} 의 DailyTargets 를 찾지 못했다")
        stages[key.attr] = targets
    return stages, names


def _stage_target(source: str, index: int) -> dict[str, Decimal]:
    """단계 → `DailyTargets` 의 `index` 번째 인자 값 (1 = 식이섬유, 2 = 나트륨)."""
    stages, names = _stage_table(source)
    return {stage: Decimal(_eval(targets.args[index], names)) for stage, targets in stages.items()}


def _stage_fiber(source: str) -> dict[str, Decimal]:
    """단계 → `DailyTargets` 두 번째 인자(하루 식이섬유)."""
    return _stage_target(source, 1)


def _stage_sodium(source: str) -> dict[str, Decimal]:
    """단계 → `DailyTargets` 세 번째 인자(하루 나트륨)."""
    return _stage_target(source, 2)


def _stage_names(source: str) -> set[str]:
    """`STAGE_PROFILES` 의 단계 이름 집합."""
    return set(_stage_table(source)[0])


# ─────────────────────────── 사본 일치 (회귀) ───────────────────────────


def test_r103_rule_version_matches_be():
    """행 103 — 규칙 버전 사본이 BE `STAGE_RULE_VERSION` 과 같다. 다르면 아래 숫자 사본도 낡았을 수 있다."""
    assert BE_RULE_VERSION == _const(_read(_INIT), "STAGE_RULE_VERSION")


def test_r104_meals_per_day_matches_be():
    """행 104 — 끼니 수 사본이 BE `rule_engine.MEALS_PER_DAY` 와 같다. 끼니 목표 · 한도가 이 수로 나뉜다."""
    assert MEALS_PER_DAY == _const(_read(_RULE_ENGINE), "MEALS_PER_DAY")


def test_r105_sodium_per_day_matches_be():
    """행 105 — 하루 나트륨 기준 사본이 BE `stage_profile._SODIUM_MG_PER_DAY` 와 같다."""
    be = _const(_read(_STAGE_PROFILE), "_SODIUM_MG_PER_DAY")
    assert _SODIUM_MG_PER_DAY == pytest.approx(float(be))


def test_r105a_sodium_per_stage_matches_ai_constant():
    """행 105a — BE `STAGE_PROFILES` 의 단계별 나트륨 기준이 모두 AI 의 단계 무관 상수와 같다 (게이트 ⓒ-R4).

    AI 는 나트륨 한도를 단계와 상관없이 `_SODIUM_MG_PER_DAY` 하나로 계산한다. BE 가 한 단계만 다른 값으로
    바꾸면 행 105(모듈 상수 비교)는 그대로 통과하는데 그 단계의 OVER 판정만 BE 화면과 어긋난다.
    """
    be = _stage_sodium(_read(_STAGE_PROFILE))
    assert be, "BE STAGE_PROFILES 에 단계가 없다"
    drifted = {stage: value for stage, value in be.items() if float(value) != pytest.approx(_SODIUM_MG_PER_DAY)}
    assert drifted == {}


@pytest.mark.parametrize("stage", list(Stage), ids=lambda s: s.value)
def test_r106_fiber_per_stage_matches_be(stage):
    """행 106 — 단계별 하루 식이섬유 기준이 BE `STAGE_PROFILES` 와 같다 (INITIAL 만 ×2/3)."""
    be = _stage_fiber(_read(_STAGE_PROFILE))
    assert stage.value in be, f"BE STAGE_PROFILES 에 {stage.value} 가 없다"
    assert _FIBER_G_PER_DAY[stage] == pytest.approx(float(be[stage.value]))


def test_r106a_stage_names_match_be():
    """행 106a — BE `STAGE_PROFILES` 의 단계 이름 집합이 AI `Stage` 이름 집합과 같다 (게이트 ⓒ-R4).

    행 106 은 AI 단계마다 BE 를 찾을 뿐이라, BE 에 단계가 추가되면 잡지 못한다. 추가된 단계의 요청은
    AI 에서 422 가 되거나 `_FIBER_G_PER_DAY` 에서 KeyError 가 난다.
    """
    assert _stage_names(_read(_STAGE_PROFILE)) == {stage.value for stage in Stage}


# ─────────────────────────── 판독기 자기 검사 ───────────────────────────


@pytest.mark.parametrize(
    ("source", "name", "expected"),
    [
        ('STAGE_RULE_VERSION: Final = "v3"\n', "STAGE_RULE_VERSION", "v3"),
        ('_BASE: Final = Decimal("30")\nX = _BASE * 2 / 3\n', "X", Decimal("20")),
    ],
    ids=["rule-version-v3", "decimal-expression"],
)
def test_r107_reader_reads_drifted_values(source, name, expected):
    """행 107 — 판독기가 실제 소스의 값을 읽는다. 고정값을 돌려주는 판독기는 BE 가 바뀌어도 drift 를 못 잡는다."""
    assert _const(source, name) == expected


def test_r107_reader_reads_drifted_stage_fiber():
    """행 107 — 단계 표 판독도 합성 소스의 바뀐 값을 그대로 읽는다."""
    source = (
        '_F = Decimal("30")\n'
        "STAGE_PROFILES = {\n"
        '    MedicationStage.INITIAL: StageProfile(_band("1", "2", "3", "4"), DailyTargets(P, _F / 2, S)),\n'
        "}\n"
    )
    assert _stage_fiber(source) == {"INITIAL": Decimal("15")}


_DRIFTED_STAGE_SOURCE = (
    '_S = Decimal("2300")\n'
    "STAGE_PROFILES = {\n"
    '    MedicationStage.INITIAL: StageProfile(_band("1", "2", "3", "4"), DailyTargets(P, F, _S)),\n'
    '    MedicationStage.EXTRA: StageProfile(_band("1", "2", "3", "4"), DailyTargets(P, F, Decimal("2000"))),\n'
    "}\n"
)


@pytest.mark.parametrize(
    ("read", "expected"),
    [
        pytest.param(_stage_sodium, {"INITIAL": Decimal("2300"), "EXTRA": Decimal("2000")}, id="stage-sodium"),
        pytest.param(_stage_names, {"INITIAL", "EXTRA"}, id="stage-names"),
    ],
)
def test_r107_reader_reads_drifted_stage_table(read, expected):
    """행 107 — 단계별 나트륨 · 단계 목록 판독도 합성 소스의 어긋난 값(한 단계만 다른 나트륨, 추가된 단계)을 읽는다.

    행 105a · 106a 가 지금 통과하는 것이 판독기가 고정값을 돌려줘서가 아님을 확인한다.
    """
    assert read(_DRIFTED_STAGE_SOURCE) == expected


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(lambda tmp: _const('OTHER = "v2"\n', "STAGE_RULE_VERSION"), id="missing-constant"),
        pytest.param(lambda tmp: _const("STAGE_RULE_VERSION = make()\n", "STAGE_RULE_VERSION"), id="unknown-expr"),
        pytest.param(lambda tmp: _stage_fiber('OTHER = "v2"\n'), id="missing-stage-table"),
        pytest.param(lambda tmp: _read(tmp / "backend" / "nope.py"), id="missing-file"),
    ],
)
def test_r108_reader_never_passes_silently(read, tmp_path):
    """행 108 — 상수를 못 찾거나 BE 파일이 없으면 조용히 통과하지 않는다 (결정 H).

    예외(LookupError · ValueError · FileNotFoundError)여야 한다. 값을 돌려주거나 skip 하면 실패다 —
    skip 도 `BaseException` 이라 여기서 잡아 실패로 바꾼다.
    """
    try:
        value = read(tmp_path)
    except (LookupError, ValueError, FileNotFoundError):
        return
    except BaseException as exc:  # pytest.skip 등 — 판독기가 조용히 빠져나간 것이다
        pytest.fail(f"판독기가 예외 대신 {type(exc).__name__} 로 빠져나갔다")
    pytest.fail(f"판독기가 실패 대신 값 {value!r} 를 돌려줬다")
