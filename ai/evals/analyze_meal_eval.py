"""`/analyze-meal` eval — 사진 fixture 라벨로 만든 케이스(CASES)와 실행 진입점.

`ai/` 에서 `python -m evals.analyze_meal_eval` 로 실행한다(스크립트 경로로 실행하면 `app` 을 못 찾는다).
실제 모델을 부르므로 메인 세션이 사용자 승인 후에만 실행한다. 사용법 · 축 · 판정 한계는 `evals/README.md`.

케이스는 **사진** 단위다. 라벨(`LABELS[파일이름]`)이 그 사진에 붙일 축과 기대값을 정하고,
`build_cases` 가 라벨을 Case 로 바꾼다 — 사진을 추가할 때 코드를 고치지 않고 라벨만 더한다.
이미지 GET 은 `fixture_server` 의 로컬 서버가 대신하고, 모델 POST 는 실제다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from app import food_analyzer
from app.schemas import MealType
from evals import analyze_meal_checks as amc
from evals import fixture_server, harness
from evals.harness import Axis, Case, Check, Grade

FIXTURE_DIR: Path = Path(__file__).resolve().parent / "fixtures" / "analyze_meal"
"""사진 fixture 디렉터리. Dockerfile 이 복사하지 않으므로 이미지에 들어가지 않는다."""

RULE_VERSION: str = "analyze-meal-labels-v1"
"""baseline 비교 조건. analyze-meal 은 BE 규칙 버전이 없어 라벨 · 판정을 바꾸면 이 값을 올린다."""

LABELS: dict[str, dict] = {}
"""사진별 라벨. 키 = fixture 파일 이름. 팀원이 채운다 — 형식은 `evals/README.md`."""

_PATH = "/analyze-meal"

CRASH_LOG = Path(__file__).resolve().parent / "analyze_meal_crashes.log"
"""하네스 크래시 traceback. 루트 `.gitignore` 의 `*.log` 가 막는다."""

# 요청 고정값 — 요청 JSON 이 seed 라 실행 시각을 쓰지 않는다. analyze-meal 프롬프트는 stage 를 쓰지 않는다
_EATEN_AT = "2026-10-29T12:30:00+09:00"
_STAGE = "MAINTENANCE"

_MEAL_TYPES = frozenset(meal_type.value for meal_type in MealType)
_UNITS = ("gram", "natural")
_FOOD_KEYS = frozenset({"unit", "required", "non_foods", "forbidden"})  # 음식 케이스에 판정 축을 붙이는 키
_KEYS = frozenset({"meal_type", "empty", *_FOOD_KEYS})

# 파일 이름 규칙 — `with_image_base` 는 이름을 quote 하지 않고 서버는 unquote 하므로 `#` · `?` · `%` 가 깨진다.
# 확장자는 서버 content-type 표와 같은 규칙으로 소문자만 받는다
_NAME = re.compile(r"[a-z0-9_.-]+")
_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})


# ─────────────────────────── 라벨 검사 ───────────────────────────


def _check_name(name: str) -> None:
    """fixture 파일 이름 규칙 — 문자 집합 · 소문자 jpg/jpeg/png · `test_` 시작 금지."""
    if not _NAME.fullmatch(name):
        raise ValueError(f"{name}: 파일 이름은 [a-z0-9_.-] 만 쓴다")
    if PurePosixPath(name).suffix not in _EXTENSIONS:
        raise ValueError(f"{name}: 확장자가 소문자 .jpg · .jpeg · .png 가 아니다")
    if name.startswith("test_"):
        raise ValueError(f"{name}: 이름이 test_ 로 시작한다 — ai/evals 안에는 test_* 를 두지 않는다")


def _check_words(name: str, key: str, value: object) -> None:
    """비어 있지 않은 문자열들의 비어 있지 않은 목록인가. str 하나는 팩토리가 글자 단위로 쪼개므로 거부한다."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name}: {key} 는 목록이어야 한다 — {value!r}")
    if not value:
        raise ValueError(f"{name}: {key} 가 비어 있다")
    if not all(isinstance(word, str) and word for word in value):
        raise ValueError(f"{name}: {key} 에 빈 문자열이나 문자열 아닌 값이 있다 — {value!r}")


def _check_label(name: str, label: Mapping) -> None:
    """라벨 하나를 검사한다. 잘못되면 파일 이름을 담은 ValueError — 오타 · 빈 값을 넘기면 그 축이 빠진 채 측정된다."""
    _check_name(name)
    keys = set(label)
    if keys - _KEYS:
        raise ValueError(f"{name}: 모르는 라벨 키 {sorted(keys - _KEYS)}")
    if label.get("meal_type") not in _MEAL_TYPES:
        raise ValueError(f"{name}: meal_type 은 {sorted(_MEAL_TYPES)} 중 하나다 — {label.get('meal_type')!r}")

    if "empty" in label:
        if label["empty"] is not True:
            raise ValueError(f"{name}: empty 는 True 만 쓴다 — {label['empty']!r}")
        if keys & _FOOD_KEYS:
            raise ValueError(f"{name}: empty 는 다른 축({sorted(keys & _FOOD_KEYS)})과 함께 쓸 수 없다")
        return

    if not keys & _FOOD_KEYS:
        raise ValueError(f"{name}: 음식 축(unit · required · non_foods · forbidden)이 하나도 없다 — 관측 축만 남는다")
    if "unit" in label and label["unit"] not in _UNITS:
        raise ValueError(f"{name}: unit 은 {list(_UNITS)} 중 하나다 — {label['unit']!r}")
    if "required" in label:
        groups = label["required"]
        if not isinstance(groups, (list, tuple)) or not groups:
            raise ValueError(f"{name}: required 는 동의어 묶음의 비어 있지 않은 목록이다 — {groups!r}")
        for group in groups:
            _check_words(name, "required 의 동의어 묶음", group)
    for key in ("non_foods", "forbidden"):
        if key in label:
            _check_words(name, key, label[key])


# ─────────────────────────── 빌더 ───────────────────────────


def _axis(check: Check, grade: Grade) -> Axis:
    """축 id 는 `evals.analyze_meal_checks` 함수 이름이다 (factory 가 만든 Check 도 factory 이름을 단다)."""
    return Axis(check.__name__, check, grade)


_GRAM = _axis(amc.gram_unit_default, "non_critical")
_NATURAL = _axis(amc.natural_unit_allowed, "non_critical")
_EMPTY = _axis(amc.non_food_empty, "non_critical")
# 음식 케이스에 늘 붙는 관측 축 — 게이트에서 빠지고 사람이 읽는다
_OBSERVATIONS: tuple[Axis, ...] = (
    _axis(amc.amount_accuracy, "ambiguous"),
    _axis(amc.confidence_calibration, "ambiguous"),
)


def _axes(label: Mapping) -> tuple[Axis, ...]:
    """검사를 통과한 라벨에 붙일 축. 사진별 기대값(required · non_foods · forbidden)은 팩토리가 묶는다."""
    if label.get("empty"):
        return (_EMPTY,)
    axes: list[Axis] = []
    if "unit" in label:
        axes.append(_GRAM if label["unit"] == "gram" else _NATURAL)
    if "required" in label:
        axes.append(_axis(amc.no_missing_food([list(group) for group in label["required"]]), "non_critical"))
    if "non_foods" in label:
        axes.append(_axis(amc.no_phantom_food(list(label["non_foods"])), "non_critical"))
    if "forbidden" in label:
        axes.append(_axis(amc.standard_name(list(label["forbidden"])), "non_critical"))
    return (*axes, *_OBSERVATIONS)


def _case(name: str, label: Mapping, *, n: int) -> Case:
    """id 는 파일 이름의 stem 이고, imageUrl 에는 파일 이름만 둔다(실행 시 `with_image_base` 가 주소를 붙인다)."""
    case_id = PurePosixPath(name).stem
    return Case(
        id=case_id,
        path=_PATH,
        request={
            "mealId": f"eval-{case_id}",
            "mealType": label["meal_type"],
            "eatenAt": _EATEN_AT,
            "stage": _STAGE,
            "imageUrl": name,
        },
        contract=amc.contract_parseable,
        axes=_axes(label),
        n=n,
    )


def build_cases(labels: Mapping[str, Mapping], *, n: int = 3) -> tuple[Case, ...]:
    """라벨 하나를 Case 하나로 바꾼다. 잘못된 라벨은 파일 이름을 담은 ValueError.

    stem 이 같은 두 파일(`meal.jpg` · `meal.png`)도 거부한다 — case id 가 겹쳐 결과 · baseline 이 덮어써진다.
    """
    stems: dict[str, str] = {}
    for name, label in labels.items():
        _check_label(name, label)
        stem = PurePosixPath(name).stem
        if stem in stems:
            raise ValueError(f"{name}: case id {stem!r} 가 {stems[stem]} 와 겹친다")
        stems[stem] = name
    return tuple(_case(name, label, n=n) for name, label in labels.items())


CASES: tuple[Case, ...] = build_cases(LABELS)


# ─────────────────────────── 실행 ───────────────────────────


def cases_prompt_hash(cases: Sequence[Case]) -> str:
    """케이스가 쓰는 mealType 각각의 `_build_prompt` 해시 — baseline 의 비교 조건이다.

    mealType 집합(정렬)으로 만든다 — 같은 끼니의 사진을 더해도 보내는 프롬프트가 같으면 해시도 같다.
    """
    meal_types = sorted({case.request["mealType"] for case in cases})
    return harness.prompt_hash(*(food_analyzer._build_prompt(meal_type) for meal_type in meal_types))


def _fixture_problems(cases: Sequence[Case], fixture_dir: Path) -> list[str]:
    return [
        problem
        for case in cases
        for problem in fixture_server.fixture_problems(fixture_dir / case.request["imageUrl"])
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """`serve_fixtures` 로 서버를 띄운 채 `harness.cli_main` 에 위임한다.

    먼저 모든 fixture 를 검사해 문제가 있으면 출력하고 2(오용)로 끝낸다 — 서버가 요청 로그를 꺼서
    fixture 누락과 모델 실패가 둘 다 `status 500` 으로 보이므로 키 확인 · 서버 기동 전에 막는다.
    채널 · 키 확인 · 서버는 호출 시점에 만든다 — import 만으로 `.env` 를 읽거나 서버를 띄우지 않는다.
    """
    cases, fixture_dir = CASES, FIXTURE_DIR
    problems = _fixture_problems(cases, fixture_dir)
    if problems:
        print("fixture 문제 — 실행하지 않는다:")
        for problem in problems:
            print(f"  {problem}")
        return 2

    with fixture_server.serve_fixtures(fixture_dir) as base:
        return harness.cli_main(
            argv,
            cases=cases,
            post_factory=lambda: fixture_server.with_image_base(harness.default_post(), base),
            has_key=harness.has_api_key,
            rule_version=RULE_VERSION,
            prompt_hash=cases_prompt_hash(cases),
            crash_log=CRASH_LOG,
        )


if __name__ == "__main__":
    raise SystemExit(main())
