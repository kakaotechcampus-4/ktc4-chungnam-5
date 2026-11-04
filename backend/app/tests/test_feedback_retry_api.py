"""POST /meals/{mealId}/feedback/retry — FAILED 인 끼니 피드백을 다시 만든다.

식사 안 함 끼니는 확정(`POST /confirm`)이 막혀 있어(채점할 음식이 없다) 이게 유일한
재시도 길이다 (PR 리뷰). 일반 끼니에도 쓸 수 있다.

틀리기 쉬운 것:
  - 이미 문장이 있는데 다시 만들어 멀쩡한 문장 · 챗봇으로 바꾼 추천을 잃는 것
  - 생성 중인데 또 넣어 더블탭이 AI 를 두 번 부르는 것
  - 재시도가 점수 · 포만감을 건드리는 것
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud import feedback as feedback_crud
from app.models.enums import MealStatus, SafetyStatus, TaskStatus
from app.models.evaluation import QQSEvaluation
from app.models.meal import SatietyLog
from app.models.task import Task
from app.tests.factories import make_meal, make_meal_feedback, make_qqs_evaluation, make_user


def _h(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def _retry(client, user_id, meal_id):
    return client.post(f"/api/v1/meals/{meal_id}/feedback/retry", headers=_h(user_id))


def _skipped_meal(db: Session, user_id):
    """식사 안 함 끼니 — EVALUATED · skipped · 점수 행 없음."""
    meal = make_meal(db, user_id=user_id, status=MealStatus.EVALUATED)
    meal.skipped = True
    meal.raw_text = None
    db.flush()
    return meal


def _add_task(db: Session, meal, status: TaskStatus) -> None:
    """앞서 돈 작업. 시각을 과거로 둔다 — 테스트는 한 트랜잭션이라 `now()` 가 같아서, 그대로
    두면 재시도가 넣은 작업과 "가장 최근" 이 갈리지 않는다(`latest_task_status` 독스트링)."""
    db.add(
        Task(
            type=feedback_crud.FEEDBACK_TASK_TYPE,
            payload={"mealId": str(meal.id)},
            status=status,
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    db.flush()


def _feedback_tasks(db: Session, meal) -> list[Task]:
    db.expire_all()
    return list(
        db.execute(
            select(Task).where(
                Task.type == feedback_crud.FEEDBACK_TASK_TYPE,
                Task.payload["mealId"].astext == str(meal.id),
            )
        ).scalars()
    )


def _pending_count(db: Session, meal) -> int:
    return sum(1 for t in _feedback_tasks(db, meal) if t.status is TaskStatus.PENDING)


# ── 다시 만든다 ──────────────────────────────────────────────


@pytest.mark.parametrize("last", [TaskStatus.FAILED, TaskStatus.DONE])
def test_failed_skipped_meal_feedback_is_retried(client, db: Session, last) -> None:
    """리뷰 재현: 식사 안 함 끼니가 FAILED 면 확정으로는 되살릴 수 없었다."""
    user = make_user(db)
    meal = _skipped_meal(db, user.id)
    _add_task(db, meal, last)
    db.commit()
    assert client.get(
        f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)
    ).json()["data"]["feedbackStatus"] == "FAILED"

    res = _retry(client, user.id, meal.id)

    assert res.status_code == 200, res.text
    assert res.json()["data"]["feedbackStatus"] == "PENDING"
    assert _pending_count(db, meal) == 1


def test_failed_normal_meal_feedback_is_retried(client, db: Session) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    make_qqs_evaluation(db, meal_id=meal.id)
    _add_task(db, meal, TaskStatus.FAILED)
    db.commit()

    res = _retry(client, user.id, meal.id)

    assert res.json()["data"]["feedbackStatus"] == "PENDING"
    assert _pending_count(db, meal) == 1


def test_row_emptied_by_reconfirm_is_retried(client, db: Session) -> None:
    """행은 있지만 본문이 비었다 — 보여 줄 문장이 없는 것이다."""
    user = make_user(db)
    meal = _skipped_meal(db, user.id)
    make_meal_feedback(db, user_id=user.id, meal_id=meal.id, body=None)
    _add_task(db, meal, TaskStatus.DONE)
    db.commit()

    _retry(client, user.id, meal.id)

    assert _pending_count(db, meal) == 1


def test_retry_does_not_touch_scores_or_satiety(client, db: Session) -> None:
    user = make_user(db)
    meal = _skipped_meal(db, user.id)
    _add_task(db, meal, TaskStatus.FAILED)
    db.commit()

    _retry(client, user.id, meal.id)

    assert db.execute(select(func.count()).select_from(QQSEvaluation)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(SatietyLog)).scalar_one() == 0


# ── 아무것도 하지 않는다 ──────────────────────────────────────


@pytest.mark.parametrize("in_flight", [TaskStatus.PENDING, TaskStatus.PROCESSING])
def test_in_flight_task_is_not_doubled(client, db: Session, in_flight) -> None:
    """더블탭 · 생성 중 재시도 — AI 를 두 번 부르지 않는다."""
    user = make_user(db)
    meal = _skipped_meal(db, user.id)
    _add_task(db, meal, in_flight)
    db.commit()

    res = _retry(client, user.id, meal.id)

    assert res.status_code == 200, res.text
    assert len(_feedback_tasks(db, meal)) == 1


def test_retry_twice_enqueues_once(client, db: Session) -> None:
    user = make_user(db)
    meal = _skipped_meal(db, user.id)
    _add_task(db, meal, TaskStatus.FAILED)
    db.commit()

    _retry(client, user.id, meal.id)
    _retry(client, user.id, meal.id)

    assert _pending_count(db, meal) == 1


def test_ready_feedback_is_returned_as_is(client, db: Session) -> None:
    """문장이 있으면 다시 만들지 않는다 — 챗봇으로 바꾼 추천도 그대로다."""
    user = make_user(db)
    meal = _skipped_meal(db, user.id)
    make_meal_feedback(
        db, user_id=user.id, meal_id=meal.id, body="점심을 건너뛰었어요.",
        safety_status=SafetyStatus.SAFE,
        suggestions=[{"foodName": "쌈채소 한 접시", "advice": "바꾼 추천", "candidateFoodRefId": None}],
    )
    _add_task(db, meal, TaskStatus.DONE)
    db.commit()

    res = _retry(client, user.id, meal.id)

    data = res.json()["data"]
    assert data["feedbackStatus"] == "READY"
    assert data["summary"] == "점심을 건너뛰었어요."
    assert [s["foodName"] for s in data["suggestions"]] == ["쌈채소 한 접시"]
    assert _pending_count(db, meal) == 0


# ── 에러 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status", [MealStatus.ANALYZING, MealStatus.REVIEW_REQUIRED, MealStatus.FAILED]
)
def test_unconfirmed_meal_is_not_confirmed(client, db: Session, status) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=status)
    db.commit()

    res = _retry(client, user.id, meal.id)

    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "NOT_CONFIRMED"
    assert _feedback_tasks(db, meal) == []


def test_someone_elses_meal_is_404(client, db: Session) -> None:
    owner = make_user(db)
    other = make_user(db, nickname="다른 사람")
    meal = _skipped_meal(db, owner.id)
    db.commit()

    res = _retry(client, other.id, meal.id)

    assert res.status_code == 404, res.text
    assert _feedback_tasks(db, meal) == []


def test_unknown_meal_is_404(client, db: Session) -> None:
    user = make_user(db)
    db.commit()

    assert _retry(client, user.id, uuid.uuid4()).status_code == 404
