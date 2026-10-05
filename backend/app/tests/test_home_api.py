"""GET /api/v1/home — 홈 화면(투약 상태 · 위 게이지 · 오늘의 식사) 조회 API (읽기 전용).

새로 계산하는 것은 거의 없다 — 다른 API 들이 만든 값을 모아 한 응답으로 조립한다.
그래서 "같은 값인가"는 이미 머지된 API(`GET /medications/current` · `GET /meals`)의 응답과 비교한다.

**현재 시각은 고정한다.** 공통 기준은 KST 2026-08-21(D) 18:00 이다. `app.core.time.datetime` 을
바꾸므로 `today_kst` · `now_kst` 를 어느 모듈이 import 하든 같은 시각을 본다
(test_daily_feedback_get_api.py 의 frozen_today 와 같다). 시각을 바꾸는 테스트는 `clock.set` 을 쓴다.
테스트 번호(#n)는 계획 `.claude/tdd/current/plan.md` §6 명세표(와 §9-0 추가 행)의 행 번호다.
"""

import logging
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.time import KST
from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.crud import satiety as satiety_crud
from app.models.enums import MealStatus, MealType, MedicationStage, SafetyStatus
from app.models.feedback import MealFeedback
from app.models.meal import Meal, SatietyLog
from app.models.medication import MedicationRecord
from app.models.task import Task
from app.tests.factories import (
    make_meal,
    make_meal_feedback,
    make_meal_item,
    make_qqs_evaluation,
    make_user,
)

URL = "/api/v1/home"
MEDICATION_URL = "/api/v1/medications/current"
MEALS_URL = "/api/v1/meals"
D = "2026-08-21"
D_DATE = date(2026, 8, 21)
YESTERDAY = D_DATE - timedelta(days=1)


def _at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """그날(KST) hour:minute:second 의 aware datetime."""
    return datetime.combine(day, time(hour, minute, second), tzinfo=KST)


NOW = _at(D_DATE, 18)


# ─────────────────────────── 시각 고정 ───────────────────────────


class _Clock:
    """테스트가 바꿀 수 있는 고정 시각. 기본값은 NOW(D 18:00 KST)."""

    def __init__(self) -> None:
        self.now = NOW

    def set(self, value: datetime) -> None:
        self.now = value


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> _Clock:
    """KST 현재 시각을 고정한다.

    함수가 아니라 그 함수가 읽는 `app.core.time.datetime` 을 바꾼다 — `from … import` 해 간
    곳에서도 잡힌다. DB 의 now() 는 바뀌지 않는다 — 시각이 판정에 쓰이는 값은 직접 심는다.
    """
    state = _Clock()

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = state.now
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("app.core.time.datetime", _FrozenDatetime)
    return state


# ─────────────────────────── helper ───────────────────────────


def _headers(user) -> dict:
    return {"X-User-Id": str(user.id)}


def _get(client, user):
    return client.get(URL, headers=_headers(user))


def _data(response) -> dict:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    return body["data"]


def _home(client, user) -> dict:
    return _data(_get(client, user))


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == code


def _current_medication(client, user) -> dict:
    """같은 시각 조건에서의 GET /medications/current data."""
    return _data(client.get(MEDICATION_URL, headers=_headers(user)))


def _meals_list_item(client, user, meal_id: uuid.UUID) -> dict:
    """GET /meals 목록에서 meal_id 항목을 꺼낸다."""
    items = _data(client.get(MEALS_URL, headers=_headers(user)))["items"]
    matched = [item for item in items if item["mealId"] == str(meal_id)]
    assert len(matched) == 1, items
    return matched[0]


def _history(db, user_id: uuid.UUID, *periods: tuple[str, ...]) -> list[MedicationRecord]:
    """투약 구간들을 직접 깔아 둔다. `(용량, 시작일)` 또는 `(용량, 시작일, 약물)`.

    test_medications_api.py 의 `_history` 와 같다 — POST 로는 과거 날짜 용량 변경을 만들 수 없다.
    저장 stage 는 전부 TITRATION 이다 (재판정 결과와 구분하려는 것).
    """
    records = []
    previous = None
    for index, period in enumerate(periods):
        dose, started = period[0], period[1]
        drug_name = period[2] if len(period) > 2 else "위고비"
        effective_from = date.fromisoformat(started)
        if previous is not None:
            medication_crud.close_current(
                db, previous, effective_to=effective_from - timedelta(days=1)
            )
        previous = medication_crud.create(
            db,
            user_id=user_id,
            drug_name=drug_name,
            dose_mg=Decimal(dose),
            injection_count=index + 1,
            stage=MedicationStage.TITRATION,
            effective_from=effective_from,
        )
        records.append(previous)
    db.flush()
    return records


def _meal(
    db,
    user,
    eaten_at: datetime,
    *,
    meal_type: MealType = MealType.LUNCH,
    status: MealStatus = MealStatus.EVALUATED,
    satiety_after: int | None = None,
) -> Meal:
    """식사 1건. satiety_after 를 주면 확정 시점 식후 포만감(satiety_logs)도 심는다."""
    meal = make_meal(db, user_id=user.id, eaten_at=eaten_at, meal_type=meal_type, status=status)
    if satiety_after is not None:
        satiety_crud.set_satiety_after(db, meal_id=meal.id, pct=satiety_after)
    return meal


def _soft_delete(db, meal: Meal) -> None:
    db.execute(update(Meal).where(Meal.id == meal.id).values(deleted_at=func.now()))
    db.flush()
    db.expire_all()


def _meal_ids(data: dict) -> list[str]:
    return [m["mealId"] for m in data["today"]["meals"]]


def _count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def _make_spec_example(db, user) -> tuple[Meal, Meal]:
    """명세 §7 예시 데이터를 심는다 — (아침, 점심).

    위고비 1.0mg, 2026-06-14 시작 → D 기준 10회차, 다음 투약 2026-08-23(D-2).
    아침 08:20 (74/90/80), 점심 12:40 (평가됨, 포만감 68, SAFE 피드백).
    """
    _history(db, user.id, ("1.0", "2026-06-14"))

    breakfast = _meal(db, user, _at(D_DATE, 8, 20), meal_type=MealType.BREAKFAST)
    make_meal_item(db, meal_id=breakfast.id, display_name="토스트")
    make_meal_item(db, meal_id=breakfast.id, display_name="그릭요거트")
    make_qqs_evaluation(
        db, meal_id=breakfast.id, quantity_score=74, quality_score=90, satiety_score=80
    )

    lunch = _meal(db, user, _at(D_DATE, 12, 40), meal_type=MealType.LUNCH, satiety_after=68)
    make_meal_item(db, meal_id=lunch.id, display_name="비빔밥")
    make_qqs_evaluation(db, meal_id=lunch.id, quantity_score=60, quality_score=70, satiety_score=50)
    make_meal_feedback(
        db, user_id=user.id, meal_id=lunch.id, body="유지기 기준 포만감이 부족한 식사였어요."
    )
    return breakfast, lunch


# ─────────────────────────── 응답 모양 ───────────────────────────


def test_01_spec_example_data_returns_spec_example_shape(client, db):
    """#1: 명세 §7 예시 데이터를 넣으면 명세 예시 모양의 응답이 나온다.

    stage 는 하드코딩하지 않고 같은 시각 조건의 GET /medications/current 와 비교한다 —
    판정은 medication 담당의 규칙이고, 홈은 그 결과를 옮길 뿐이다.
    """
    user = make_user(db)
    breakfast, lunch = _make_spec_example(db, user)

    response = _get(client, user)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    data = body["data"]

    assert data["date"] == D
    medication = data["medication"]
    assert medication["drugName"] == "위고비"
    assert medication["doseMg"] == 1.0
    assert medication["doseCount"] == 10
    assert medication["nextDoseDate"] == "2026-08-23"
    assert medication["daysUntilNextDose"] == 2
    assert medication["doseChangeScheduled"] is False
    assert medication["stage"] == _current_medication(client, user)["stage"]

    stomach = data["stomach"]
    assert stomach["satietyPct"] == 68
    assert stomach["sourceMealId"] == str(lunch.id)
    assert stomach["sourceMealAt"] == "2026-08-21T12:40:00+09:00"
    assert stomach["minutesSinceMeal"] == 320
    assert stomach["feedbackSummary"] == "유지기 기준 포만감이 부족한 식사였어요."

    today = data["today"]
    assert today["recordedCount"] == 2
    assert today["missingMealTypes"] == ["DINNER"]
    first = today["meals"][0]
    assert first["mealId"] == str(breakfast.id)
    assert first["mealType"] == "BREAKFAST"
    assert first["eatenAt"] == "2026-08-21T08:20:00+09:00"
    assert set(first["displayName"].split(", ")) == {"토스트", "그릭요거트"}
    assert first["scores"] == {"quantity": 74, "quality": 90, "satiety": 80}


def test_02_key_sets_match_spec_exactly(client, db):
    """#2: 응답 각 층의 키 집합이 명세 §7 과 정확히 같다 — quantityLabel 같은 명세 밖 필드가 없다.

    라벨은 FE 가 계산한다 (명세 §1). 서버가 필드를 더 내면 명세와 코드가 조용히 갈라진다.
    """
    user = make_user(db)
    _make_spec_example(db, user)

    data = _home(client, user)

    assert set(data) == {"date", "medication", "stomach", "today"}
    assert set(data["medication"]) == {
        "drugName",
        "doseMg",
        "doseCount",
        "stage",
        "nextDoseDate",
        "daysUntilNextDose",
        "doseChangeScheduled",
    }
    assert set(data["stomach"]) == {
        "satietyPct",
        "sourceMealId",
        "sourceMealAt",
        "minutesSinceMeal",
        "feedbackSummary",
    }
    assert set(data["today"]) == {"recordedCount", "meals", "missingMealTypes"}
    assert set(data["today"]["meals"][0]) == {
        "mealId",
        "mealType",
        "eatenAt",
        "displayName",
        "thumbnailUrl",
        "scores",
    }
    assert set(data["today"]["meals"][0]["scores"]) == {"quantity", "quality", "satiety"}


# ─────────────────────────── medication ───────────────────────────

_SHARED_MEDICATION_FIELDS = (
    "drugName",
    "doseMg",
    "doseCount",
    "stage",
    "nextDoseDate",
    "daysUntilNextDose",
)


def test_03_medication_equals_medications_current(client, db):
    """#3: medication 의 6필드가 GET /medications/current 와 같다 (같은 계산을 쓴다).

    투약 이력이 두 구간(0.5 → 1.0)이라 회차·시작일 계산이 현재 행만으로는 안 맞는 경우다.
    """
    user = make_user(db)
    _history(db, user.id, ("0.5", "2026-06-14"), ("1.0", "2026-07-12"))

    home = _home(client, user)["medication"]
    current = _current_medication(client, user)

    for field in _SHARED_MEDICATION_FIELDS:
        assert home[field] == current[field], field


def test_04_stage_is_rejudged_today_not_the_stored_value(client, db):
    """#4: stage 는 저장된 stage 가 아니라 오늘 기준 재판정 결과다.

    같은 용량을 20주째 맞고 있으면 저장값(TITRATION)과 달리 정착 단계다 — 저장값을 읽으면
    마지막 POST 시점에 멈춘 단계가 나간다.
    """
    user = make_user(db)
    _history(db, user.id, ("1.0", (D_DATE - timedelta(weeks=20)).isoformat()))

    stage = _home(client, user)["medication"]["stage"]

    assert stage == _current_medication(client, user)["stage"]
    assert stage != MedicationStage.TITRATION.value


def test_05_home_does_not_change_stored_stage(client, db):
    """#5: 홈 조회가 medication_records 의 저장 stage 를 바꾸지 않는다.

    조회가 쓰기를 하면 폴링마다 이력이 바뀐다.
    """
    user = make_user(db)
    (record,) = _history(db, user.id, ("1.0", (D_DATE - timedelta(weeks=20)).isoformat()))

    _home(client, user)
    db.expire_all()

    stored = db.execute(
        select(MedicationRecord.stage).where(MedicationRecord.id == record.id)
    ).scalar_one()
    assert stored == MedicationStage.TITRATION


def test_06_no_medication_returns_200_with_null_medication(client, db):
    """#6: 투약 미등록 사용자는 409 STAGE_NOT_SET 이 아니라 200 + medication null 이다.

    홈을 에러로 막으면 신규 사용자가 입력 안내조차 못 본다.
    """
    user = make_user(db)

    response = _get(client, user)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["medication"] is None


def test_07_other_users_medication_is_not_mixed_in(client, db):
    """#7: 다른 사용자에게만 투약이 있으면 내 medication 은 null 이다."""
    me = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _history(db, other.id, ("1.0", "2026-06-14"))

    assert _home(client, me)["medication"] is None


def test_08_my_medication_is_kept(client, db):
    """#8: 나와 다른 사용자가 다른 용량이면 내 용량이 실린다."""
    me = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _history(db, me.id, ("1.0", "2026-06-14"))
    _history(db, other.id, ("2.4", "2026-06-14"))

    assert _home(client, me)["medication"]["doseMg"] == 1.0


def test_09_dose_change_scheduled_is_false_not_null(client, db):
    """#9: doseChangeScheduled 는 null 이 아니라 false 다 (예정 변경을 만드는 공개 경로가 없다)."""
    user = make_user(db)
    _history(
        db,
        user.id,
        ("0.25", "2026-05-17"),
        ("0.5", "2026-06-14"),
        ("1.0", "2026-07-12"),
    )

    assert _home(client, user)["medication"]["doseChangeScheduled"] is False


# ─────────────────────────── today — 범위 ───────────────────────────


def test_10_only_todays_meals_yesterday_excluded(client, db):
    """#10: today.meals 에는 오늘(KST) 식사만 있고 어제 식사는 빠진다."""
    user = make_user(db)
    _meal(db, user, _at(YESTERDAY, 12))
    today_meal = _meal(db, user, _at(D_DATE, 12))

    today = _home(client, user)["today"]

    assert _meal_ids({"today": today}) == [str(today_meal.id)]
    assert today["recordedCount"] == 1


def test_11_kst_0030_counts_as_today(client, db):
    """#11: KST 00:30(UTC 로는 전날 15:30) 식사는 오늘이다."""
    user = make_user(db)
    meal = _meal(db, user, _at(D_DATE, 0, 30))

    assert str(meal.id) in _meal_ids(_home(client, user))


def test_12_late_night_meal_stays_on_its_day(client, db):
    """#12: 밤 11시(KST) 식사는 다음날로 넘어가지 않는다 — 어제 23:30 은 빠지고 오늘 23:30 은 남는다."""
    user = make_user(db)
    _meal(db, user, _at(YESTERDAY, 23, 30))
    tonight = _meal(db, user, _at(D_DATE, 23, 30))

    assert _meal_ids(_home(client, user)) == [str(tonight.id)]


def test_13_morning_request_uses_kst_date(client, db, clock):
    """#13: KST 08:30(UTC 로는 전날 23:30)에 조회해도 date 와 today 는 KST 기준이다."""
    clock.set(_at(D_DATE, 8, 30))
    user = make_user(db)
    meal = _meal(db, user, _at(D_DATE, 7))

    data = _home(client, user)

    assert data["date"] == D
    assert str(meal.id) in _meal_ids(data)


def test_14_meals_are_ordered_by_eaten_at_ascending(client, db):
    """#14: today.meals 는 등록 순서가 아니라 eatenAt 오름차순이다."""
    user = make_user(db)
    evening = _meal(db, user, _at(D_DATE, 19), meal_type=MealType.DINNER)
    morning = _meal(db, user, _at(D_DATE, 8), meal_type=MealType.BREAKFAST)
    noon = _meal(db, user, _at(D_DATE, 12))

    assert _meal_ids(_home(client, user)) == [str(morning.id), str(noon.id), str(evening.id)]


def test_15_soft_deleted_meal_is_excluded(client, db):
    """#15: soft delete 된 식사는 today 에서 빠진다."""
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 8), meal_type=MealType.BREAKFAST)
    deleted = _meal(db, user, _at(D_DATE, 12))
    _soft_delete(db, deleted)

    today = _home(client, user)["today"]

    assert today["recordedCount"] == 1
    assert str(deleted.id) not in _meal_ids({"today": today})


def test_16_living_meal_is_kept(client, db):
    """#16: 옆 식사가 지워져도 살아있는 식사는 today 에 남는다 (#15 의 짝)."""
    user = make_user(db)
    alive = _meal(db, user, _at(D_DATE, 8), meal_type=MealType.BREAKFAST)
    deleted = _meal(db, user, _at(D_DATE, 12))
    _soft_delete(db, deleted)

    assert str(alive.id) in _meal_ids(_home(client, user))


def test_17_other_users_meals_are_not_mixed_in(client, db):
    """#17: 다른 사용자의 오늘 식사는 내 today 에 섞이지 않는다."""
    me = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _meal(db, other, _at(D_DATE, 8), meal_type=MealType.BREAKFAST)
    _meal(db, other, _at(D_DATE, 12))
    mine = _meal(db, me, _at(D_DATE, 12))

    today = _home(client, me)["today"]

    assert today["recordedCount"] == 1
    assert _meal_ids({"today": today}) == [str(mine.id)]


# ─────────────────────────── today — 항목 ───────────────────────────


def test_18_unevaluated_meal_has_null_scores(client, db):
    """#18: 평가 전(ANALYZING, qqs 없음) 식사는 scores 가 null 이다 — 0 으로 채우지 않는다."""
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 12), status=MealStatus.ANALYZING)

    assert _home(client, user)["today"]["meals"][0]["scores"] is None


def test_19_evaluated_scores_are_integers_equal_to_get_meals(client, db):
    """#19: 평가된 식사의 점수는 0~100 정수이고 GET /meals 의 scores 와 같다 (같은 반올림 규칙)."""
    user = make_user(db)
    meal = _meal(db, user, _at(D_DATE, 12))
    make_qqs_evaluation(
        db,
        meal_id=meal.id,
        quantity_score=Decimal("74.6"),
        quality_score=Decimal("80.4"),
        satiety_score=Decimal("64.5"),
    )

    scores = _home(client, user)["today"]["meals"][0]["scores"]

    assert all(isinstance(scores[k], int) for k in ("quantity", "quality", "satiety")), scores
    assert scores == _meals_list_item(client, user, meal.id)["scores"]


def test_20_missing_axis_is_null_only_for_that_axis(client, db):
    """#20: 한 축만 채점되지 않았으면 그 축만 null 이고 나머지는 정수다."""
    user = make_user(db)
    meal = _meal(db, user, _at(D_DATE, 12))
    make_qqs_evaluation(
        db, meal_id=meal.id, quantity_score=70, quality_score=None, satiety_score=55
    )

    scores = _home(client, user)["today"]["meals"][0]["scores"]

    assert scores["quality"] is None
    assert scores["quantity"] == 70
    assert scores["satiety"] == 55


def test_21_display_name_and_thumbnail_equal_get_meals(client, db):
    """#21: displayName · thumbnailUrl 은 GET /meals 와 같다 (썸네일 규칙이 어긋나면 여기서 잡힌다)."""
    user = make_user(db)
    meal = _meal(db, user, _at(D_DATE, 12))
    make_meal_item(db, meal_id=meal.id, display_name="현미밥")
    make_meal_item(db, meal_id=meal.id, display_name="된장국")
    db.execute(update(Meal).where(Meal.id == meal.id).values(image_key="meals/lunch-01.jpg"))
    db.flush()
    db.expire_all()

    home_item = _home(client, user)["today"]["meals"][0]
    list_item = _meals_list_item(client, user, meal.id)

    assert home_item["displayName"] == list_item["displayName"]
    assert home_item["thumbnailUrl"] == list_item["thumbnailUrl"]
    assert home_item["thumbnailUrl"] is not None


def test_22_meal_without_photo_has_null_thumbnail(client, db):
    """#22: 사진이 없는(image_key NULL) 식사의 thumbnailUrl 은 null 이다."""
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 12))

    assert _home(client, user)["today"]["meals"][0]["thumbnailUrl"] is None


# ─────────────────────────── today — missingMealTypes ───────────────────────────


def test_23_missing_lists_absent_main_meals_in_order(client, db):
    """#23: missingMealTypes 는 B·L·D 중 기록 안 된 끼니를 BREAKFAST → LUNCH → DINNER 순서로 남긴다."""
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 12), meal_type=MealType.LUNCH)

    assert _home(client, user)["today"]["missingMealTypes"] == ["BREAKFAST", "DINNER"]


def test_24_snack_is_never_missing(client, db):
    """#24: 세 끼를 다 기록했으면 간식이 없어도 missingMealTypes 는 비어 있다 (SNACK 을 넣지 않는다)."""
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 8), meal_type=MealType.BREAKFAST)
    _meal(db, user, _at(D_DATE, 12), meal_type=MealType.LUNCH)
    _meal(db, user, _at(D_DATE, 17), meal_type=MealType.DINNER)

    assert _home(client, user)["today"]["missingMealTypes"] == []


def test_25_no_meals_returns_empty_list_and_three_missing(client, db):
    """#25: 오늘 식사가 없으면 recordedCount 0, meals [], 세 끼 모두 missing 이다."""
    user = make_user(db)

    today = _home(client, user)["today"]

    assert today["recordedCount"] == 0
    assert today["meals"] == []
    assert today["missingMealTypes"] == ["BREAKFAST", "LUNCH", "DINNER"]


def test_26_deleted_meal_type_is_missing_again(client, db):
    """#26: 지운 끼니는 기록된 것으로 치지 않는다 — 다시 missing 이다."""
    user = make_user(db)
    breakfast = _meal(db, user, _at(D_DATE, 8), meal_type=MealType.BREAKFAST)
    _meal(db, user, _at(D_DATE, 12), meal_type=MealType.LUNCH)
    _soft_delete(db, breakfast)

    assert "BREAKFAST" in _home(client, user)["today"]["missingMealTypes"]


def test_27_future_meal_times_are_still_missing(client, db, clock):
    """#27: 아직 시간이 안 된 끼니도 missing 이다 — 시각으로 거르지 않는다 (오전 08:30 에 LUNCH·DINNER)."""
    clock.set(_at(D_DATE, 8, 30))
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 7), meal_type=MealType.BREAKFAST)

    assert _home(client, user)["today"]["missingMealTypes"] == ["LUNCH", "DINNER"]


# ─────────────────────────── stomach — 기준 식사 ───────────────────────────


def test_28_stomach_uses_latest_meal_with_satiety_after(client, db):
    """#28: stomach 는 satiety_after 가 기록된 가장 최근의 내 식사 기준이다."""
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 10), satiety_after=50)
    latest = _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)

    stomach = _home(client, user)["stomach"]

    assert stomach["sourceMealId"] == str(latest.id)
    assert stomach["satietyPct"] == 68
    assert stomach["sourceMealAt"] == "2026-08-21T12:40:00+09:00"


def test_29_newer_meal_without_satiety_after_is_skipped(client, db):
    """#29: 더 최근이라도 satiety_after 가 없는 식사는 건너뛰고 그 앞 식사를 본다."""
    user = make_user(db)
    with_satiety = _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)
    _meal(db, user, _at(D_DATE, 17))

    assert _home(client, user)["stomach"]["sourceMealId"] == str(with_satiety.id)


def test_30_newer_meal_with_satiety_after_is_kept_as_candidate(client, db):
    """#30: 더 최근 식사에도 satiety_after 가 있으면 그 식사가 기준이다 (#29 의 짝)."""
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)
    newer = _meal(db, user, _at(D_DATE, 17), satiety_after=40)

    stomach = _home(client, user)["stomach"]

    assert stomach["sourceMealId"] == str(newer.id)
    assert stomach["satietyPct"] == 40


def test_31_yesterdays_meal_can_be_the_source(client, db):
    """#31: 기준 식사는 오늘로 한정하지 않는다 — 어제 20:00 식사도 기준이 된다 (today 에는 안 나온다)."""
    user = make_user(db)
    yesterday = _meal(db, user, _at(YESTERDAY, 20), satiety_after=55)

    data = _home(client, user)

    assert data["stomach"]["sourceMealId"] == str(yesterday.id)
    assert data["stomach"]["minutesSinceMeal"] == 1320
    assert data["today"]["recordedCount"] == 0


def test_32_satiety_pct_is_the_latest_checkin(client, db):
    """#32: 사후 체크인이 있으면 satietyPct 는 그 끼니의 가장 최근(식후 시점이 가장 늦은) 체크인 값이다.

    "지금 얼마나 부르세요?" 에 답하면 게이지가 바로 그 값으로 바뀌어야 한다.
    """
    user = make_user(db)
    meal = _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)
    satiety_crud.upsert_checkin(db, meal_id=meal.id, offset_hours=3, pct=40)
    satiety_crud.upsert_checkin(db, meal_id=meal.id, offset_hours=1, pct=60)

    assert _home(client, user)["stomach"]["satietyPct"] == 40


def test_32a_without_checkin_satiety_pct_is_satiety_after(client, db):
    """#32a: 체크인이 없으면 확정 시점의 satiety_after 다."""
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)

    assert _home(client, user)["stomach"]["satietyPct"] == 68


def test_32b_checkin_of_another_meal_is_ignored(client, db):
    """#32b: 기준 식사가 아닌 끼니의 체크인은 게이지에 섞이지 않는다."""
    user = make_user(db)
    older = _meal(db, user, _at(D_DATE, 10), satiety_after=50)
    _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)
    satiety_crud.upsert_checkin(db, meal_id=older.id, offset_hours=2, pct=20)

    assert _home(client, user)["stomach"]["satietyPct"] == 68


def test_32c_checkin_does_not_change_the_meal_satiety_score(client, db):
    """#32c: 체크인은 게이지만 바꾼다 — 끼니 포만감 점수(S)는 확정 시점 값 그대로다."""
    user = make_user(db)
    _, lunch = _make_spec_example(db, user)
    satiety_crud.upsert_checkin(db, meal_id=lunch.id, offset_hours=3, pct=40)

    data = _home(client, user)

    assert data["stomach"]["satietyPct"] == 40
    scores = next(m for m in data["today"]["meals"] if m["mealId"] == str(lunch.id))["scores"]
    assert scores["satiety"] == 50


def test_33_deleted_meal_is_not_the_source(client, db):
    """#33: 지운 식사는 기준이 아니다 — 그 앞의 살아있는 식사를 본다."""
    user = make_user(db)
    earlier = _meal(db, user, _at(D_DATE, 10), satiety_after=50)
    deleted = _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)
    _soft_delete(db, deleted)

    stomach = _home(client, user)["stomach"]

    assert stomach["sourceMealId"] == str(earlier.id)
    assert stomach["satietyPct"] == 50


def test_34_other_users_latest_meal_is_not_the_source(client, db):
    """#34: 다른 사용자의 더 최근 식사는 내 기준 식사가 아니다."""
    me = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    mine = _meal(db, me, _at(D_DATE, 10), satiety_after=50)
    _meal(db, other, _at(D_DATE, 17), satiety_after=90)

    stomach = _home(client, me)["stomach"]

    assert stomach["sourceMealId"] == str(mine.id)
    assert stomach["satietyPct"] == 50


@pytest.mark.parametrize("case", ["no_meal", "meal_without_satiety_after"])
def test_35_no_source_meal_returns_null_stomach(client, db, case):
    """#35: 기준 식사가 없으면(식사가 없거나 포만감 기록이 없으면) stomach 는 null 이고 200 이다.

    기본값(0 등)을 지어내지 않는다 — "포만감 0%" 는 거짓이다.
    """
    user = make_user(db)
    if case == "meal_without_satiety_after":
        _meal(db, user, _at(D_DATE, 12))

    assert _home(client, user)["stomach"] is None


def test_36_minutes_since_meal_is_floored(client, db):
    """#36: minutesSinceMeal 은 분 단위 내림이다 — 12:39:01 → 18:00 은 320.98분이라 320.

    반올림(321)·올림(321)과 결과가 갈리는 시각을 쓴다. 320.5분은 round() 의 짝수 반올림도
    320 이 되어 내림을 가려내지 못한다 (RED-CHECK 지적).
    """
    user = make_user(db)
    _meal(db, user, _at(D_DATE, 12, 39, 1), satiety_after=68)

    assert _home(client, user)["stomach"]["minutesSinceMeal"] == 320


def test_49_future_source_meal_has_zero_minutes_not_negative(client, db):
    """#49: eaten_at 이 서버 시각보다 미래인 기준 식사는 minutesSinceMeal 0 이다 (음수를 내지 않는다).

    식사는 후보에서 빼지 않는다 — 기준은 그대로 그 식사다.
    """
    user = make_user(db)
    future = _meal(db, user, _at(D_DATE, 18, 30), satiety_after=60)

    stomach = _home(client, user)["stomach"]

    assert stomach["sourceMealId"] == str(future.id)
    assert stomach["minutesSinceMeal"] == 0


# ─────────────────────────── stomach — feedbackSummary ───────────────────────────


def test_37_safe_feedback_of_source_meal_is_shown(client, db):
    """#37: 기준 식사의 피드백이 SAFE 면 feedbackSummary 에 그 문장이 실린다."""
    user = make_user(db)
    meal = _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)
    make_meal_feedback(db, user_id=user.id, meal_id=meal.id, body="X")

    assert _home(client, user)["stomach"]["feedbackSummary"] == "X"


@pytest.mark.parametrize(
    "safety_status", [SafetyStatus.REVIEW_REQUIRED, SafetyStatus.BLOCKED, None]
)
def test_38_non_safe_or_missing_feedback_is_null(client, db, safety_status):
    """#38: 기준 식사 피드백이 REVIEW_REQUIRED · BLOCKED 이거나 없으면 feedbackSummary 는 null 이다.

    가드레일을 통과하지 못한 문장은 화면에 나가면 안 된다. stomach 의 나머지 값은 그대로 채운다.
    """
    user = make_user(db)
    meal = _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)
    if safety_status is not None:
        make_meal_feedback(
            db, user_id=user.id, meal_id=meal.id, body="숨겨야 할 문장", safety_status=safety_status
        )

    stomach = _home(client, user)["stomach"]

    assert stomach["feedbackSummary"] is None
    assert stomach["sourceMealId"] == str(meal.id)
    assert stomach["satietyPct"] == 68


def test_39_does_not_borrow_safe_feedback_from_another_meal(client, db):
    """#39: 기준 식사에 피드백이 없으면 다른 식사의 SAFE 문장을 빌려 오지 않는다."""
    user = make_user(db)
    other_meal = _meal(db, user, _at(D_DATE, 10))  # 포만감 없음 → 기준 아님
    make_meal_feedback(db, user_id=user.id, meal_id=other_meal.id, body="다른 식사의 문장")
    source = _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)

    stomach = _home(client, user)["stomach"]

    assert stomach["sourceMealId"] == str(source.id)
    assert stomach["feedbackSummary"] is None


# ─────────────────────────── crud ───────────────────────────


def test_40_list_meals_in_range_returns_my_living_meals_half_open_ascending(db):
    """#40: list_meals_in_range 는 [start, end) 안의 내 살아있는 식사만 eaten_at 오름차순으로 준다.

    end 는 제외다 — 다음날 00:00 식사가 오늘로 들어오면 두 날에 같은 식사가 보인다.
    """
    me = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    start = _at(D_DATE, 0)
    end = _at(D_DATE + timedelta(days=1), 0)

    _meal(db, me, start - timedelta(minutes=1))  # 직전 — 제외
    at_end = _meal(db, me, end)  # 끝 — 제외
    before_end = _meal(db, me, end - timedelta(minutes=1))  # 끝 직전 — 포함
    at_start = _meal(db, me, start)  # 시작 — 포함
    _meal(db, other, _at(D_DATE, 12))  # 남의 것 — 제외
    deleted = _meal(db, me, _at(D_DATE, 12))  # 삭제 — 제외
    _soft_delete(db, deleted)

    rows = meal_crud.list_meals_in_range(db, user_id=me.id, range_start=start, range_end=end)

    assert [row[0].id for row in rows] == [at_start.id, before_end.id]
    assert at_end.id not in {row[0].id for row in rows}


@pytest.mark.parametrize("case", ["combined", "empty_user"])
def test_41_get_latest_meal_with_satiety_after(db, case):
    """#41: get_latest_meal_with_satiety_after 는 satiety_after 가 있는 가장 최근의 내 살아있는 식사를 준다.

    더 최근이지만 포만감 없는 식사(#29)·지운 식사(#33)·남의 식사(#34)는 건너뛴다. 없으면 None.
    """
    me = make_user(db, nickname="나")
    if case == "empty_user":
        assert meal_crud.get_latest_meal_with_satiety_after(db, user_id=me.id) is None
        return

    other = make_user(db, nickname="남")
    expected = _meal(db, me, _at(D_DATE, 10), satiety_after=50)
    deleted = _meal(db, me, _at(D_DATE, 12, 40), satiety_after=68)
    _soft_delete(db, deleted)
    _meal(db, me, _at(D_DATE, 17))
    _meal(db, other, _at(D_DATE, 18), satiety_after=90)

    row = meal_crud.get_latest_meal_with_satiety_after(db, user_id=me.id)

    assert row is not None
    meal, satiety_after = row
    assert meal.id == expected.id
    assert satiety_after == 50


# ─────────────────────────── 인증 · 사용자 ───────────────────────────


def test_43_missing_header_returns_401(client):
    """#43: X-User-Id 헤더가 없으면 401 UNAUTHORIZED 다 (core/deps.py)."""
    _assert_error(client.get(URL), 401, "UNAUTHORIZED")


def test_44_malformed_user_id_returns_401(client):
    """#44: X-User-Id 가 UUID 형식이 아니면 401 UNAUTHORIZED 다."""
    _assert_error(client.get(URL, headers={"X-User-Id": "not-a-uuid"}), 401, "UNAUTHORIZED")


def test_45_unknown_user_returns_404_not_409(client, db):
    """#45: 없는 사용자는 404 USER_NOT_FOUND 다 — 투약 미등록 409 STAGE_NOT_SET 으로 새지 않는다."""
    response = client.get(URL, headers={"X-User-Id": str(uuid.uuid4())})

    _assert_error(response, 404, "USER_NOT_FOUND")


# ─────────────────────────── 읽기 전용 · 로그 ───────────────────────────


def test_46_home_is_read_only(client, db):
    """#46: GET /home 은 행을 만들지 않는다 — task_queue · meal_feedbacks · satiety_logs · medication_records 행 수 불변.

    홈은 앱을 켤 때마다 불린다. 조회가 작업을 등록하면 AI 호출이 쌓인다.
    """
    user = make_user(db)
    _make_spec_example(db, user)
    models = (Task, MealFeedback, SatietyLog, MedicationRecord)
    before = {model: _count(db, model) for model in models}

    _home(client, user)

    after = {model: _count(db, model) for model in models}
    assert after == before


def test_47_logs_do_not_contain_sensitive_values(client, db, caplog):
    """#47: 로그에 약제명 · 음식명 · 이미지 키 · 피드백 문장을 남기지 않는다 (규칙 6).

    응답에는 나간다 — 로그에만 없어야 한다.
    """
    caplog.set_level(logging.DEBUG)
    user = make_user(db)
    _history(db, user.id, ("1.0", "2026-06-14"))
    meal = _meal(db, user, _at(D_DATE, 12, 40), satiety_after=68)
    make_meal_item(db, meal_id=meal.id, display_name="민감음식-SECRET")
    db.execute(
        update(Meal).where(Meal.id == meal.id).values(image_key="meals/SECRET-key.jpg")
    )
    db.flush()
    db.expire_all()
    make_meal_feedback(db, user_id=user.id, meal_id=meal.id, body="민감문장-SECRET")

    data = _home(client, user)

    assert data["medication"]["drugName"] == "위고비"
    assert data["today"]["meals"][0]["displayName"] == "민감음식-SECRET"
    assert "SECRET-key" in data["today"]["meals"][0]["thumbnailUrl"]
    assert data["stomach"]["feedbackSummary"] == "민감문장-SECRET"
    for secret in ("위고비", "민감음식-SECRET", "meals/SECRET-key.jpg", "민감문장-SECRET"):
        assert secret not in caplog.text
