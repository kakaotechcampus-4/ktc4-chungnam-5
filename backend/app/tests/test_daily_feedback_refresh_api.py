"""POST /api/v1/insights/daily/refresh — 하루 피드백 재생성 요청 API.

이 API 는 작업(feedback.daily)만 큐에 넣고 202 를 돌려준다. 실제 생성은 워커가 한다
(`test_feedback_daily_job.py`). 실패 경로는 FE 가 분기하는 error.code 까지 단언하고,
어떤 실패든 작업이 쌓이지 않았는지(Task 0행)를 함께 본다.
"""

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from app.core.time import KST
from app.crud.daily_feedback import get_latest_refresh_task
from app.models.enums import TaskStatus
from app.models.feedback import DailyFeedback
from app.models.task import Task
from app.tests.factories import make_meal, make_meal_feedback, make_qqs_evaluation, make_user

URL = "/api/v1/insights/daily/refresh"
D = "2026-08-21"
TASK_TYPE = "feedback.daily"


# ─────────────────────────── helper ───────────────────────────


def _headers(user) -> dict:
    return {"X-User-Id": str(user.id)}


def _tasks(db) -> list[Task]:
    db.expire_all()
    return list(db.execute(select(Task).where(Task.type == TASK_TYPE)).scalars())


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
    type: str = TASK_TYPE,
    payload: dict | None = None,
    created_at: datetime | None = None,
) -> Task:
    """task_queue 에 작업 1행을 직접 심는다. flush 까지만.

    같은 트랜잭션 안에서는 now() 가 고정되므로, 순서가 판정에 영향을 주면 created_at 을 명시한다.
    """
    extra = {"created_at": created_at} if created_at is not None else {}
    task = Task(
        type=type,
        payload=payload if payload is not None else {"userId": str(user_id), "date": date},
        status=status,
        **extra,
    )
    db.add(task)
    db.flush()
    return task


def _add_safe_evidence(db, user) -> None:
    """그날(D, KST 점심) SAFE 근거 1건 — 식사 + Q/Q/S + SAFE 끼니 피드백."""
    meal = make_meal(db, user_id=user.id, eaten_at=datetime(2026, 8, 21, 12, 30, tzinfo=KST))
    make_qqs_evaluation(db, meal_id=meal.id)
    make_meal_feedback(db, user_id=user.id, meal_id=meal.id)


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == code


@pytest.fixture
def frozen_today(monkeypatch):
    """KST 오늘을 D(2026-08-21)로 고정한다.

    `today_kst` 를 어느 모듈이 `from … import` 해 가든 잡히도록, 함수 자체가 아니라
    그 함수가 읽는 `app.core.time.datetime` 을 바꾼다 — 함수 본문은 호출 시점에
    자기 모듈의 전역 `datetime` 을 찾는다.
    """

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("app.core.time.datetime", _FrozenDatetime)


# ─────────────────────────── 정상 ───────────────────────────


def test_valid_request_returns_202_generating_with_poll_interval(client, db):
    """A1: 유효한 요청이면 202 와 GENERATING · pollIntervalMs 1500 을 돌려준다.

    FE 는 이 값으로 폴링을 시작한다 — 장기 피드백(#46)과 같은 모양이다.
    """
    user = make_user(db)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {"feedbackStatus": "GENERATING", "pollIntervalMs": 1500}


def test_response_has_no_timeout_ms(client, db):
    """A2: 응답에 timeoutMs 를 싣지 않는다 (#46 과 동일한 계약)."""
    user = make_user(db)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    assert "timeoutMs" not in response.json()["data"]


def test_valid_request_enqueues_exactly_one_feedback_daily_task(client, db):
    """A3: feedback.daily 작업이 정확히 1건, payload 는 userId · date 뿐, 상태는 PENDING 이다.

    payload 키는 워커(jobs/feedback_daily.py)가 읽는 이름과 같아야 한다.
    """
    user = make_user(db)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    tasks = _tasks(db)
    assert len(tasks) == 1
    assert tasks[0].type == TASK_TYPE
    assert tasks[0].payload == {"userId": str(user.id), "date": D}
    assert tasks[0].status == TaskStatus.PENDING


def test_refresh_does_not_create_daily_feedback_directly(client, db):
    """A4: refresh 는 daily_feedbacks 를 직접 만들지 않는다 — 생성은 워커 몫이다.

    API 가 AI 를 부르거나 행을 만들면 요청이 10초 넘게 걸리고 워커와 중복된다.
    """
    user = make_user(db)
    _add_safe_evidence(db, user)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    count = db.execute(
        select(func.count()).select_from(DailyFeedback).where(DailyFeedback.user_id == user.id)
    ).scalar_one()
    assert count == 0


def test_payload_user_id_is_the_header_user(client, db):
    """A13: 작업 payload 의 userId 는 요청 헤더의 사용자다 — 다른 사용자가 섞이지 않는다."""
    make_user(db, nickname="첫째")
    user2 = make_user(db, nickname="둘째")

    response = client.post(URL, json={"date": D}, headers=_headers(user2))

    assert response.status_code == 202, response.text
    tasks = _tasks(db)
    assert len(tasks) == 1
    assert tasks[0].payload["userId"] == str(user2.id)


def test_two_requests_same_day_enqueue_only_one_task(client, db):
    """R1(A14 교체): 같은 날 두 번 요청하면 둘 다 202 GENERATING 이지만 작업은 1건만 쌓인다.

    첫 작업이 아직 PENDING 이라 두 번째 요청은 새로 넣지 않는다 — 새로고침 연타마다
    AI 를 여러 번 부르지 않고, 워커 여러 대가 같은 행을 동시에 upsert 하지 않게 한다 (#46 과 같은 결론).
    """
    user = make_user(db)

    first = client.post(URL, json={"date": D}, headers=_headers(user))
    second = client.post(URL, json={"date": D}, headers=_headers(user))

    for response in (first, second):
        assert response.status_code == 202, response.text
        assert response.json()["data"]["feedbackStatus"] == "GENERATING"
    assert _count_tasks(db) == 1


def test_no_evidence_still_returns_202_and_enqueues(client, db):
    """A15': 그날 근거가 없어도 409 가 아니라 202 이고 작업이 등록된다 (D6(b)).

    근거 판단은 워커가 한다 — 근거가 없으면 워커가 행을 만들지 않고 끝난다.
    """
    user = make_user(db)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    assert _count_tasks(db) == 1


# ─────────────────────────── 인증 ───────────────────────────


def test_missing_header_returns_401_and_enqueues_nothing(client, db):
    """A5: X-User-Id 헤더가 없으면 401 UNAUTHORIZED 이고 작업을 넣지 않는다.

    축: 회귀 — STUB 시그니처가 이미 강제한다. 라우트·스키마가 빠지거나 느슨해지는 회귀를 막는다.
    """
    response = client.post(URL, json={"date": D})

    _assert_error(response, 401, "UNAUTHORIZED")
    assert _count_tasks(db) == 0


def test_malformed_header_returns_401_and_enqueues_nothing(client, db):
    """A6: X-User-Id 가 UUID 형식이 아니면 401 UNAUTHORIZED 이고 작업을 넣지 않는다.

    축: 회귀 — STUB 시그니처가 이미 강제한다. 라우트·스키마가 빠지거나 느슨해지는 회귀를 막는다.
    """
    response = client.post(URL, json={"date": D}, headers={"X-User-Id": "not-a-uuid"})

    _assert_error(response, 401, "UNAUTHORIZED")
    assert _count_tasks(db) == 0


def test_unknown_user_returns_404_user_not_found_and_enqueues_nothing(client, db):
    """A7: 없는 사용자면 404 USER_NOT_FOUND 이고 작업을 넣지 않는다.

    작업이 들어가면 워커가 존재하지 않는 사용자로 AI 를 부르게 된다.
    """
    response = client.post(URL, json={"date": D}, headers={"X-User-Id": str(uuid.uuid4())})

    _assert_error(response, 404, "USER_NOT_FOUND")
    assert _count_tasks(db) == 0


# ─────────────────────────── 요청 검증 ───────────────────────────


def test_unknown_field_returns_422(client, db):
    """A8: 오타 필드(dat)가 섞이면 422 VALIDATION_ERROR — 조용히 무시하지 않는다.

    축: 회귀 — STUB 시그니처가 이미 강제한다. 라우트·스키마가 빠지거나 느슨해지는 회귀를 막는다.
    """
    user = make_user(db)

    response = client.post(URL, json={"date": D, "dat": D}, headers=_headers(user))

    _assert_error(response, 422, "VALIDATION_ERROR")
    assert _count_tasks(db) == 0


def test_invalid_date_format_returns_422(client, db):
    """A9: 존재하지 않는 날짜(2026-13-01)는 422 VALIDATION_ERROR 이고 작업을 넣지 않는다.

    축: 회귀 — STUB 시그니처가 이미 강제한다. 라우트·스키마가 빠지거나 느슨해지는 회귀를 막는다.
    """
    user = make_user(db)

    response = client.post(URL, json={"date": "2026-13-01"}, headers=_headers(user))

    _assert_error(response, 422, "VALIDATION_ERROR")
    assert _count_tasks(db) == 0


def test_missing_date_returns_422(client, db):
    """A10: date 는 필수다 — 없으면 422 VALIDATION_ERROR 이고 작업을 넣지 않는다 (D8).

    축: 회귀 — STUB 시그니처가 이미 강제한다. 라우트·스키마가 빠지거나 느슨해지는 회귀를 막는다.
    """
    user = make_user(db)

    response = client.post(URL, json={}, headers=_headers(user))

    _assert_error(response, 422, "VALIDATION_ERROR")
    assert _count_tasks(db) == 0


def test_future_date_returns_422(client, db, frozen_today):
    """A11: KST 오늘보다 미래 날짜는 422 VALIDATION_ERROR 이고 작업을 넣지 않는다 (D8).

    아직 오지 않은 날의 피드백은 만들 근거가 없다.
    """
    user = make_user(db)

    response = client.post(URL, json={"date": "2026-08-22"}, headers=_headers(user))

    _assert_error(response, 422, "VALIDATION_ERROR")
    assert _count_tasks(db) == 0


def test_today_kst_is_accepted(client, db, frozen_today):
    """A12: KST 오늘 날짜는 받는다 — 미래 거부(A11)가 오늘까지 막으면 안 된다."""
    user = make_user(db)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    assert _count_tasks(db) == 1


# ─────────────────────────── 중복 요청 방지 ───────────────────────────


def test_pending_task_blocks_new_enqueue_and_returns_generating(client, db):
    """R2: 같은 사용자·날짜의 대기(PENDING) 작업이 있으면 새로 넣지 않고 202 GENERATING 을 준다.

    이미 워커가 처리할 작업이 있다 — 하나 더 넣으면 AI 호출만 늘고 결과는 같다.
    """
    user = make_user(db)
    pending = _put_task(db, user_id=user.id)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {"feedbackStatus": "GENERATING", "pollIntervalMs": 1500}
    tasks = _tasks(db)
    assert [task.id for task in tasks] == [pending.id]


def test_latest_pending_blocks_even_with_older_done(client, db):
    """R3: 최신 작업이 PENDING 이면 그보다 오래된 DONE 이 있어도 새로 넣지 않는다.

    판정은 가장 최근 작업 하나로 한다 — 오래된 DONE 을 보고 새로 넣으면 중복이 생긴다.
    """
    user = make_user(db)
    _put_task(
        db, user_id=user.id, status=TaskStatus.DONE,
        created_at=datetime(2026, 8, 21, 9, 0, tzinfo=KST),
    )
    _put_task(
        db, user_id=user.id, status=TaskStatus.PENDING,
        created_at=datetime(2026, 8, 21, 10, 0, tzinfo=KST),
    )

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    assert response.json()["data"]["feedbackStatus"] == "GENERATING"
    assert _count_tasks(db) == 2


def test_older_pending_does_not_block_when_latest_is_done(client, db):
    """R17: 오래된 PENDING 이 남아 있어도 최신 작업이 DONE 이면 새로 넣는다.

    R3 의 짝이다 — "PENDING 이 하나라도 있으면 막는다"로 구현하면 여기서 걸린다.
    판정은 최신 작업 하나의 상태로 한다 (이슈: `latest.status == PENDING`).
    """
    user = make_user(db)
    _put_task(
        db, user_id=user.id, status=TaskStatus.PENDING,
        created_at=datetime(2026, 8, 21, 9, 0, tzinfo=KST),
    )
    _put_task(
        db, user_id=user.id, status=TaskStatus.DONE,
        created_at=datetime(2026, 8, 21, 10, 0, tzinfo=KST),
    )

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    assert response.json()["data"]["feedbackStatus"] == "GENERATING"
    assert _count_tasks(db) == 3


@pytest.mark.parametrize(
    "previous_status",
    [
        pytest.param(TaskStatus.DONE, id="R4-done"),
        pytest.param(TaskStatus.FAILED, id="R5-failed"),
    ],
)
def test_finished_previous_task_allows_new_enqueue(client, db, previous_status):
    """R4·R5: 직전 작업이 DONE 이거나 FAILED 면 새 작업을 넣는다.

    끝난 작업까지 막으면 사용자가 다시 생성할 방법이 없다 (R2·R3 의 짝).
    """
    user = make_user(db)
    _put_task(db, user_id=user.id, status=previous_status)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    tasks = _tasks(db)
    assert len(tasks) == 2
    pending = [task for task in tasks if task.status == TaskStatus.PENDING]
    assert len(pending) == 1
    assert pending[0].payload == {"userId": str(user.id), "date": D}


def test_other_users_pending_task_does_not_block(client, db):
    """R6: 다른 사용자의 같은 날 PENDING 작업은 내 요청을 막지 않는다.

    userId 조건이 빠지면 한 사용자의 새로고침이 모든 사용자를 막는다.
    """
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _put_task(db, user_id=other.id)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    mine = [
        task for task in _tasks(db)
        if task.payload["userId"] == str(user.id) and task.status == TaskStatus.PENDING
    ]
    assert len(mine) == 1


def test_my_other_date_pending_task_does_not_block(client, db):
    """R7: 내 다른 날짜의 PENDING 작업은 이 날짜의 요청을 막지 않는다.

    date 조건이 빠지면 어제 피드백을 생성 중일 때 오늘 피드백을 요청할 수 없다.
    """
    user = make_user(db)
    _put_task(db, user_id=user.id, date="2026-08-20")

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    today = [
        task for task in _tasks(db)
        if task.payload["date"] == D and task.status == TaskStatus.PENDING
    ]
    assert len(today) == 1


def test_other_type_pending_task_does_not_block(client, db):
    """R8: 다른 타입(feedback.long)의 PENDING 작업은 하루 피드백 요청을 막지 않는다.

    type 조건이 빠지면 장기 피드백 생성 중에 하루 피드백을 요청할 수 없다.
    payload 에 date 까지 같게 넣는다 — 없으면 date 조건이 대신 걸러 type 누락을 못 잡는다.
    """
    user = make_user(db)
    _put_task(
        db, user_id=user.id, type="feedback.long",
        payload={"userId": str(user.id), "date": D, "periodType": "WEEKLY"},
    )

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    assert _count_tasks(db) == 1


def test_unknown_user_with_pending_task_still_returns_404(client, db):
    """R11: 대기 작업이 있어도 없는 사용자면 404 USER_NOT_FOUND 이고 작업 수는 그대로다.

    축: 회귀 — 중복 판정이 사용자 확인보다 먼저 와서 202 로 새는 것을 막는다.
    """
    unknown = uuid.uuid4()
    _put_task(db, user_id=unknown)

    response = client.post(URL, json={"date": D}, headers={"X-User-Id": str(unknown)})

    _assert_error(response, 404, "USER_NOT_FOUND")
    assert _count_tasks(db) == 1


def test_future_date_with_pending_task_still_returns_422(client, db, frozen_today):
    """R12: 대기 작업이 있어도 미래 날짜면 422 VALIDATION_ERROR 이고 작업 수는 그대로다.

    축: 회귀 — 중복 판정이 날짜 검증보다 먼저 와서 202 로 새는 것을 막는다.
    """
    user = make_user(db)
    _put_task(db, user_id=user.id, date="2026-08-22")

    response = client.post(URL, json={"date": "2026-08-22"}, headers=_headers(user))

    _assert_error(response, 422, "VALIDATION_ERROR")
    assert _count_tasks(db) == 1


# ─────────────────────────── crud: get_latest_refresh_task ───────────────────────────


def test_get_latest_refresh_task_returns_most_recent_for_user_and_date(db):
    """R9: 같은 사용자·날짜의 feedback.daily 작업 중 가장 최근에 등록된 1건을 돌려준다.

    다른 사용자·다른 날짜·다른 타입의 더 최근 작업은 고르지 않는다.
    """
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _put_task(
        db, user_id=user.id, status=TaskStatus.PENDING,
        created_at=datetime(2026, 8, 21, 9, 0, tzinfo=KST),
    )
    latest = _put_task(
        db, user_id=user.id, status=TaskStatus.FAILED,
        created_at=datetime(2026, 8, 21, 10, 0, tzinfo=KST),
    )
    newer = datetime(2026, 8, 21, 11, 0, tzinfo=KST)
    _put_task(db, user_id=other.id, created_at=newer)
    _put_task(db, user_id=user.id, date="2026-08-20", created_at=newer)
    _put_task(
        db, user_id=user.id, type="feedback.long",
        payload={"userId": str(user.id), "date": D}, created_at=newer,
    )

    found = get_latest_refresh_task(db, user_id=user.id, feedback_date=date(2026, 8, 21))

    assert found is not None
    assert found.id == latest.id


def test_get_latest_refresh_task_returns_none_when_no_match(db):
    """R10: 이 사용자·날짜의 feedback.daily 작업이 없으면 None 이다 — 남의 작업을 대신 돌려주지 않는다."""
    user = make_user(db, nickname="나")
    other = make_user(db, nickname="남")
    _put_task(db, user_id=other.id)
    _put_task(db, user_id=user.id, date="2026-08-20")

    found = get_latest_refresh_task(db, user_id=user.id, feedback_date=date(2026, 8, 21))

    assert found is None
