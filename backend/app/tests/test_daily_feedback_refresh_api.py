"""POST /api/v1/insights/daily/refresh — 하루 피드백 재생성 요청 API.

이 API 는 작업(feedback.daily)만 큐에 넣고 202 를 돌려준다. 실제 생성은 워커가 한다
(`test_feedback_daily_job.py`). 실패 경로는 FE 가 분기하는 error.code 까지 단언하고,
어떤 실패든 작업이 쌓이지 않았는지(Task 0행)를 함께 본다.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.core.time import KST
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
    assert body["data"] == {"status": "GENERATING", "pollIntervalMs": 1500}


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


def test_two_requests_same_day_enqueue_two_tasks(client, db):
    """A14: 같은 날 두 번 요청하면 둘 다 202 GENERATING 이고 작업이 2건 쌓인다 (중복 허용, D7(a)).

    워커가 upsert 라 두 번 돌아도 행은 하나다 (W14).
    """
    user = make_user(db)

    first = client.post(URL, json={"date": D}, headers=_headers(user))
    second = client.post(URL, json={"date": D}, headers=_headers(user))

    for response in (first, second):
        assert response.status_code == 202, response.text
        assert response.json()["data"]["status"] == "GENERATING"
    assert _count_tasks(db) == 2


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
