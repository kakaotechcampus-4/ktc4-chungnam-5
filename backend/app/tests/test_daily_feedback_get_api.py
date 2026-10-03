"""GET /api/v1/insights/daily — 하루 피드백 조회 API (읽기 전용).

행은 워커(`feedback.daily`)가 만든다 — 여기서는 `daily_feedback_crud.upsert` · `replace_sources`
로 직접 심는다. 실패 경로는 FE 가 분기하는 error.code 까지 단언한다.

**시각은 직접 심는다.** `db` 픽스처는 한 트랜잭션이라 그 안의 now() 가 전부 같은 값이다.
그래서 "생성 이후 바뀌었다"를 만들려면 행의 updated_at(생성 시각) · 근거 식사의 created_at ·
Q/Q/S 의 computed_at 을 과거로 옮겨 두고, 변화(새 식사·삭제·투약 기록)의 시각을 그 뒤로 둔다.
테스트 번호(#n)는 계획 `.claude/tdd/current/plan.md` §6 명세표의 행 번호다.
"""

import logging
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.time import KST
from app.crud import daily_feedback as daily_feedback_crud
from app.crud import medication as medication_crud
from app.models.enums import MedicationStage, SafetyStatus, TaskStatus
from app.models.evaluation import QQSEvaluation
from app.models.feedback import DailyFeedback
from app.models.meal import Meal
from app.models.task import Task
from app.tests.factories import (
    make_meal,
    make_meal_feedback,
    make_meal_item,
    make_qqs_evaluation,
    make_user,
)

URL = "/api/v1/insights/daily"
D = "2026-08-21"
D_DATE = date(2026, 8, 21)
TASK_TYPE = "feedback.daily"


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    """그날(KST) hour:minute 의 aware datetime."""
    return datetime.combine(day, time(hour, minute), tzinfo=KST)


# 행 하나의 시간선 (D 기준, KST):
#   12:30 · 18:30 근거 식사 → 12:40 식사 기록(created_at) · 12:45 채점(computed_at)
#   20:00 행 최초 생성(created_at) → 21:00 마지막 생성(updated_at = generatedAt)
#   22:00 생성 이후의 변화(새 식사 기록·삭제·투약 기록)
GENERATED_AT = _at(D_DATE, 21)
AFTER_GENERATED = _at(D_DATE, 22)


# ─────────────────────────── helper ───────────────────────────


def _headers(user) -> dict:
    return {"X-User-Id": str(user.id)}


def _get(client, user, date: str = D):
    return client.get(URL, params={"date": date}, headers=_headers(user))


def _data(response) -> dict:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    return body["data"]


def _count_tasks(db) -> int:
    return db.execute(
        select(func.count()).select_from(Task).where(Task.type == TASK_TYPE)
    ).scalar_one()


def _put_task(
    db,
    *,
    user_id: uuid.UUID,
    date: str = D,
    status: TaskStatus = TaskStatus.PENDING,
    created_at: datetime | None = None,
) -> Task:
    """task_queue 에 feedback.daily 작업 1행을 직접 심는다. flush 까지만.

    같은 트랜잭션 안에서는 now() 가 고정되므로, 순서가 판정에 영향을 주면 created_at 을 명시한다.
    """
    extra = {"created_at": created_at} if created_at is not None else {}
    task = Task(
        type=TASK_TYPE,
        payload={"userId": str(user_id), "date": date},
        status=status,
        **extra,
    )
    db.add(task)
    db.flush()
    return task


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == code


def _make_daily_row(
    db,
    user,
    *,
    feedback_date: date = D_DATE,
    summary: str = "오늘은 점심 이후 포만감이 빨리 떨어졌어요.",
    scores: tuple[int, int, int] = (74, 81, 65),
    safety_status: SafetyStatus = SafetyStatus.SAFE,
) -> tuple[uuid.UUID, list[Meal]]:
    """그날 근거 끼니 2개(점심·저녁)와 하루 피드백 행 1개를 심고 (행 id, 근거 식사들) 을 돌려준다.

    시각은 위 시간선대로 과거로 옮긴다 — 행 생성 이전에 기록·채점된 근거라서,
    아무것도 안 바꾸면 stale=false 여야 한다.
    """
    meals = []
    for hour in (12, 18):
        meal = make_meal(db, user_id=user.id, eaten_at=_at(feedback_date, hour, 30))
        make_qqs_evaluation(db, meal_id=meal.id)
        meals.append(meal)
    meal_feedbacks = [make_meal_feedback(db, user_id=user.id, meal_id=m.id) for m in meals]

    row_id = daily_feedback_crud.upsert(
        db,
        user_id=user.id,
        feedback_date=feedback_date,
        summary=summary,
        quantity_score=scores[0],
        quality_score=scores[1],
        satiety_score=scores[2],
        model_version="stub-daily-0",
        safety_status=safety_status,
    )
    daily_feedback_crud.replace_sources(
        db, daily_feedback_id=row_id, meal_feedback_ids=[f.id for f in meal_feedbacks]
    )

    meal_ids = [m.id for m in meals]
    db.execute(
        update(Meal).where(Meal.id.in_(meal_ids)).values(created_at=_at(feedback_date, 12, 40))
    )
    db.execute(
        update(QQSEvaluation)
        .where(QQSEvaluation.meal_id.in_(meal_ids))
        .values(computed_at=_at(feedback_date, 12, 45))
    )
    db.execute(
        update(DailyFeedback)
        .where(DailyFeedback.id == row_id)
        .values(created_at=_at(feedback_date, 20), updated_at=_at(feedback_date, 21))
    )
    db.flush()
    db.expire_all()
    return row_id, meals


def _add_meal_after_generation(db, user, *, eaten_at: datetime) -> Meal:
    """행 생성 이후(22:00)에 기록된 식사 1건."""
    meal = make_meal(db, user_id=user.id, eaten_at=eaten_at)
    db.execute(update(Meal).where(Meal.id == meal.id).values(created_at=AFTER_GENERATED))
    db.flush()
    return meal


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    """KST 오늘을 D(2026-08-21)로 고정한다.

    `today_kst` 를 어느 모듈이 `from … import` 해 가든 잡히도록, 함수 자체가 아니라
    그 함수가 읽는 `app.core.time.datetime` 을 바꾼다 (test_daily_feedback_refresh_api.py 와 같다).
    DB 의 now() 는 바뀌지 않는다 — 그래서 시각이 판정에 쓰이는 곳은 직접 심는다.
    """

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("app.core.time.datetime", _FrozenDatetime)


# ─────────────────────────── 행 내용 ───────────────────────────


def test_01_row_without_task_returns_ready_with_content(client, db):
    """#1: 그날 행이 있고 작업이 없으면 내용과 함께 READY 를 돌려준다.

    FE 는 이 응답으로 오늘 요약 카드를 그린다.
    """
    user = make_user(db)
    row_id, _ = _make_daily_row(db, user)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "READY"
    assert data["dailyFeedbackId"] == str(row_id)
    assert data["feedbackDate"] == D
    assert data["summary"] == "오늘은 점심 이후 포만감이 빨리 떨어졌어요."
    assert data["scores"] == {"quantity": 74, "quality": 81, "satiety": 65}
    assert data["safetyStatus"] == "SAFE"


def test_02_generated_at_is_updated_at_in_kst(client, db):
    """#2: generatedAt 은 created_at 이 아니라 updated_at(마지막 생성 시각)이고 +09:00 으로 나간다.

    created_at 은 최초 INSERT 에 고정된다 — 재생성해도 옛 시각이 보이면 안 된다.
    """
    user = make_user(db)
    _make_daily_row(db, user)  # created_at 20:00, updated_at 21:00 (KST)

    data = _data(_get(client, user))

    assert data["generatedAt"] == "2026-08-21T21:00:00+09:00"
    assert data["generatedAt"] != _at(D_DATE, 20).isoformat()


def test_03_source_meal_ids_are_the_evidence_meals(client, db):
    """#3: sourceMealIds 는 그 행의 근거 끼니(meal_feedbacks.meal_id)와 일치한다."""
    user = make_user(db)
    _, meals = _make_daily_row(db, user)

    data = _data(_get(client, user))

    assert set(data["sourceMealIds"]) == {str(m.id) for m in meals}
    assert len(data["sourceMealIds"]) == 2


# ─────────────────────────── 상태 판정 ───────────────────────────


def test_04_no_row_no_task_returns_pending_with_empty_shape(client, db):
    """#4: 행도 작업도 없으면 PENDING 이고 본문 필드는 null, sourceMealIds 는 [], stale 은 false 다.

    404 가 아니다 — 폴링 중인 FE 는 상태만 보면 된다 (게이트 1 결정 3·4).
    """
    user = make_user(db)

    data = _data(_get(client, user))

    assert data == {
        "dailyFeedbackId": None,
        "feedbackDate": D,
        "feedbackStatus": "PENDING",
        "summary": None,
        "scores": None,
        "sourceMealIds": [],
        "safetyStatus": None,
        "generatedAt": None,
        "stale": False,
        "staleReason": None,
    }


def test_05_no_row_with_pending_task_returns_generating(client, db):
    """#5: 행이 없고 대기(PENDING) 작업이 있으면 GENERATING 이고 본문은 비어 있다."""
    user = make_user(db)
    _put_task(db, user_id=user.id, status=TaskStatus.PENDING)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "GENERATING"
    assert data["dailyFeedbackId"] is None
    assert data["summary"] is None


def test_06_row_with_pending_task_returns_generating_and_keeps_content(client, db):
    """#6: 행이 있어도 대기 작업이 있으면 GENERATING 이다 — 갱신 중 표시가 우선이고 행 내용은 실린다.

    FE 는 옛 요약을 보여주면서 "새로 만드는 중"을 함께 띄운다.
    """
    user = make_user(db)
    row_id, _ = _make_daily_row(db, user)
    _put_task(db, user_id=user.id, status=TaskStatus.PENDING)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "GENERATING"
    assert data["dailyFeedbackId"] == str(row_id)
    assert data["summary"] == "오늘은 점심 이후 포만감이 빨리 떨어졌어요."


def test_07_no_row_with_failed_task_returns_failed(client, db):
    """#7: 행이 없고 마지막 작업이 FAILED 면 FAILED 다."""
    user = make_user(db)
    _put_task(db, user_id=user.id, status=TaskStatus.FAILED)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "FAILED"
    assert data["dailyFeedbackId"] is None


def test_08_no_row_with_done_task_returns_ready_with_empty_body(client, db):
    """#8: 행이 없고 마지막 작업이 DONE 이면 READY 이고 본문은 비어 있다.

    근거가 없어서 워커가 행을 안 만들고 정상 종료한 경우다 — 계속 폴링하게 두면 안 된다.
    """
    user = make_user(db)
    _put_task(db, user_id=user.id, status=TaskStatus.DONE)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "READY"
    assert data["dailyFeedbackId"] is None
    assert data["summary"] is None
    assert data["scores"] is None
    assert data["sourceMealIds"] == []


def test_09_row_with_failed_task_returns_ready(client, db):
    """#9: 행이 있으면 마지막 작업이 FAILED 여도 READY 이고 내용이 남는다 (#7 의 짝).

    재생성이 실패했다고 이미 있는 요약을 FAILED 로 가리면 안 된다.
    """
    user = make_user(db)
    _make_daily_row(db, user)
    _put_task(db, user_id=user.id, status=TaskStatus.FAILED)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "READY"
    assert data["summary"] == "오늘은 점심 이후 포만감이 빨리 떨어졌어요."


def test_10_older_pending_then_done_is_not_generating(client, db):
    """#10: 오래된 PENDING 뒤에 최신 작업이 DONE 이면 GENERATING 이 아니다 (READY).

    판정은 가장 최근 작업 하나로 한다 — "PENDING 이 하나라도 있으면"으로 구현하면 여기서 걸린다.
    """
    user = make_user(db)
    _put_task(db, user_id=user.id, status=TaskStatus.PENDING, created_at=_at(D_DATE, 9))
    _put_task(db, user_id=user.id, status=TaskStatus.DONE, created_at=_at(D_DATE, 10))

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "READY"


def test_11_older_done_then_pending_is_generating(client, db):
    """#11: 오래된 DONE 뒤에 최신 작업이 PENDING 이면 GENERATING 이다 (#10 의 짝)."""
    user = make_user(db)
    _put_task(db, user_id=user.id, status=TaskStatus.DONE, created_at=_at(D_DATE, 9))
    _put_task(db, user_id=user.id, status=TaskStatus.PENDING, created_at=_at(D_DATE, 10))

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "GENERATING"


# ─────────────────────────── 안전 상태 ───────────────────────────


def test_12_blocked_row_hides_summary_but_keeps_scores_and_sources(client, db):
    """#12: safetyStatus 가 BLOCKED 면 summary 는 null 이고 safetyStatus 는 그대로 나간다.

    가드레일을 통과 못 한 AI 문장은 노출하지 않는다 (규칙 1). 점수·근거는 AI 문장이 아니라
    노출한다 (게이트 1 결정 5).
    """
    user = make_user(db)
    _, meals = _make_daily_row(db, user, safety_status=SafetyStatus.BLOCKED)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "READY"
    assert data["summary"] is None
    assert data["safetyStatus"] == "BLOCKED"
    assert data["scores"] == {"quantity": 74, "quality": 81, "satiety": 65}
    assert set(data["sourceMealIds"]) == {str(m.id) for m in meals}


def test_13_review_required_row_hides_summary(client, db):
    """#13: REVIEW_REQUIRED 도 BLOCKED 와 같이 summary 를 숨기고 safetyStatus 는 그대로 나간다."""
    user = make_user(db)
    _make_daily_row(db, user, safety_status=SafetyStatus.REVIEW_REQUIRED)

    data = _data(_get(client, user))

    assert data["summary"] is None
    assert data["safetyStatus"] == "REVIEW_REQUIRED"


def test_14_safe_row_shows_summary(client, db):
    """#14: SAFE 면 summary 를 그대로 노출한다 (#12·#13 의 짝)."""
    user = make_user(db)
    _make_daily_row(db, user, summary="채소를 먼저 드신 날이었어요.")

    data = _data(_get(client, user))

    assert data["summary"] == "채소를 먼저 드신 날이었어요."
    assert data["safetyStatus"] == "SAFE"


# ─────────────────────────── 점수 ───────────────────────────


def test_15_scores_use_python_round(client, db):
    """#15: 점수(Numeric)는 파이썬 round 로 정수가 된다 — 74.6→75, 80.4→80, 64.5→64.

    dashboard `_round_or_none` 과 같은 규칙이다 (게이트 1 결정 7).
    """
    user = make_user(db)
    row_id, _ = _make_daily_row(db, user)
    db.execute(
        update(DailyFeedback)
        .where(DailyFeedback.id == row_id)
        .values(
            quantity_score=Decimal("74.60"),
            quality_score=Decimal("80.40"),
            satiety_score=Decimal("64.50"),
            updated_at=GENERATED_AT,
        )
    )
    db.flush()

    data = _data(_get(client, user))

    assert data["scores"] == {"quantity": 75, "quality": 80, "satiety": 64}


def test_16_null_score_axis_is_null(client, db):
    """#16: 값이 없는 점수 축만 null 이고 나머지는 정수로 나간다 — 0 으로 지어내지 않는다."""
    user = make_user(db)
    row_id, _ = _make_daily_row(db, user)
    db.execute(
        update(DailyFeedback)
        .where(DailyFeedback.id == row_id)
        .values(satiety_score=None, updated_at=GENERATED_AT)
    )
    db.flush()

    data = _data(_get(client, user))

    assert data["scores"] == {"quantity": 74, "quality": 81, "satiety": None}


# ─────────────────────────── stale ───────────────────────────


def test_17_new_meal_that_day_after_generation_is_new_meals(client, db):
    """#17: 생성 이후 그날 식사가 새로 기록되면 stale=true, NEW_MEALS 다."""
    user = make_user(db)
    _make_daily_row(db, user)
    _add_meal_after_generation(db, user, eaten_at=_at(D_DATE, 18))

    data = _data(_get(client, user))

    assert data["stale"] is True
    assert data["staleReason"] == "NEW_MEALS"


def test_18_nothing_changed_is_not_stale(client, db):
    """#18: 생성 이후 바뀐 게 없으면 stale=false, staleReason=null 이다 (#17 의 짝).

    근거 식사는 생성 전에 기록·채점됐다 — 이걸 낡음으로 보면 항상 stale 이 뜬다.
    """
    user = make_user(db)
    _make_daily_row(db, user)

    data = _data(_get(client, user))

    assert data["stale"] is False
    assert data["staleReason"] is None


def test_19_meal_on_another_day_is_not_stale(client, db):
    """#19: 다음 날(D+1) 식사가 생성 이후 기록돼도 D 의 피드백은 낡지 않는다 (하루 범위 상한)."""
    user = make_user(db)
    _make_daily_row(db, user)
    _add_meal_after_generation(db, user, eaten_at=_at(D_DATE + timedelta(days=1), 9))

    data = _data(_get(client, user))

    assert data["stale"] is False
    assert data["staleReason"] is None


def test_20_meal_at_kst_0030_is_inside_the_day(client, db):
    """#20: D 00:30 KST(UTC 로는 전날) 식사도 D 의 범위 안이다 → stale=true, NEW_MEALS (하한).

    UTC 날짜로 자르면 이 식사를 전날로 보내 놓친다.
    """
    user = make_user(db)
    _make_daily_row(db, user)
    _add_meal_after_generation(db, user, eaten_at=_at(D_DATE, 0, 30))

    data = _data(_get(client, user))

    assert data["stale"] is True
    assert data["staleReason"] == "NEW_MEALS"


def test_21_soft_deleted_evidence_meal_is_meal_deleted(client, db):
    """#21: 생성 전에 기록된 그날 식사가 생성 이후 soft delete 되면 stale=true, MEAL_DELETED 다."""
    user = make_user(db)
    _, meals = _make_daily_row(db, user)
    meals[0].deleted_at = AFTER_GENERATED
    db.flush()

    data = _data(_get(client, user))

    assert data["stale"] is True
    assert data["staleReason"] == "MEAL_DELETED"


def test_22_meal_being_recalculated_is_meal_edited(client, db):
    """#22: 반영된 식사를 고쳐 재계산 대기 중(Q/Q/S 없음 + is_recalculation)이면 stale=true, MEAL_EDITED 다."""
    user = make_user(db)
    _, meals = _make_daily_row(db, user)
    evaluation = db.execute(
        select(QQSEvaluation).where(QQSEvaluation.meal_id == meals[0].id)
    ).scalar_one()
    db.delete(evaluation)
    meals[0].is_recalculation = True
    db.flush()

    data = _data(_get(client, user))

    assert data["stale"] is True
    assert data["staleReason"] == "MEAL_EDITED"


def test_23_medication_record_starting_that_day_is_stage_changed(client, db):
    """#23: 그날 시작하는 투약 기록이 생성 이후 등록되면 stale=true, STAGE_CHANGED 다."""
    user = make_user(db)
    _make_daily_row(db, user)
    record = medication_crud.create(
        db,
        user_id=user.id,
        drug_name="위고비",
        dose_mg=Decimal("0.25"),
        injection_count=1,
        stage=MedicationStage.INITIAL,
        effective_from=D_DATE,
    )
    record.created_at = AFTER_GENERATED
    db.flush()

    data = _data(_get(client, user))

    assert data["stale"] is True
    assert data["staleReason"] == "STAGE_CHANGED"


# ─────────────────────────── 격리 · 읽기 전용 · 로그 ───────────────────────────


def test_29_other_users_row_is_not_visible(client, db):
    """#29: 다른 사용자의 같은 날 행은 내 응답에 보이지 않는다 — 내 상태는 PENDING, id 는 null."""
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _make_daily_row(db, other)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "PENDING"
    assert data["dailyFeedbackId"] is None


def test_30_other_users_pending_task_does_not_affect_me(client, db):
    """#30: 다른 사용자의 같은 날 PENDING 작업은 내 상태를 GENERATING 으로 바꾸지 않는다."""
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _put_task(db, user_id=other.id, status=TaskStatus.PENDING)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "PENDING"


def test_31_my_row_on_another_day_is_not_visible(client, db):
    """#31: 내 다른 날(D-1) 행은 D 조회에 보이지 않는다 — PENDING."""
    user = make_user(db)
    _make_daily_row(db, user, feedback_date=D_DATE - timedelta(days=1))

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "PENDING"
    assert data["dailyFeedbackId"] is None


def test_32_other_users_meal_change_does_not_make_me_stale(client, db):
    """#32: 다른 사용자가 생성 이후 그날 식사를 기록해도 내 피드백은 낡지 않는다."""
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _make_daily_row(db, user)
    _add_meal_after_generation(db, other, eaten_at=_at(D_DATE, 18))

    data = _data(_get(client, user))

    assert data["stale"] is False
    assert data["staleReason"] is None


def test_33_get_creates_no_task_and_no_row(client, db):
    """#33: GET 은 읽기 전용이다 — 작업을 등록하지도, daily_feedbacks 행을 만들지도 않는다.

    폴링(1.5초 주기)마다 작업이 쌓이면 AI 호출이 폭증한다.
    """
    user = make_user(db)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "PENDING"
    assert _count_tasks(db) == 0
    row_count = db.execute(
        select(func.count()).select_from(DailyFeedback).where(DailyFeedback.user_id == user.id)
    ).scalar_one()
    assert row_count == 0


def test_34_logs_do_not_contain_summary_or_food_names(client, db, caplog):
    """#34: 로그에 summary · 음식명 같은 민감정보를 남기지 않는다 (규칙 6)."""
    caplog.set_level(logging.DEBUG)
    user = make_user(db)
    _, meals = _make_daily_row(db, user, summary="민감요약문구-SECRET-51ac")
    make_meal_item(db, meal_id=meals[0].id, display_name="민감음식-SECRET-83bd")

    data = _data(_get(client, user))

    assert data["summary"] == "민감요약문구-SECRET-51ac"  # 응답에는 나가지만 로그에는 없다
    for secret in ("민감요약문구-SECRET-51ac", "민감음식-SECRET-83bd", "김치찌개"):
        assert secret not in caplog.text


def test_35_soft_deleted_source_meal_is_excluded(client, db):
    """#35: 근거 끼니 중 soft delete 된 식사는 sourceMealIds 에서 빠지고 staleReason 은 MEAL_DELETED 다.

    지운 식사로 FE 가 이동하면 404 가 난다 (게이트 1 결정 6).
    """
    user = make_user(db)
    _, meals = _make_daily_row(db, user)
    meals[0].deleted_at = AFTER_GENERATED
    db.flush()

    data = _data(_get(client, user))

    assert str(meals[0].id) not in data["sourceMealIds"]
    assert data["staleReason"] == "MEAL_DELETED"


def test_36_not_deleted_source_meal_remains(client, db):
    """#36: 삭제되지 않은 근거 끼니는 sourceMealIds 에 남는다 (#35 의 짝)."""
    user = make_user(db)
    _, meals = _make_daily_row(db, user)
    meals[0].deleted_at = AFTER_GENERATED
    db.flush()

    data = _data(_get(client, user))

    assert data["sourceMealIds"] == [str(meals[1].id)]


# ─────────────────────────── 인증 ───────────────────────────


def test_37_missing_header_returns_401(client, db):
    """#37: X-User-Id 헤더가 없으면 401 UNAUTHORIZED 다.

    축: 회귀 — STUB 라우트가 이미 강제한다. 인증 의존성이 빠지는 회귀를 막는다.
    """
    response = client.get(URL, params={"date": D})

    _assert_error(response, 401, "UNAUTHORIZED")


def test_38_malformed_header_returns_401(client, db):
    """#38: X-User-Id 가 UUID 형식이 아니면 401 UNAUTHORIZED 다.

    축: 회귀 — STUB 라우트가 이미 강제한다.
    """
    response = client.get(URL, params={"date": D}, headers={"X-User-Id": "not-a-uuid"})

    _assert_error(response, 401, "UNAUTHORIZED")


def test_39_unknown_user_returns_404_and_creates_no_task(client, db):
    """#39: 없는 사용자면 404 USER_NOT_FOUND 이고 작업 수는 그대로다.

    그 uuid 로 대기 작업이 있어도 GENERATING 으로 새지 않는다 — 사용자 확인이 먼저다.
    """
    unknown = uuid.uuid4()
    _put_task(db, user_id=unknown, status=TaskStatus.PENDING)

    response = client.get(URL, params={"date": D}, headers={"X-User-Id": str(unknown)})

    _assert_error(response, 404, "USER_NOT_FOUND")
    assert _count_tasks(db) == 1


# ─────────────────────────── 요청 검증 ───────────────────────────


def test_40_missing_date_returns_422(client, db):
    """#40: date 는 필수다 — 없으면 422 VALIDATION_ERROR (게이트 1 결정 1).

    축: 회귀 — STUB 라우트의 Query(...) 가 이미 강제한다.
    """
    user = make_user(db)

    response = client.get(URL, headers=_headers(user))

    _assert_error(response, 422, "VALIDATION_ERROR")


def test_41_invalid_date_returns_422(client, db):
    """#41: 존재하지 않는 날짜(2026-13-01)는 422 VALIDATION_ERROR 다.

    축: 회귀 — STUB 라우트의 date 타입이 이미 강제한다.
    """
    user = make_user(db)

    response = _get(client, user, date="2026-13-01")

    _assert_error(response, 422, "VALIDATION_ERROR")


def test_42_future_date_returns_422(client, db):
    """#42: KST 오늘보다 미래 날짜는 422 VALIDATION_ERROR 다 (refresh 와 같은 규칙)."""
    user = make_user(db)

    response = _get(client, user, date="2026-08-22")

    _assert_error(response, 422, "VALIDATION_ERROR")


def test_43_today_kst_is_accepted(client, db):
    """#43: KST 오늘 날짜는 받는다 — 미래 거부(#42)가 오늘까지 막으면 안 된다."""
    user = make_user(db)

    data = _data(_get(client, user, date=D))

    assert data["feedbackStatus"] == "PENDING"


def test_44_unknown_user_with_future_date_returns_404_first(client, db):
    """#44: 없는 사용자 + 미래 날짜면 422 가 아니라 404 USER_NOT_FOUND 가 먼저다 (refresh 와 같은 순서)."""
    response = client.get(
        URL, params={"date": "2026-08-22"}, headers={"X-User-Id": str(uuid.uuid4())}
    )

    _assert_error(response, 404, "USER_NOT_FOUND")


# ─────────────────────────── crud ───────────────────────────


def test_45_get_for_day_returns_only_that_users_row_for_that_day(db):
    """#45: get_for_day 는 그 사용자·그 날짜의 행 1건만 돌려주고, 없는 날짜면 None 이다.

    user_id 나 날짜 조건이 빠지면 남의 행·다른 날 행이 섞인다.
    """
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    mine_d, _ = _make_daily_row(db, user)
    _make_daily_row(db, user, feedback_date=D_DATE - timedelta(days=1))
    _make_daily_row(db, other)

    found = daily_feedback_crud.get_for_day(db, user_id=user.id, feedback_date=D_DATE)
    missing = daily_feedback_crud.get_for_day(
        db, user_id=user.id, feedback_date=D_DATE - timedelta(days=2)
    )

    assert found is not None
    assert found.id == mine_d
    assert missing is None


def test_46_list_source_meal_ids_returns_only_this_rows_sources(db):
    """#46: list_source_meal_ids 는 이 행의 근거 끼니 meal_id 만 돌려준다 — 다른 행 근거는 섞이지 않는다."""
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    mine, my_meals = _make_daily_row(db, user)
    _make_daily_row(db, other)

    meal_ids = daily_feedback_crud.list_source_meal_ids(db, daily_feedback_id=mine)

    assert set(meal_ids) == {m.id for m in my_meals}
    assert len(meal_ids) == 2
