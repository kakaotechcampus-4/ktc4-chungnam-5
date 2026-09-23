"""GET /api/v1/user-states/latest — 가장 최근 컨디션 기록 1건 조회 API.

홈의 "오늘 컨디션 기록" 팝업이 열릴 때 체중과 "지난주 대비" 배지를 미리 채운다.
"최근"은 입력 순서가 아니라 recordedAt 기준이고, 응답은 같은 기록에 대한
POST /user-states 응답과 같은 모양·같은 계산이어야 한다.
401 선언 검사는 test_openapi_error_schema.py 의 AUTHENTICATED_ROUTES 가 맡는다.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.crud import user_state as user_state_crud
from app.main import app
from app.models.user import UserState
from app.tests.factories import make_user

URL = "/api/v1/user-states/latest"
POST_URL = "/api/v1/user-states"
T = datetime.fromisoformat("2026-08-21T21:30:00+09:00")
# created_at 기준점. 값 자체는 의미가 없고 두 행 사이의 선후만 본다.
T0 = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)

PROFILE_BODY = {
    "nickname": "종호",
    "heightCm": 174.0,
    "weightKg": 79.0,
    "baselineIntake": 700,
}


# ─────────────────────────── helper ───────────────────────────


def _headers(user) -> dict:
    return {"X-User-Id": str(user.id)}


def _state(db, user, recorded_at: datetime, weight: str = "78.40", **kwargs) -> UserState:
    return user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=Decimal(weight),
        recorded_at=recorded_at,
        **kwargs,
    )


def _state_with_created_at(
    db, user, *, recorded_at: datetime, created_at: datetime
) -> UserState:
    """created_at 을 직접 준 기록.

    created_at 은 server_default=now() 이고 Postgres now() 는 트랜잭션 시작 시각이다.
    db 픽스처는 테스트 하나를 한 트랜잭션으로 감싸므로 그냥 만들면 전부 같은 값이 된다.
    """
    state = UserState(
        user_id=user.id,
        weight_kg=Decimal("78.40"),
        recorded_at=recorded_at,
        gi_symptoms=[],
        created_at=created_at,
    )
    db.add(state)
    db.flush()
    return state


def _count_states(db, user_id) -> int:
    return db.execute(
        select(func.count()).select_from(UserState).where(UserState.user_id == user_id)
    ).scalar_one()


def _row_values(db, user_id) -> list[tuple]:
    """DB 에 저장된 내 기록의 내용 컬럼. identity map 이 아니라 SELECT 결과로 읽는다."""
    return [
        tuple(row)
        for row in db.execute(
            select(
                UserState.id,
                UserState.weight_kg,
                UserState.appetite_level,
                UserState.gi_symptoms,
                UserState.note,
                UserState.recorded_at,
            ).where(UserState.user_id == user_id)
        )
    ]


def _get_ok(client, headers) -> dict:
    """200 성공 래퍼를 확인하고 data 를 돌려준다."""
    response = client.get(URL, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    return body["data"]


# ─────────────────────────── 최근 기록 선택 ───────────────────────────


def test_returns_my_record_with_latest_recorded_at(client, db):
    """recordedAt 이 가장 늦은 내 기록을 돌려준다.

    가장 늦은 기록을 가운데 입력해 "마지막 입력"이나 "첫 입력"으로는 맞출 수 없게 한다.
    """
    user = make_user(db)
    _state(db, user, T - timedelta(days=3), "79.00")
    latest = _state(db, user, T, "78.40")
    _state(db, user, T - timedelta(days=1), "78.80")

    data = _get_ok(client, _headers(user))

    assert data["userStateId"] == str(latest.id)
    assert data["weightKg"] == 78.4


def test_later_input_with_earlier_recorded_at_is_not_latest(client, db):
    """나중에 입력했지만 recordedAt 이 더 이른 기록은 최근이 아니다.

    어제 기록을 오늘 뒤늦게 입력해도 팝업에는 오늘 기록이 떠야 한다 — 기준은 입력 순서가 아니다.
    """
    user = make_user(db)
    a = _state_with_created_at(db, user, recorded_at=T, created_at=T0)
    _state_with_created_at(
        db, user, recorded_at=T - timedelta(days=1), created_at=T0 + timedelta(hours=1)
    )

    data = _get_ok(client, _headers(user))

    assert data["userStateId"] == str(a.id)


@pytest.mark.parametrize("later_first", [True, False], ids=["later-inserted-first", "later-inserted-last"])
def test_same_recorded_at_returns_later_created_at(client, db, later_first):
    """recordedAt 이 같으면 created_at 이 늦은 기록을 돌려준다 (D2).

    UUID 는 순서가 없으므로 삽입 순서와 무관하게 created_at 으로 결정돼야 한다.
    """
    user = make_user(db)
    if later_first:
        later = _state_with_created_at(
            db, user, recorded_at=T, created_at=T0 + timedelta(seconds=1)
        )
        _state_with_created_at(db, user, recorded_at=T, created_at=T0)
    else:
        _state_with_created_at(db, user, recorded_at=T, created_at=T0)
        later = _state_with_created_at(
            db, user, recorded_at=T, created_at=T0 + timedelta(seconds=1)
        )

    data = _get_ok(client, _headers(user))

    assert data["userStateId"] == str(later.id)


# ─────────────────────────── 사용자 격리 ───────────────────────────


def test_other_users_later_record_is_not_returned(client, db):
    """다른 사용자의 기록은 recordedAt 이 더 늦어도 나오지 않는다.

    남의 체중이 내 팝업에 미리 채워지면 개인정보 노출이다.
    """
    me = make_user(db, nickname="나")
    other = make_user(db, nickname="타인")
    mine = _state(db, me, T)
    _state(db, other, T + timedelta(days=1))

    data = _get_ok(client, _headers(me))

    assert data["userStateId"] == str(mine.id)


def test_user_filter_does_not_hide_own_latest_record(client, db):
    """사용자 필터가 본인의 최신 기록까지 가리지 않는다 (위 테스트의 짝).

    거르는 조건이 지나쳐 모든 사용자에게 같은 행(또는 null)을 주면 안 된다.
    """
    me = make_user(db, nickname="나")
    other = make_user(db, nickname="타인")
    _state(db, me, T)
    theirs = _state(db, other, T + timedelta(days=1))

    data = _get_ok(client, _headers(other))

    assert data["userStateId"] == str(theirs.id)


# ─────────────────────────── 응답 내용 ───────────────────────────


def test_response_equals_post_response_for_same_record(client, db):
    """응답이 같은 기록에 대한 POST /user-states 응답과 같다.

    FE 는 두 응답을 같은 타입으로 다룬다. 필드 하나라도 다르면 화면이 어긋난다.
    """
    user = make_user(db)
    _state(db, user, T - timedelta(days=8), "79.00")
    post = client.post(
        POST_URL,
        json={
            "weightKg": 78.4,
            "appetiteLevel": 3,
            "giSymptoms": [{"code": "NAUSEA", "severity": "MODERATE"}],
            "note": None,
            "recordedAt": "2026-08-21T21:30:00+09:00",
        },
        headers=_headers(user),
    )
    assert post.status_code == 201, post.text
    post_data = post.json()["data"]

    data = _get_ok(client, _headers(user))

    assert data == post_data
    assert data["weightChangeKg"] == -0.6


def test_weight_change_is_based_on_record_recorded_at(client, db):
    """weightChangeKg 는 조회 시각이 아니라 그 기록의 recordedAt 기준이다.

    조회 시각(서버 now) 기준으로 계산하면 과거 기록의 배지가 0 이나 null 로 바뀐다.
    """
    user = make_user(db)
    _state(db, user, T - timedelta(days=8), "79.00")
    _state(db, user, T, "78.40")

    data = _get_ok(client, _headers(user))

    assert data["weightChangeKg"] == -0.6


def test_profile_first_record_is_returned_as_is(client, db):
    """프로필 생성이 남긴 첫 기록만 있으면 그 기록을 그대로 돌려준다.

    가입 직후 팝업은 온보딩 체중으로 채워진다. 식욕·증상이 없다고 거르면 안 된다.
    """
    created = client.post("/api/v1/users/profile", json=PROFILE_BODY)
    assert created.status_code == 201, created.text
    user_id = uuid.UUID(created.json()["data"]["userId"])
    only_row_id = db.execute(
        select(UserState.id).where(UserState.user_id == user_id)
    ).scalar_one()

    data = _get_ok(client, {"X-User-Id": str(user_id)})

    assert data["userStateId"] == str(only_row_id)
    assert data["weightKg"] == 79.0
    assert data["appetiteLevel"] is None
    assert data["giSymptoms"] == []
    assert data["note"] is None
    assert data["weightChangeKg"] is None
    assert data["weightChangeBaseline"] == "LAST_WEEK"


def test_utc_recorded_at_is_returned_in_kst(client, db):
    """UTC 로 저장된 recordedAt 을 +09:00 으로 돌려준다 (팀 관례)."""
    user = make_user(db)
    _state(db, user, datetime.fromisoformat("2026-08-21T12:30:00+00:00"))

    data = _get_ok(client, _headers(user))

    assert data["recordedAt"] == "2026-08-21T21:30:00+09:00"


def test_no_records_returns_200_with_null_data(client, db):
    """기록이 하나도 없으면 200 과 data null 이다 (D1).

    프로필 없이 사용자가 생기는 경로(카카오 로그인 등)에서 팝업은 빈 값으로 열린다.
    """
    user = make_user(db)

    data = _get_ok(client, _headers(user))

    assert data is None


def test_get_does_not_create_or_modify_records(client, db):
    """조회는 기록을 만들거나 바꾸지 않는다.

    팝업을 열 때마다 기록이 생기면 체중 추이가 오염된다.
    """
    user = make_user(db)
    _state(
        db,
        user,
        T,
        appetite_level=3,
        gi_symptoms=[{"code": "NAUSEA", "severity": "MODERATE"}],
        note="메모",
    )
    before = _row_values(db, user.id)

    first = _get_ok(client, _headers(user))
    second = _get_ok(client, _headers(user))

    assert _count_states(db, user.id) == 1
    assert _row_values(db, user.id) == before
    assert first == second


# ─────────────────────────── 인증 · 사용자 ───────────────────────────


def test_missing_user_header_is_401(client):
    """X-User-Id 가 없으면 401 UNAUTHORIZED 다."""
    response = client.get(URL)
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_malformed_user_header_is_401(client):
    """X-User-Id 형식이 틀리면 401 UNAUTHORIZED 다."""
    response = client.get(URL, headers={"X-User-Id": "not-a-uuid"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_unknown_user_is_404(client):
    """없는 사용자면 404 USER_NOT_FOUND 다.

    "기록 없음"(200 + null)과 섞이면 FE 가 없는 사용자를 신규 사용자로 오인한다.
    """
    response = client.get(URL, headers={"X-User-Id": str(uuid.uuid4())})

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "USER_NOT_FOUND"


# ─────────────────────────── 로그 (규칙 6) ───────────────────────────

SECRET_WEIGHT = "123.45"
SECRET_NOTE = "비밀메모"
SECRET_CODE = "NAUSEA"


def test_success_does_not_log_health_values(client, db, caplog):
    """성공한 조회는 체중·증상·메모를 로그에 남기지 않는다 (README 규칙 6)."""
    caplog.set_level(logging.DEBUG)
    user = make_user(db)
    _state(
        db,
        user,
        T,
        SECRET_WEIGHT,
        note=SECRET_NOTE,
        gi_symptoms=[{"code": SECRET_CODE, "severity": "MILD"}],
    )

    data = _get_ok(client, _headers(user))

    assert data["note"] == SECRET_NOTE
    assert SECRET_NOTE not in caplog.text
    assert SECRET_WEIGHT not in caplog.text
    assert SECRET_CODE not in caplog.text


# ─────────────────────────── OpenAPI 문서 ───────────────────────────


@pytest.fixture(scope="module")
def schema() -> dict:
    return app.openapi()


def test_openapi_declares_404(schema):
    """문서의 GET /api/v1/user-states/latest 에 404 가 선언돼 있다.

    401 선언은 test_openapi_error_schema.py 의 AUTHENTICATED_ROUTES 가 검사한다.
    """
    assert "404" in schema["paths"][URL]["get"]["responses"]
