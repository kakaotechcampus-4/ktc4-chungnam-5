"""`evals/analyze_meal_eval.py` 테스트 — 라벨 → 케이스 빌더 · 실데이터 라벨 · 프롬프트 해시 · `main()` 배선.

모델 · 키 · 외부 네트워크 없음. 테스트 이름의 `r<번호>` 는 계획 §14 명세표 행 번호다.

- 행 44–49 는 `build_cases` 에 **테스트 안에서 만든 라벨**을 넘긴다(실제 `LABELS` 와 무관).
- 행 50–52 는 실제 `LABELS` · `CASES` · `FIXTURE_DIR` 를 쓴다. 행 51 · 52 는 `LABELS` 를 돌며 검사하므로
  `LABELS` 가 비면 공허 통과한다 — 행 51a 가 `LABELS` 가 비지 않음을 막는다.
- 행 54–59 는 `main()` 을 부른다. **실제 `.env` 를 읽지 않는다** — 모든 테스트에서 `app.config.get_settings` ·
  `app.food_analyzer.get_settings` 를 "부르면 실패" 로 바꿔 두고(autouse), 필요한 테스트만 가짜 설정으로 덮는다.
  같은 autouse 가 작업 디렉터리를 `tmp_path` 로 옮긴다 — `Settings()` 를 직접 만들어도 cwd 기준 `ai/.env` 를 못 읽는다.
  키 확인은 `evals.harness.has_api_key` 를, 모델 POST 는 `httpx.AsyncClient.post` 만 Fake 로 바꾼다.
  이미지 GET 은 실제로 127.0.0.1 로컬 서버에 간다.

구현이 지켜야 하는 참조 방식 (테스트가 monkeypatch 하는 위치):
- `main` 은 `CASES` · `FIXTURE_DIR` 를 **호출 시점의 모듈 전역**으로 읽는다.
- `main` 은 `evals.harness` 를 모듈째 참조해 `harness.has_api_key` · `harness.default_post` 를 호출 시점에 꺼낸다.
- `main` 은 `fixture_server` 모듈을 import 하고 `fixture_server.serve_fixtures` 를 호출 시점에 참조한다.
- `cases_prompt_hash` 는 `app.food_analyzer._build_prompt` 를 **호출 시점에 모듈 속성으로** 참조한다.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import struct
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

import evals.analyze_meal_eval as analyze_meal_eval
from app import food_analyzer
from app.schemas import AnalyzeMealRequest
from evals import analyze_meal_checks as amc
from evals import fixture_server, harness
from evals.analyze_meal_eval import build_cases, cases_prompt_hash, main
from evals.fixture_server import fixture_problems
from evals.harness import Axis, Case

_PATH = "/analyze-meal"

# 이슈 표의 판정 축 등급 (계약 축 `contract_parseable` 은 `Case.contract` 로 붙고 critical 이다)
_GRADES: dict[str, str] = {
    "gram_unit_default": "non_critical",
    "natural_unit_allowed": "non_critical",
    "no_phantom_food": "non_critical",
    "no_missing_food": "non_critical",
    "non_food_empty": "non_critical",
    "standard_name": "non_critical",
    "amount_accuracy": "ambiguous",
    "confidence_calibration": "ambiguous",
}
_UNIT_AXES = frozenset({"gram_unit_default", "natural_unit_allowed"})
_OBSERVATION_AXES = frozenset({"amount_accuracy", "confidence_calibration"})

# MEAL few-shot 예시 음식 — 라벨 음식명에 나오면 예시를 베낀 답이 정답처럼 보인다
_EXAMPLE_FOODS = ("참치김밥", "김밥", "라면", "삶은 달걀", "방울토마토")
# 단어 첫머리에서만 찾는다 — 앞이 문자열 시작 · 공백 · 문장부호일 때 (`test_eval_meal_cases.py` 와 같은 규칙)
_EXAMPLE_FOOD_PATTERNS = tuple((food, re.compile(rf"(?<!\w){re.escape(food)}")) for food in _EXAMPLE_FOODS)

# 행 46 라벨 — 팩토리 축에 묶일 값
_HANSANG_LABEL = {
    "meal_type": "DINNER",
    "unit": "gram",
    "required": [["밥", "쌀밥", "흰쌀밥"], ["된장찌개"]],
    "non_foods": ["그릇", "숟가락"],
    "forbidden": ["비비고"],
}

# 모델 POST Fake 가 돌려줄 유효한 인식 결과 (few-shot · 프롬프트 예시 밖 음식)
_MODEL_ITEMS = [{"originalFoodName": "닭가슴살 샐러드", "estimatedAmount": 250, "unit": "g", "confidence": 0.8}]
_MODEL_REPLY = {"choices": [{"message": {"content": json.dumps({"items": _MODEL_ITEMS}, ensure_ascii=False)}}]}


# ─────────────────────────── 최소 이미지 바이트 ───────────────────────────


def _segment(marker: int, payload: bytes) -> bytes:
    """JPEG 세그먼트 — 마커 2바이트 + 길이(자기 자신 2바이트 포함) + 본문."""
    return struct.pack(">BBH", 0xFF, marker, len(payload) + 2) + payload


_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"
_APP0 = _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
_APP1_EXIF = _segment(0xE1, b"Exif\x00\x00" + b"II*\x00\x08\x00\x00\x00")


def _jpeg(*, exif: bool = False) -> bytes:
    """SOI · APP0(JFIF) · EOI 최소 JPEG (EXIF 없음). `exif=True` 면 SOI 바로 뒤에 APP1 Exif 를 넣는다."""
    return _SOI + (_APP1_EXIF if exif else b"") + _APP0 + _EOI


# ─────────────────────────── 빌더 ───────────────────────────


def _request(case_id: str, meal_type: str, image: str) -> dict:
    """§14 고정값을 따른 analyze-meal 요청 JSON."""
    return {
        "mealId": f"eval-{case_id}",
        "mealType": meal_type,
        "eatenAt": "2026-10-29T12:30:00+09:00",
        "stage": "MAINTENANCE",
        "imageUrl": image,
    }


def _case(case_id: str = "meal", meal_type: str = "LUNCH", *, image: str | None = None, n: int = 1) -> Case:
    """`build_cases` 를 거치지 않고 만든 Case — 계약 + 관측 축 하나. `main` · 해시 테스트용."""
    return Case(
        id=case_id,
        path=_PATH,
        request=_request(case_id, meal_type, image or f"{case_id}.jpg"),
        contract=amc.contract_parseable,
        axes=(Axis("amount_accuracy", amc.amount_accuracy, "ambiguous"),),
        n=n,
    )


def _item(name: str, amount: float = 100, unit: str = "g", confidence: float = 0.8) -> dict:
    return {"originalFoodName": name, "estimatedAmount": amount, "unit": unit, "confidence": confidence}


def _resp(*items: dict) -> dict:
    """`AnalyzeMealResponse` 모양의 응답 JSON."""
    return {"mealId": "eval-x", "modelVersion": "fake-model", "safetyStatus": "SAFE", "items": list(items)}


def _axis_ids(case: Case) -> set[str]:
    return {axis.id for axis in case.axes}


def _axis(case: Case, axis_id: str) -> Axis:
    found = [axis for axis in case.axes if axis.id == axis_id]
    assert len(found) == 1, f"{case.id} 에 {axis_id} 축이 {len(found)}개다"
    return found[0]


def _only_case(labels: dict) -> Case:
    cases = build_cases(labels)
    assert len(cases) == 1, cases
    return cases[0]


def _example_hits(text: str) -> list[str]:
    """`text` 에서 단어 첫머리에 나온 MEAL few-shot 예시 음식."""
    return [food for food, pattern in _EXAMPLE_FOOD_PATTERNS if pattern.search(text)]


# ─────────────────────────── 픽스처 ───────────────────────────


def _settings_forbidden():
    raise AssertionError("테스트가 실제 설정(.env)을 읽으려 했다")


@pytest.fixture(autouse=True)
def _no_real_settings(monkeypatch, tmp_path):
    """모든 테스트에서 실제 설정 읽기를 막는다 — 부르면 AssertionError. 필요한 테스트만 가짜 설정으로 덮는다.

    `Settings` 는 `.env` 를 cwd 기준으로 읽는다 — `get_settings` 를 거치지 않고 `Settings()` 를 직접 만드는 경로도
    `ai/.env` 를 못 읽게 cwd 를 빈 `tmp_path` 로 옮긴다. 이 파일의 테스트는 상대경로에 기대지 않는다.
    """
    monkeypatch.setattr("app.config.get_settings", _settings_forbidden)
    monkeypatch.setattr("app.food_analyzer.get_settings", _settings_forbidden)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def model_posts(monkeypatch) -> list[tuple[str, dict]]:
    """`app.food_analyzer.get_settings` 를 가짜로 바꾸고 모델 POST(`httpx.AsyncClient.post`)만 Fake 로 둔다.

    `.env` 를 읽지 않고 게이트웨이에 가지 않는다. 이미지 GET(`AsyncClient.get`)은 실제로 로컬 서버에 간다.
    선례: `test_eval_fixture_server.py` 의 같은 이름 픽스처.
    """
    fake_settings = SimpleNamespace(
        LLM_VISION_URL="https://mlapi.run/fake-vision",
        LLM_TIMEOUT_SEC=5.0,
        LLM_MAX_RETRIES=0,
        LLM_API_KEY=SimpleNamespace(get_secret_value=lambda: "verify-dummy"),
    )
    monkeypatch.setattr("app.food_analyzer.get_settings", lambda: fake_settings)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)

    calls: list[tuple[str, dict]] = []

    async def _fake_post(self, url, **kwargs):
        calls.append((str(url), kwargs))
        return httpx.Response(200, json=_MODEL_REPLY, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    return calls


def _use_cases(monkeypatch, fixture_dir, *cases: Case) -> None:
    """`main` 이 호출 시점에 읽는 모듈 전역 `CASES` · `FIXTURE_DIR` 를 바꾼다."""
    monkeypatch.setattr(analyze_meal_eval, "CASES", tuple(cases))
    monkeypatch.setattr(analyze_meal_eval, "FIXTURE_DIR", fixture_dir)


def _key_check_forbidden():
    raise AssertionError("키 확인(has_api_key)까지 갔다 — 그 전에 끝나야 한다")


@pytest.fixture
def valid_fixture(tmp_path):
    """EXIF 없는 최소 JPEG 하나가 든 fixture 디렉터리와 그 사진의 Case."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    data = _jpeg()
    (fixture_dir / "meal.jpg").write_bytes(data)
    assert fixture_problems(fixture_dir / "meal.jpg") == []
    return SimpleNamespace(dir=fixture_dir, data=data, case=_case("meal", image="meal.jpg"))


@pytest.fixture
def wired(monkeypatch, valid_fixture, model_posts):
    """행 57–59 배선 구성 — tmp fixture + Case 1개 · 키 있음 · 가짜 설정 · 모델 POST Fake · 서버 base 기록.

    base 를 기록하려고 `fixture_server.serve_fixtures` 를 감싼다 — 구현은 이 모듈 속성을 호출 시점에 참조해야 한다.
    """
    _use_cases(monkeypatch, valid_fixture.dir, valid_fixture.case)
    monkeypatch.setattr(harness, "has_api_key", lambda: True)

    original = fixture_server.serve_fixtures
    bases: list[str] = []

    @contextmanager
    def _recording_serve(directory):
        with original(directory) as base:
            bases.append(base)
            yield base

    monkeypatch.setattr(fixture_server, "serve_fixtures", _recording_serve)
    return SimpleNamespace(data=valid_fixture.data, case=valid_fixture.case, posts=model_posts, bases=bases)


_CLOSED_TIMEOUT = httpx.Timeout(5.0, connect=0.3)
"""닫힌 서버로의 GET — 연결 단계만 짧게 기다린다(거부 · 타임아웃 어느 쪽이든 닫힌 것으로 본다)."""


# ─────────────────────────── build_cases (테스트 라벨) ───────────────────────────


_MEAL_TYPES = ("BREAKFAST", "LUNCH", "DINNER", "SNACK")


@pytest.mark.parametrize("meal_type", _MEAL_TYPES, ids=[f"r44-{m}" for m in _MEAL_TYPES])
def test_r44_one_label_becomes_one_case_with_contract_and_request(meal_type):
    """행 44 — 라벨 하나는 Case 하나가 된다: path `/analyze-meal`, 계약 = `analyze_meal_checks.contract_parseable`,
    요청이 `AnalyzeMealRequest` 를 만족, imageUrl = 파일 이름, mealType = 라벨값, id = 파일 이름 stem, n 기본 3 · 인자 반영.

    요청 고정값(§14): mealId = `eval-<case id>`, stage = MAINTENANCE, eatenAt 은 +09:00 고정 시각 — 실행마다 같은 seed 다.
    imageUrl 에 주소를 넣으면 실행 때 `with_image_base` 가 주소를 겹쳐 붙인다(파일 이름만 둔다).
    MealType 4종 모두 라벨값이 그대로 요청 mealType 이 된다 — 한 끼니를 거부하거나 고정값으로 덮으면 그 끼니 프롬프트가 측정되지 않는다.
    """
    labels = {"bibimbap.jpg": {"meal_type": meal_type, "unit": "gram"}}

    case = _only_case(labels)
    model = AnalyzeMealRequest.model_validate(case.request)

    assert case.path == _PATH
    assert case.contract is amc.contract_parseable
    assert case.id == "bibimbap"
    assert case.request["imageUrl"] == "bibimbap.jpg"
    assert case.request["mealType"] == meal_type
    assert model.meal_type == meal_type
    assert case.request["mealId"] == "eval-bibimbap"
    assert case.request["stage"] == "MAINTENANCE"
    assert model.eaten_at.utcoffset() == dt.timedelta(hours=9)
    assert build_cases(labels)[0].request == case.request, "요청이 실행마다 달라진다"
    assert case.n == 3
    assert build_cases(labels, n=5)[0].n == 5


_REAL_DATETIME = dt.datetime
# 바꿔 끼울 "실행 시각" 두 개 — 서로 멀고, 어떤 고정 seed 와도 우연히 같기 어렵다
_RUN_TIMES = (dt.datetime(2001, 2, 3, 4, 5, 6, 789000), dt.datetime(2038, 7, 8, 9, 10, 11, 123000))


def _frozen_datetime(moment: dt.datetime) -> type[dt.datetime]:
    """`now` · `utcnow` · `today` 가 `moment` 를 돌려주는 `datetime` 대역. 다른 동작은 진짜 `datetime` 과 같다."""

    class _FrozenDatetime(_REAL_DATETIME):
        @classmethod
        def now(cls, tz=None):
            return moment.replace(tzinfo=tz) if tz is not None else moment

        @classmethod
        def utcnow(cls):
            return moment

        @classmethod
        def today(cls):
            return moment

    return _FrozenDatetime


def _build_at(monkeypatch, labels: dict, moment: dt.datetime) -> tuple[Case, ...]:
    """실행 시각을 `moment` 로 바꿔 끼운 채 `build_cases` 를 부른다. 끝나면 바로 되돌린다.

    구현이 시각을 얻는 경로를 모두 덮는다 — `datetime` 모듈의 `datetime` 속성(`import datetime` · `datetime.datetime.now`)과,
    `analyze_meal_eval` 모듈 전역 중 진짜 `datetime` 클래스를 가리키는 이름(`from datetime import datetime [as x]`).
    """
    frozen = _frozen_datetime(moment)
    with monkeypatch.context() as m:
        m.setattr(dt, "datetime", frozen)
        for name, value in list(vars(analyze_meal_eval).items()):
            if value is _REAL_DATETIME:
                m.setattr(analyze_meal_eval, name, frozen)
        return build_cases(labels)


def test_r44_eaten_at_is_fixed_seed_not_run_time(monkeypatch):
    """행 44 — 요청 eatenAt 은 실행 시각이 아니라 고정 seed 다. 실행 시각을 다른 값으로 바꿔 끼워도 요청이 같다.

    요청 JSON 이 seed 다 — eatenAt 이 실행마다 바뀌면 같은 케이스가 회차 · baseline 마다 다른 입력이 된다.
    연속 두 번 만들어 비교하는 것으로는 못 잡는다(시계 해상도 안에서 같은 값이 나온다) — 시각을 직접 바꿔 끼운다.
    """
    labels = {"bibimbap.jpg": {"meal_type": "LUNCH", "unit": "gram"}}

    first = _build_at(monkeypatch, labels, _RUN_TIMES[0])
    second = _build_at(monkeypatch, labels, _RUN_TIMES[1])

    assert len(first) == len(second) == 1
    assert second[0].request["eatenAt"] == first[0].request["eatenAt"], "eatenAt 이 실행 시각을 따라간다"
    assert second[0].request == first[0].request
    eaten_at = AnalyzeMealRequest.model_validate(first[0].request).eaten_at
    assert eaten_at.utcoffset() == dt.timedelta(hours=9)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [("gram", {"gram_unit_default"}), ("natural", {"natural_unit_allowed"}), (None, set())],
    ids=["r45-gram", "r45-natural", "r45-no-unit"],
)
def test_r45_unit_label_attaches_only_its_unit_axis(unit, expected):
    """행 45 — `unit: "gram"` 이면 `gram_unit_default` 만, `"natural"` 이면 `natural_unit_allowed` 만 붙는다.
    unit 라벨이 없으면 단위 축은 붙지 않는다.

    두 단위 축은 서로 반대를 요구한다 — 한 사진에 둘 다 붙으면 어떤 답도 한쪽에서 실패한다.
    """
    label = {"meal_type": "LUNCH", "required": [["잡채"]]}
    if unit is not None:
        label["unit"] = unit

    case = _only_case({"japchae.jpg": label})

    assert _axis_ids(case) & _UNIT_AXES == expected
    for axis_id in expected:
        assert _axis(case, axis_id).check is getattr(amc, axis_id)


_R46_GOOD = _resp(_item("쌀밥", 210), _item("된장찌개", 300), _item("배추김치", 40))
_R46_BAD = {
    "no_missing_food": _resp(_item("쌀밥", 210), _item("배추김치", 40)),  # 된장찌개 묶음이 빠졌다
    "no_phantom_food": _resp(_item("쌀밥", 210), _item("된장찌개", 300), _item("숟가락", 30)),  # 라벨 비음식
    "standard_name": _resp(_item("쌀밥", 210), _item("비비고 된장찌개", 300)),  # 라벨 금지어
}


@pytest.mark.parametrize("axis_id", sorted(_R46_BAD), ids=[f"r46-{a}" for a in sorted(_R46_BAD)])
def test_r46_label_values_are_bound_to_factory_axes(axis_id):
    """행 46 — `required` · `non_foods` · `forbidden` 은 라벨 값을 묶은 팩토리 축이 된다.
    라벨을 어긴 나쁜 응답(누락 · 비음식 이름 · 금지어 이름)은 실패하고, 라벨 모범 응답은 통과한다.

    판정 기준은 §12 를 따른다 — 누락은 공백 제거 후 완전 일치, 비음식은 낱말 단위, 금지어는 부분 문자열.
    모범 응답이 동의어("쌀밥")로만 통과하므로 라벨이 아닌 다른 값이 묶이면 드러난다. 라벨을 묶지 않으면
    축이 늘 "라벨 비어 있음" 으로 실패하거나 무엇이든 통과해 측정이 공허해진다.
    """
    case = _only_case({"hansang.jpg": _HANSANG_LABEL})
    axis = _axis(case, axis_id)

    bad_ok, _ = axis.check(_R46_BAD[axis_id], case.request)
    good_ok, good_note = axis.check(_R46_GOOD, case.request)

    assert bad_ok is False
    assert good_ok is True, good_note


_R47_LABELS = {
    "japchae.jpg": {
        "meal_type": "LUNCH",
        "unit": "gram",
        "required": [["잡채"]],
        "non_foods": ["접시"],
        "forbidden": ["비비고"],
    },
    "whole_cake.jpg": {"meal_type": "DINNER", "unit": "natural"},
    "empty_desk.png": {"meal_type": "SNACK", "empty": True},
}


def test_r47_axis_grades_match_issue_table():
    """행 47 — 붙은 축의 등급이 이슈 표와 같다 (non-critical 6 · 관측 2). 8개 축이 모두 쓰이고 표 밖 축은 없다.

    등급이 바뀌면 게이트가 조용히 느슨해지거나(관측으로 내려감) 엄격해진다.
    """
    cases = build_cases(_R47_LABELS)

    used = {axis.id for case in cases for axis in case.axes}
    wrong = [(case.id, axis.id, axis.grade) for case in cases for axis in case.axes if _GRADES.get(axis.id) != axis.grade]

    assert used == set(_GRADES)
    assert wrong == []


def test_r48_food_case_gets_observation_axes_and_empty_case_only_non_food_empty():
    """행 48 — 음식 케이스에는 관측 2축이 붙고, `empty: True` 케이스에는 `non_food_empty` 하나만 붙는다.

    음식 없는 사진에 음식 축(단위 · 누락 · 관측)이 붙으면 "항목을 내지 말라" 와 반대를 요구한다.
    `non_food_empty` 가 음식 케이스에 붙으면 모든 정상 답이 실패한다.
    """
    cases = {
        case.id: case
        for case in build_cases(
            {"salad.jpg": {"meal_type": "LUNCH", "unit": "gram"}, "desk.jpg": {"meal_type": "SNACK", "empty": True}}
        )
    }
    food, empty = cases["salad"], cases["desk"]

    assert _OBSERVATION_AXES <= _axis_ids(food)
    assert "non_food_empty" not in _axis_ids(food)
    assert _axis(food, "amount_accuracy").check is amc.amount_accuracy
    assert _axis(food, "confidence_calibration").check is amc.confidence_calibration
    assert _axis_ids(empty) == {"non_food_empty"}
    assert _axis(empty, "non_food_empty").check is amc.non_food_empty


_VALID_LABEL = {"meal_type": "LUNCH", "unit": "gram"}
_R49_BAD = [
    ("r49-no-meal-type", "meal.jpg", {"unit": "gram"}),
    ("r49-bad-meal-type", "meal.jpg", {"meal_type": "BRUNCH", "unit": "gram"}),
    ("r49-lower-meal-type", "meal.jpg", {"meal_type": "lunch", "unit": "gram"}),
    ("r49-unknown-key", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "forbiden": ["비비고"]}),
    ("r49-bad-unit", "meal.jpg", {"meal_type": "LUNCH", "unit": "g"}),
    ("r49-empty-with-unit", "desk.jpg", {"meal_type": "SNACK", "empty": True, "unit": "gram"}),
    ("r49-empty-with-required", "desk.jpg", {"meal_type": "SNACK", "empty": True, "required": [["잡채"]]}),
    ("r49-empty-required", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "required": []}),
    ("r49-empty-non-foods", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "non_foods": []}),
    ("r49-empty-forbidden", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "forbidden": []}),
    ("r49-empty-synonym-group", "meal.jpg", {"meal_type": "LUNCH", "required": [["잡채"], []]}),
    ("r49-blank-synonym", "meal.jpg", {"meal_type": "LUNCH", "required": [["잡채", ""]]}),
    ("r49-blank-non-food", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "non_foods": [""]}),
    ("r49-blank-forbidden", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "forbidden": [""]}),
    ("r49-str-non-foods", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "non_foods": "그릇"}),
    ("r49-str-forbidden", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "forbidden": "비비고"}),
    ("r49-str-required", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "required": "잡채"}),
    ("r49-str-synonym-group", "meal.jpg", {"meal_type": "LUNCH", "required": [["잡채"], "김치"]}),
    ("r49-name-upper", "Meal.jpg", _VALID_LABEL),
    ("r49-name-space", "my meal.jpg", _VALID_LABEL),
    ("r49-name-hash", "meal#1.jpg", _VALID_LABEL),
    ("r49-name-question", "meal?.jpg", _VALID_LABEL),
    ("r49-name-percent", "meal%20.jpg", _VALID_LABEL),
    ("r49-name-hangul", "비빔밥.jpg", _VALID_LABEL),
    ("r49-ext-upper-JPG", "meal.JPG", _VALID_LABEL),
    ("r49-ext-gif", "meal.gif", _VALID_LABEL),
    ("r49-ext-heic", "meal.heic", _VALID_LABEL),
    ("r49-ext-webp", "meal.webp", _VALID_LABEL),
    ("r49-ext-none", "meal", _VALID_LABEL),
    ("r49-test-prefix", "test_meal.jpg", _VALID_LABEL),
    ("r49-observation-only", "meal.jpg", {"meal_type": "LUNCH"}),
    # 비음식은 낱말 단위 판정 — 공백이 든 원소는 어떤 낱말과도 같아질 수 없어 공허 통과한다
    ("r49-non-food-with-space", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "non_foods": ["나무 젓가락"]}),
    ("r49-non-food-with-space-2nd", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "non_foods": ["그릇", "빈 그릇"]}),
    # 공백만인 원소 — 빈 문자열과 같은 공허 · 과잉 판정
    ("r49-space-only-non-food", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "non_foods": [" "]}),
    ("r49-space-only-forbidden", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "forbidden": [" "]}),
    ("r49-tab-only-forbidden", "meal.jpg", {"meal_type": "LUNCH", "unit": "gram", "forbidden": ["\t"]}),
    ("r49-space-only-synonym", "meal.jpg", {"meal_type": "LUNCH", "required": [[" "]]}),
    ("r49-space-only-synonym-2nd", "meal.jpg", {"meal_type": "LUNCH", "required": [["밥", " "]]}),
]


@pytest.mark.parametrize(("name", "label"), [(n, lb) for _, n, lb in _R49_BAD], ids=[i for i, _, _ in _R49_BAD])
def test_r49_invalid_label_raises_value_error_with_file_name(name, label):
    """행 49 — 잘못된 라벨은 ValueError 이고 메시지에 파일 이름이 들어 있다. 정상 라벨이 앞에 있어도 걸러진다.

    - 라벨 키 오타 · 값 오류를 조용히 넘기면 그 축이 빠진 채 측정된다.
    - 빈 목록 · 빈 묶음 · 빈 문자열은 판정을 공허하게 만든다 — 금지어 `""` 는 모든 이름에 걸린다(§12-3).
    - 목록 자리의 str 은 팩토리가 글자 단위로 쪼갠다(`"그릇"` → `{"그","릇"}`, §12-4).
    - 파일 이름은 `[a-z0-9_.-]` 만 — `with_image_base` 는 quote 하지 않고 서버는 unquote 해 `#` · `?` · `%` 가 깨진다(§13-1).
    - 확장자는 소문자 jpg · jpeg · png 만(서버 content-type 표), `test_` 이름은 `ai/evals` 안 `test_*` 금지 규칙.
    - 관측 축만 남는 음식 케이스는 게이트에 아무것도 기여하지 않는다.
    - `non_foods` 원소에 공백이 있으면 거른다 — 비음식은 낱말 단위 판정이라 `"나무 젓가락"` 은 어떤 낱말과도 같아질 수 없다(§15 ①).
    - 공백만인 원소(`" "` · `"\\t"`)는 빈 문자열과 같다 — 금지어 `" "` 는 공백 든 모든 이름에 걸리고, 동의어 `" "` 는 늘 누락이다(§15 ①).
    """
    labels = {"ok_meal.jpg": _VALID_LABEL, name: label}

    with pytest.raises(ValueError) as excinfo:
        build_cases(labels)

    assert name in str(excinfo.value)


def test_r49_same_stem_fixtures_raise_value_error():
    """행 49 — stem 이 같은 fixture 두 개(`meal.jpg` + `meal.png`)는 ValueError 이고 메시지에 stem 이나 파일 이름이 들어 있다.

    case id 가 stem 이라 둘이 같은 id 가 된다 — 결과 · baseline 이 id 로 묶여 한쪽이 조용히 덮어써진다.
    """
    labels = {"meal.jpg": _VALID_LABEL, "meal.png": _VALID_LABEL}

    with pytest.raises(ValueError) as excinfo:
        build_cases(labels)

    message = str(excinfo.value)
    assert "meal" in message, message


_R49B_NAMES = [
    ("meal-1.jpeg", "meal-1"),
    ("a_b.c.png", "a_b.c"),
    ("2026_10_29.jpg", "2026_10_29"),
    ("my_test.png", "my_test"),
    ("latest_test.jpg", "latest_test"),
]


@pytest.mark.parametrize(("name", "stem"), _R49B_NAMES, ids=[f"r49b-{n}" for n, _ in _R49B_NAMES])
def test_r49b_allowed_file_names_build_a_case_with_stem_id(name, stem):
    """행 49b — 규칙 안의 파일 이름(하이픈 · 숫자 · 점 여러 개 · `.jpeg` · 중간이나 끝의 `test`)은 거르지 않는다.
    ValueError 없이 Case 하나가 나오고 id 는 마지막 확장자만 뗀 stem 이다(`a_b.c.png` → `a_b.c`).

    행 49 의 이름 규칙(`[a-z0-9_.-]` · 소문자 jpg/jpeg/png · `test_` 시작 금지)의 짝이다 — 과하게 거르면 정상 사진을 못 쓴다.
    `test_` 금지는 **시작**만이다(`ai/evals` 안 `test_*` 파일 규칙). id 를 첫 점 앞까지 자르면 서로 다른 사진이 겹친다.
    """
    case = _only_case({name: _VALID_LABEL})

    assert case.id == stem
    assert case.request["imageUrl"] == name
    assert case.request["mealId"] == f"eval-{stem}"


_R49C_WORDS = [
    ("r49c-non-foods-no-space", "non_foods", ["그릇", "젓가락"], "no_phantom_food"),
    ("r49c-forbidden-with-space", "forbidden", ["빙그레 우유"], "standard_name"),
    ("r49c-synonym-with-space", "required", [["흰 쌀밥", "쌀밥"]], "no_missing_food"),
]


@pytest.mark.parametrize(
    ("key", "value", "axis_id"), [(k, v, a) for _, k, v, a in _R49C_WORDS], ids=[i for i, _, _, _ in _R49C_WORDS]
)
def test_r49c_valid_words_build_a_case_with_their_factory_axis(key, value, axis_id):
    """행 49 남김 짝 — 공백 없는 비음식(`"그릇"`)과 공백이 든 금지어(`"빙그레 우유"`) · 동의어(`"흰 쌀밥"`)는 거르지 않는다.
    ValueError 없이 Case 하나가 나오고 그 키의 팩토리 축이 붙는다.

    행 49 의 공백 규칙(§15 ①)의 짝이다 — 공백 금지는 낱말 단위로 보는 `non_foods` 만이다.
    금지어는 부분 문자열, 누락은 공백을 지운 뒤 완전 일치로 판정하므로(§12) 띄어 쓴 이름도 유효하다.
    과하게 거르면 브랜드명 · 띄어 쓴 음식명을 라벨에 못 쓴다.
    """
    case = _only_case({"meal.jpg": {"meal_type": "LUNCH", "unit": "gram", key: value}})

    assert _axis(case, axis_id).grade == "non_critical"


# ─────────────────────────── 실데이터 (LABELS · CASES · FIXTURE_DIR) ───────────────────────────


def test_r50_empty_labels_give_no_cases_and_cases_follow_labels():
    """행 50 — `build_cases({})` 는 빈 튜플이고, 모듈의 `CASES` 는 `build_cases(LABELS)` 와 같다.

    `CASES` 를 손으로 따로 두면 라벨을 고쳐도 케이스가 바뀌지 않는다 — 라벨이 유일한 입력이어야 한다.
    팩토리 축은 같은 인자면 `==` 로 같은 값이라 `Case` 를 그대로 비교한다(행 50b · 50c 가 그 양방향).
    """
    assert build_cases({}) == ()
    assert analyze_meal_eval.CASES == build_cases(analyze_meal_eval.LABELS)


# 행 50b — `_HANSANG_LABEL` 에서 팩토리 라벨 값 하나만 바꾼 변형
_R50B_VARIANTS = [
    ("r50b-required", "required", [["밥", "쌀밥", "흰쌀밥"], ["김치찌개"]]),
    ("r50b-non-foods", "non_foods", ["그릇", "젓가락"]),
    ("r50b-forbidden", "forbidden", ["청정원"]),
]


@pytest.mark.parametrize(
    ("key", "value"), [(k, v) for _, k, v in _R50B_VARIANTS], ids=[i for i, _, _ in _R50B_VARIANTS]
)
def test_r50b_cases_differing_only_in_one_factory_label_value_are_not_equal(key, value):
    """행 50b — 팩토리 라벨 값(`required` · `non_foods` · `forbidden`) 하나만 다른 라벨로 만든 케이스는 `!=` 다.

    id · 요청 · 축 id · 등급은 같으므로 차이는 팩토리 축의 인자에서만 나와야 한다 — 축 id 만 비교하면
    라벨 값이 엉뚱하게 묶인 `CASES` 를 행 50 이 놓친다.
    """
    original = build_cases({"hansang.jpg": _HANSANG_LABEL})
    changed = build_cases({"hansang.jpg": {**_HANSANG_LABEL, key: value}})

    # 전제 — 축 구성이 같아야 `!=` 가 팩토리 인자에서 나온 것이다(변형이 축을 빼거나 더하면 공허 통과)
    assert [[(a.id, a.grade) for a in c.axes] for c in original] == [[(a.id, a.grade) for a in c.axes] for c in changed]
    assert original != changed


def test_r50c_cases_built_twice_from_same_label_are_equal():
    """행 50c — 같은 라벨로 두 번 만든 케이스는 `==` 다 — 행 50b 의 짝.

    호출마다 새 클로저면 같은 라벨끼리도 다르다고 나와 행 50 이 고칠 수 없는 빨강이 된다.
    """
    labels = {"hansang.jpg": _HANSANG_LABEL}

    assert build_cases(labels) == build_cases(labels)


def test_r51_every_labelled_fixture_exists_without_problems():
    """행 51 — `LABELS` 의 모든 파일이 `FIXTURE_DIR` 에 있고 `fixture_problems` 가 비어 있다 (없음 · 크기 · EXIF · 형식).

    `LABELS` 를 돌며 검사하므로 `LABELS` 가 비면 공허 통과한다 — 행 51a 가 `LABELS` 가 비지 않음을 막는다.
    """
    problems = [
        problem
        for name in analyze_meal_eval.LABELS
        for problem in fixture_problems(analyze_meal_eval.FIXTURE_DIR / name)
    ]

    assert problems == []


def test_r51a_labels_are_not_empty():
    """행 51a — 사진 라벨(`LABELS`)이 하나 이상 있다.

    행 51(사진 존재 · 파일 검사) · 52(few-shot 겹침)는 `LABELS` 를 돌며 검사한다 — 라벨이 비면 아무것도 보지 않고 통과한다.
    """
    assert len(analyze_meal_eval.LABELS) >= 1


def test_r52_required_synonyms_avoid_meal_few_shot_foods():
    """행 52 — `LABELS` 의 `required` 동의어에 MEAL few-shot 예시 음식(참치김밥 · 김밥 · 라면 · 삶은 달걀 · 방울토마토)이 없다.

    겹치면 예시를 베낀 답이 정답처럼 보인다. 금지어는 **단어 첫머리에서만** 찾는다(`test_eval_meal_cases.py` 와 같은 규칙) —
    매칭 규칙 자체를 먼저 확인한다(이 자기 검사는 지금도 실제로 돈다).
    본 검사는 `LABELS` 를 돌므로 `LABELS` 가 비면 공허 통과한다 — 행 51a 가 `LABELS` 가 비지 않음을 막는다.
    """
    # 매칭 규칙 자기 검사 — 단어 첫머리의 예시 음식은 잡고, 낱말 안쪽 · 다른 음식은 잡지 않는다
    assert _example_hits("라면") == ["라면"]
    assert _example_hits("라면을 먹었어요") == ["라면"]
    assert _example_hits("참치김밥") == ["참치김밥"]
    assert _example_hits("삶은 달걀") == ["삶은 달걀"]
    assert _example_hits("방울토마토 5개") == ["방울토마토"]
    assert _example_hits("먹어도 되는 음식이라면 괜찮아요") == []
    assert _example_hits("토마토 샐러드") == []
    assert _example_hits("김치볶음밥") == []

    hits = [
        (name, synonym, food)
        for name, label in analyze_meal_eval.LABELS.items()
        for group in label.get("required", [])
        for synonym in group
        for food in _example_hits(synonym)
    ]

    assert hits == []


# ─────────────────────────── cases_prompt_hash ───────────────────────────


def test_r53_prompt_hash_depends_only_on_meal_type_set_and_prompt(monkeypatch):
    """행 53 — `cases_prompt_hash` 는 결정적이고, 케이스가 쓰는 mealType 집합이 다르면 바뀌고,
    `_build_prompt` 가 바뀌면 바뀐다. 같은 mealType 이 여러 케이스여도 한 번만 반영되고 순서와 무관하다.

    baseline 비교의 조건이다 — 프롬프트가 바뀌었는데 해시가 같으면 다른 조건의 baseline 과 조용히 비교된다.
    케이스를 하나 더해도 mealType 집합이 같으면 프롬프트는 같으므로 해시도 같아야 한다.
    집합의 mealType **각각**의 프롬프트가 들어가야 한다 — 하나만 반영하면 다른 끼니 프롬프트가 바뀌어도 해시가 같다.
    구현은 `app.food_analyzer._build_prompt` 를 **호출 시점에 모듈 속성으로** 참조해야 한다(이 테스트가 그 위치를 바꾼다).
    """
    lunch_a, lunch_b, dinner = _case("a", "LUNCH"), _case("b", "LUNCH"), _case("c", "DINNER")

    lunch_only = cases_prompt_hash([lunch_a])
    assert cases_prompt_hash([lunch_a]) == lunch_only
    assert cases_prompt_hash([_case("c", "DINNER")]) != lunch_only
    both = cases_prompt_hash([lunch_a, dinner])
    assert both != lunch_only
    assert cases_prompt_hash([lunch_a, lunch_b]) == lunch_only
    assert cases_prompt_hash([dinner, lunch_b, lunch_a]) == both
    dinner_only = cases_prompt_hash([dinner])
    assert dinner_only != both, "mealType 집합 {DINNER} 와 {DINNER, LUNCH} 가 같은 해시다"

    # mealType 별 프롬프트가 각각 반영되는가 — DINNER 프롬프트만 바꾸면 DINNER 가 든 목록만 해시가 바뀐다
    original = food_analyzer._build_prompt
    monkeypatch.setattr(
        "app.food_analyzer._build_prompt",
        lambda meal_type: original(meal_type) + (" 저녁만 바뀐 문장" if meal_type == "DINNER" else ""),
    )
    assert cases_prompt_hash([lunch_a]) == lunch_only, "DINNER 프롬프트만 바꿨는데 LUNCH 해시가 바뀌었다"
    assert cases_prompt_hash([dinner]) != dinner_only
    assert cases_prompt_hash([lunch_a, dinner]) != both

    monkeypatch.setattr("app.food_analyzer._build_prompt", lambda meal_type: f"다른 프롬프트 {meal_type}")
    assert cases_prompt_hash([lunch_a]) != lunch_only


# ─────────────────────────── main ───────────────────────────


def test_r54_unknown_only_id_exits_2_before_key_check(monkeypatch, capsys, valid_fixture):
    """행 54 — `main(["--only", "nope"])` 은 2 로 끝난다. 키 확인(`has_api_key`) 전에 오용으로 판정한다.

    CLI 오용을 키 확인 뒤에 보면 키가 없는 PC 에서 SKIP(0) 으로 묻혀 오타를 모른다.
    구현은 `harness.has_api_key` 를 호출 시점에 모듈 속성으로 참조해야 한다(이 테스트가 그 위치를 바꾼다).
    """
    _use_cases(monkeypatch, valid_fixture.dir, valid_fixture.case)
    monkeypatch.setattr(harness, "has_api_key", _key_check_forbidden)

    code = main(["--only", "nope"])

    assert code == 2
    assert "nope" in capsys.readouterr().out


def test_r55_no_api_key_skips_with_exit_0_and_no_model_call(monkeypatch, capsys, valid_fixture, model_posts):
    """행 55 — `harness.has_api_key` 가 False 면 `main([])` 은 0 으로 끝나고 SKIP 을 출력하며 모델 POST 는 0회다.

    키 없는 PC(CI · 팀원)에서 eval 이 실패로 보이거나 크레딧을 쓰려 하면 안 된다.
    """
    _use_cases(monkeypatch, valid_fixture.dir, valid_fixture.case)
    key_calls: list[None] = []

    def _no_key() -> bool:
        key_calls.append(None)
        return False

    monkeypatch.setattr(harness, "has_api_key", _no_key)

    code = main([])

    assert code == 0
    assert "SKIP" in capsys.readouterr().out
    assert key_calls, "SKIP 이 키 확인에서 나오지 않았다"
    assert model_posts == []


@pytest.mark.parametrize(
    ("name", "write"),
    [("ghost.jpg", None), ("exif_meal.jpg", _jpeg(exif=True))],
    ids=["r56-missing", "r56-exif"],
)
def test_r56_fixture_problem_exits_2_before_key_check_and_model_call(
    monkeypatch, capsys, tmp_path, model_posts, name, write
):
    """행 56 — CASES 의 fixture 에 문제(없는 파일 · EXIF)가 있으면 `main([])` 은 2 로 끝나고 문제를 파일 이름과 함께 출력한다.
    키 확인 · 모델 POST 는 0회다.

    서버가 요청 로그를 끄므로 fixture 누락과 모델 실패가 둘 다 `status 500` 으로 보인다 — 실행 전에 막는다.
    EXIF 사진을 그대로 모델에 보내면 촬영 위치가 외부로 나간다.
    """
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    if write is not None:
        (fixture_dir / name).write_bytes(write)
    _use_cases(monkeypatch, fixture_dir, _case(name.rsplit(".", 1)[0], image=name))
    monkeypatch.setattr(harness, "has_api_key", _key_check_forbidden)

    code = main([])

    assert code == 2
    assert name in capsys.readouterr().out
    assert model_posts == []


def test_r56b_no_cases_exits_2_with_message(monkeypatch, capsys, valid_fixture, model_posts):
    """행 56b — 케이스가 0개면(`CASES == ()`) 키가 있어도 `main([])` 은 문제를 출력하고 2(오용)로 끝난다.

    0개로 돌리면 `0/0` 으로 게이트 PASS 가 찍힌다 — 아무것도 재지 않았는데 통과로 보인다.
    문구는 단언하지 않는다(무엇이 문제인지 출력했는가만 본다).
    """
    _use_cases(monkeypatch, valid_fixture.dir)
    monkeypatch.setattr(harness, "has_api_key", lambda: True)

    code = main([])

    assert code == 2
    assert capsys.readouterr().out.strip() != ""


def test_r56c_no_cases_save_does_not_write_baseline(monkeypatch, valid_fixture, model_posts, tmp_path):
    """행 56c — 케이스가 0개면 `main(["--save", 경로])` 는 2 로 끝나고 baseline 파일을 만들지 않는다.

    빈 baseline 이 저장되면 다음 비교가 "비교할 케이스 없음" 으로 조용히 통과한다.
    """
    _use_cases(monkeypatch, valid_fixture.dir)
    monkeypatch.setattr(harness, "has_api_key", lambda: True)
    saved = tmp_path / "b.json"

    code = main(["--save", str(saved)])

    assert code == 2
    assert not saved.exists(), "케이스 0개인데 baseline 이 저장됐다"


def _serve_forbidden(directory):
    raise AssertionError("fixture 서버를 띄웠다 — 케이스 0개면 그 전에 끝나야 한다")


@pytest.mark.parametrize("argv", [[], ["--only", "nope"]], ids=["r56d-no-args", "r56d-only-nope"])
def test_r56d_no_cases_exits_before_key_check_and_server(monkeypatch, valid_fixture, model_posts, argv):
    """행 56d — 케이스가 0개면 인자와 무관하게 키 확인 · 서버 기동 · 모델 POST 전에 2 로 끝난다.

    0개 판정 · 사유 출력 · 2 는 `harness.cli_main` 이 한다(인자 해석 뒤, 키 확인 전). `main` 은 0개면 서버를 띄우지 않고 위임한다.
    키 확인 · 서버 기동은 "불리면 AssertionError" 대역으로 바꾼다. 키 확인 뒤에 막으면 키 없는 PC 에서 SKIP(0) 으로 묻힌다.
    구현은 `harness.has_api_key` · `fixture_server.serve_fixtures` 를 호출 시점에 모듈 속성으로 참조해야 한다.
    """
    _use_cases(monkeypatch, valid_fixture.dir)
    monkeypatch.setattr(harness, "has_api_key", _key_check_forbidden)
    monkeypatch.setattr(fixture_server, "serve_fixtures", _serve_forbidden)

    code = main(argv)

    assert code == 2
    assert model_posts == []


def test_s18_no_cases_help_exits_0(monkeypatch, valid_fixture, model_posts):
    """행 s18 — 케이스가 0개여도 `main(["--help"])` 는 도움말을 내고 0 으로 끝난다. 키 확인 · 서버 기동은 하지 않는다.

    0개 검사가 인자 해석 전에 있으면 케이스가 비었을 때 사용법조차 볼 수 없다(`--help` 도 2).
    """
    _use_cases(monkeypatch, valid_fixture.dir)
    monkeypatch.setattr(harness, "has_api_key", _key_check_forbidden)
    monkeypatch.setattr(fixture_server, "serve_fixtures", _serve_forbidden)

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert model_posts == []


def test_r57_main_wires_fixture_server_to_model_call(wired):
    """행 57 — 배선 통합: tmp fixture + Case 1개 · 키 있음 · 가짜 설정 · 모델 POST Fake 에서 `main(["--n", "1"])` 은 0 이고,
    모델 POST 는 1회이며 모델에 간 data URI 가 fixture 바이트의 base64 다.

    `main` 이 서버를 띄우고 imageUrl 에 주소를 붙여 `default_post` 로 `main.py` 를 거친다는 것을 고정한다 —
    한 군데라도 빠지면 이미지 GET 이 실패해 500(모델 POST 0회)이 된다.
    주소는 보낼 요청에만 붙는다 — 끝난 뒤 CASES 의 요청 `imageUrl` 은 파일 이름 그대로다(제자리에서 고치면
    회차마다 주소가 겹쳐 붙고, 같은 프로세스의 다음 실행 · baseline 에 실행마다 바뀌는 주소가 남는다).
    """
    code = main(["--n", "1"])

    assert code == 0
    assert len(wired.posts) == 1
    _, kwargs = wired.posts[0]
    image_block = kwargs["json"]["messages"][0]["content"][0]
    assert image_block["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(wired.data).decode("ascii")
    assert wired.case.request["imageUrl"] == "meal.jpg"
    assert analyze_meal_eval.CASES[0].request["imageUrl"] == "meal.jpg"


def test_r58_saved_baseline_has_no_server_address(wired, tmp_path):
    """행 58 — `--save` 로 저장한 baseline 에 `127.0.0.1` · `http://` · 서버 포트가 없고,
    `rule_version == RULE_VERSION`, `prompt_hash == cases_prompt_hash(CASES)` 다.

    서버 주소는 실행마다 바뀐다 — baseline 에 남으면 비교 · 커밋 diff 가 매번 흔들리고 로컬 환경이 기록된다.
    포트 검사는 해시 값을 뺀 본문에서 한다(16진 해시에 숫자열이 우연히 들어갈 수 있다).
    """
    saved = tmp_path / "b.json"

    code = main(["--n", "1", "--save", str(saved)])

    assert code == 0
    text = saved.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert doc["rule_version"] == analyze_meal_eval.RULE_VERSION
    assert doc["prompt_hash"] == cases_prompt_hash(analyze_meal_eval.CASES)
    assert wired.bases, "serve_fixtures 가 base 를 내지 않았다"
    port = str(httpx.URL(wired.bases[0]).port)
    assert "127.0.0.1" not in text
    assert "http://" not in text
    assert port not in text.replace(doc["prompt_hash"], "")


def test_r59_server_is_closed_after_main_returns(wired):
    """행 59 — `main` 이 끝난 뒤 `serve_fixtures` 가 낸 base 주소로의 연결이 거부된다.

    eval 이 끝난 뒤 포트가 열려 있으면 사진 디렉터리가 계속 노출되고 다음 실행과 겹친다.
    구현은 `fixture_server` 모듈을 import 하고 `fixture_server.serve_fixtures` 를 호출 시점에 참조해야 한다(이 테스트가 감싼다).
    """
    main(["--n", "1"])

    assert len(wired.bases) == 1, wired.bases
    with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
        httpx.get(f"{wired.bases[0]}/meal.jpg", timeout=_CLOSED_TIMEOUT, trust_env=False)
