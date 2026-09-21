"""POST /api/v1/user-states — 컨디션(체중·식욕·GI 증상) 기록 API.

응답 래퍼 · camelCase · +09:00 직렬화 · 422/401/404 의 error.code 까지 함께 검증한다.
FE 는 HTTP status 가 아니라 error.code 로 분기하므로 실패 경로는 code 를 단언한다.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.main import app
from app.models.user import UserState
from app.tests.factories import make_user

URL = "/api/v1/user-states"
T = datetime.fromisoformat("2026-08-21T21:30:00+09:00")

VALID_BODY = {
    "weightKg": 78.4,
    "appetiteLevel": 3,
    "giSymptoms": [{"code": "NAUSEA", "severity": "MODERATE"}],
    "note": None,
    "recordedAt": "2026-08-21T21:30:00+09:00",
}

ALL_CODES = [
    "NAUSEA",
    "VOMITING",
    "HEARTBURN",
    "CONSTIPATION",
    "DIARRHEA",
    "BLOATING",
    "ABDOMINAL_PAIN",
]


# ─────────────────────────── helper ───────────────────────────


def _headers(user) -> dict:
    return {"X-User-Id": str(user.id)}


def _body(**overrides) -> dict:
    body = dict(VALID_BODY)
    body.update(overrides)
    return body


def _states(db, user_id) -> list[UserState]:
    # 같은 세션의 identity map 객체가 아니라 실제 저장된 값(Numeric(5,2)·JSONB)을 읽는다.
    db.expire_all()
    return list(
        db.execute(select(UserState).where(UserState.user_id == user_id)).scalars()
    )


def _count_states(db, user_id) -> int:
    return db.execute(
        select(func.count()).select_from(UserState).where(UserState.user_id == user_id)
    ).scalar_one()


def _assert_rejected(response, db, user_id) -> None:
    """422 공통 기대 — status 422, error.code VALIDATION_ERROR, 그 사용자의 행 0."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert _count_states(db, user_id) == 0


# ─────────────────────────── 정상 ───────────────────────────


def test_valid_request_returns_201_with_request_values(client, db):
    """A1: 유효한 요청이면 201 과 함께 요청값을 그대로 돌려준다.

    FE 는 응답으로 화면을 다시 그린다 — 값이 바뀌어 돌아오면 사용자가 입력한 것과 다르게 보인다.
    """
    user = make_user(db)
    response = client.post(URL, json=VALID_BODY, headers=_headers(user))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    assert data["weightKg"] == 78.4
    assert data["appetiteLevel"] == 3
    assert data["giSymptoms"] == [{"code": "NAUSEA", "severity": "MODERATE"}]
    assert data["note"] is None
    assert data["recordedAt"] == "2026-08-21T21:30:00+09:00"
    assert data["weightChangeBaseline"] == "LAST_WEEK"
    uuid.UUID(data["userStateId"])  # UUID 문자열이 아니면 ValueError


def test_valid_request_stores_exactly_one_row(client, db):
    """A2: 유효한 요청이면 내 user_states 가 정확히 1행 생기고 값이 요청과 같다."""
    user = make_user(db)
    response = client.post(URL, json=VALID_BODY, headers=_headers(user))
    assert response.status_code == 201, response.text

    rows = _states(db, user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.weight_kg == Decimal("78.40")
    assert row.appetite_level == 3
    assert row.gi_symptoms == [{"code": "NAUSEA", "severity": "MODERATE"}]
    assert row.recorded_at == T


def test_utc_recorded_at_is_returned_in_kst(client, db):
    """A3: UTC(Z) 로 보낸 recordedAt 도 +09:00 으로 응답한다 (팀 관례)."""
    user = make_user(db)
    response = client.post(
        URL, json=_body(recordedAt="2026-08-21T12:30:00Z"), headers=_headers(user)
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["recordedAt"] == "2026-08-21T21:30:00+09:00"


def test_omitted_recorded_at_uses_server_now(client, db):
    """A4: recordedAt 을 생략하면 서버 현재 시각으로 저장된다."""
    user = make_user(db)
    body = _body()
    del body["recordedAt"]

    before = datetime.now(timezone.utc)
    response = client.post(URL, json=body, headers=_headers(user))
    after = datetime.now(timezone.utc)

    assert response.status_code == 201, response.text
    rows = _states(db, user.id)
    assert len(rows) == 1
    assert before <= rows[0].recorded_at <= after
    assert response.json()["data"]["recordedAt"].endswith("+09:00")


def test_weight_only_request_leaves_other_fields_empty(client, db):
    """A5: weightKg 만 보내면 식욕·메모는 null, 증상은 빈 목록이다 ("증상 없음" = [])."""
    user = make_user(db)
    response = client.post(URL, json={"weightKg": 78.4}, headers=_headers(user))

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["appetiteLevel"] is None
    assert data["giSymptoms"] == []
    assert data["note"] is None

    rows = _states(db, user.id)
    assert len(rows) == 1
    assert rows[0].gi_symptoms == []


def test_note_is_stored_and_returned(client, db):
    """A6: note 문자열이 그대로 저장되고 그대로 돌아온다."""
    user = make_user(db)
    note = "저녁 후 더부룩함"
    response = client.post(URL, json=_body(note=note), headers=_headers(user))

    assert response.status_code == 201, response.text
    assert response.json()["data"]["note"] == note
    rows = _states(db, user.id)
    assert len(rows) == 1
    assert rows[0].note == note


# ─────────────────────────── 값 범위 (양방향) ───────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [{"weightKg": 500}, {"appetiteLevel": 1}, {"appetiteLevel": 5}],
    ids=["weight-500", "appetite-1", "appetite-5"],
)
def test_boundary_values_are_accepted(client, db, overrides):
    """A7: 경계값 weightKg 500 · appetiteLevel 1·5 는 허용한다.

    거름 규칙이 경계를 잘못 잡으면 정상 입력이 422 로 막힌다 (A8·A9 의 짝).
    """
    user = make_user(db)
    response = client.post(URL, json=_body(**overrides), headers=_headers(user))

    assert response.status_code == 201, response.text
    assert _count_states(db, user.id) == 1


@pytest.mark.parametrize("weight", [0, -1, 500.01], ids=["zero", "negative", "over-500"])
def test_out_of_range_weight_is_rejected(client, db, weight):
    """A8: weightKg 0 · -1 · 500.01 은 422 VALIDATION_ERROR 이고 저장하지 않는다."""
    user = make_user(db)
    response = client.post(URL, json=_body(weightKg=weight), headers=_headers(user))
    _assert_rejected(response, db, user.id)


@pytest.mark.parametrize("level", [0, 6, 2.5], ids=["zero", "six", "fraction"])
def test_out_of_range_appetite_is_rejected(client, db, level):
    """A9: appetiteLevel 0 · 6 · 2.5 는 422 VALIDATION_ERROR 이고 저장하지 않는다."""
    user = make_user(db)
    response = client.post(URL, json=_body(appetiteLevel=level), headers=_headers(user))
    _assert_rejected(response, db, user.id)


# ─────────────────────────── 증상 (양방향) ───────────────────────────


def test_all_symptom_codes_and_severities_are_accepted(client, db):
    """A10: 증상 7종과 강도 3종은 모두 허용하고 요청 그대로 돌려준다 (A11·A12 의 짝)."""
    user = make_user(db)
    severities = ["MILD", "MODERATE", "SEVERE"]
    symptoms = [
        {"code": code, "severity": severities[i % 3]} for i, code in enumerate(ALL_CODES)
    ]
    response = client.post(URL, json=_body(giSymptoms=symptoms), headers=_headers(user))

    assert response.status_code == 201, response.text
    assert response.json()["data"]["giSymptoms"] == symptoms


def test_unknown_symptom_code_is_rejected(client, db):
    """A11: 모르는 증상 code(HEADACHE) 는 422 VALIDATION_ERROR 이고 저장하지 않는다."""
    user = make_user(db)
    response = client.post(
        URL,
        json=_body(giSymptoms=[{"code": "HEADACHE", "severity": "MILD"}]),
        headers=_headers(user),
    )
    _assert_rejected(response, db, user.id)


def test_unknown_severity_is_rejected(client, db):
    """A12: 모르는 severity(LOW) 는 422 VALIDATION_ERROR 이고 저장하지 않는다."""
    user = make_user(db)
    response = client.post(
        URL,
        json=_body(giSymptoms=[{"code": "NAUSEA", "severity": "LOW"}]),
        headers=_headers(user),
    )
    _assert_rejected(response, db, user.id)


def test_missing_severity_is_rejected(client, db):
    """A13: 증상에 severity 가 없으면 422 VALIDATION_ERROR 이고 저장하지 않는다."""
    user = make_user(db)
    response = client.post(
        URL, json=_body(giSymptoms=[{"code": "NAUSEA"}]), headers=_headers(user)
    )
    _assert_rejected(response, db, user.id)


def test_duplicate_symptom_code_is_rejected(client, db):
    """A14: 같은 code 가 두 번 오면 422 VALIDATION_ERROR 이고 저장하지 않는다.

    어느 강도가 맞는지 서버가 고를 수 없다.
    """
    user = make_user(db)
    response = client.post(
        URL,
        json=_body(
            giSymptoms=[
                {"code": "NAUSEA", "severity": "MILD"},
                {"code": "NAUSEA", "severity": "SEVERE"},
            ]
        ),
        headers=_headers(user),
    )
    _assert_rejected(response, db, user.id)


# ─────────────────────────── recordedAt (양방향) ───────────────────────────


def test_future_recorded_at_is_rejected(client, db):
    """A15: 미래 recordedAt(now+1일) 은 422 VALIDATION_ERROR 이고 저장하지 않는다."""
    user = make_user(db)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    response = client.post(
        URL, json=_body(recordedAt=future.isoformat()), headers=_headers(user)
    )
    _assert_rejected(response, db, user.id)


def test_recent_past_recorded_at_is_accepted(client, db):
    """A16: 방금 전(now−1분) recordedAt 은 허용한다 (A15 의 짝)."""
    user = make_user(db)
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    response = client.post(
        URL, json=_body(recordedAt=recent.isoformat()), headers=_headers(user)
    )

    assert response.status_code == 201, response.text
    assert _count_states(db, user.id) == 1


def test_naive_recorded_at_is_rejected(client, db):
    """A26: 시간대 없는 recordedAt 은 422 VALIDATION_ERROR 이고 저장하지 않는다 (D5).

    시간대를 추측하면 KST/UTC 9시간 차이로 다른 날 기록이 된다.
    """
    user = make_user(db)
    response = client.post(
        URL, json=_body(recordedAt="2026-08-21T21:30:00"), headers=_headers(user)
    )
    _assert_rejected(response, db, user.id)


# ─────────────────────────── 모르는 필드 · 누락 ───────────────────────────


def test_unknown_top_level_field_is_rejected(client, db):
    """A17: 최상위 모르는 필드(weigthKg 오타)는 422 VALIDATION_ERROR 이고 저장하지 않는다.

    조용히 무시하면 201 이 나가 "저장됐다"는 착각을 준다 (팀 관례, PR #13).
    """
    user = make_user(db)
    response = client.post(URL, json=_body(weigthKg=78.4), headers=_headers(user))
    _assert_rejected(response, db, user.id)


def test_unknown_symptom_field_is_rejected(client, db):
    """A18: 증상 항목 안의 모르는 필드(memo)는 422 VALIDATION_ERROR 이고 저장하지 않는다."""
    user = make_user(db)
    response = client.post(
        URL,
        json=_body(
            giSymptoms=[{"code": "NAUSEA", "severity": "MILD", "memo": "아침에 심함"}]
        ),
        headers=_headers(user),
    )
    _assert_rejected(response, db, user.id)


def test_missing_weight_is_rejected(client, db):
    """A19: weightKg 가 없으면 422 VALIDATION_ERROR 이고 저장하지 않는다."""
    user = make_user(db)
    response = client.post(URL, json={"appetiteLevel": 3}, headers=_headers(user))
    _assert_rejected(response, db, user.id)


def test_explicit_null_symptoms_is_rejected(client, db):
    """A27: giSymptoms 에 명시적 null 을 보내면 422 VALIDATION_ERROR 이고 저장하지 않는다 (D8).

    "증상 없음" 은 [] 하나로만 표현한다.
    """
    user = make_user(db)
    response = client.post(URL, json=_body(giSymptoms=None), headers=_headers(user))
    _assert_rejected(response, db, user.id)


# ─────────────────────────── 인증 · 사용자 ───────────────────────────


def test_missing_user_header_is_401(client):
    """A20: X-User-Id 가 없으면 401 UNAUTHORIZED 다."""
    response = client.post(URL, json=VALID_BODY)
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_malformed_user_header_is_401(client):
    """A21: X-User-Id 형식이 틀리면 401 UNAUTHORIZED 다."""
    response = client.post(URL, json=VALID_BODY, headers={"X-User-Id": "not-a-uuid"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_unknown_user_is_404_and_stores_nothing(client, db):
    """A22: 없는 사용자면 404 USER_NOT_FOUND 이고 아무것도 저장하지 않는다.

    FK 위반 500 으로 새거나, 고아 행이 남으면 안 된다.
    """
    unknown = uuid.uuid4()
    response = client.post(URL, json=VALID_BODY, headers={"X-User-Id": str(unknown)})

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "USER_NOT_FOUND"
    assert _count_states(db, unknown) == 0


# ─────────────────────────── 로그 (규칙 6) ───────────────────────────

SECRET_WEIGHT = 123.45
SECRET_NOTE = "비밀메모"
SECRET_CODE = "NAUSEA"


def _secret_body(**overrides) -> dict:
    return _body(
        weightKg=SECRET_WEIGHT,
        note=SECRET_NOTE,
        giSymptoms=[{"code": SECRET_CODE, "severity": "MILD"}],
        **overrides,
    )


def _assert_no_secret_in_logs(caplog) -> None:
    assert SECRET_NOTE not in caplog.text
    assert str(SECRET_WEIGHT) not in caplog.text
    assert SECRET_CODE not in caplog.text


def test_success_does_not_log_health_values(client, db, caplog):
    """A23a: 성공한 요청이 체중·증상·메모를 로그에 남기지 않는다 (README 규칙 6)."""
    caplog.set_level(logging.DEBUG)
    user = make_user(db)
    response = client.post(URL, json=_secret_body(), headers=_headers(user))

    assert response.status_code == 201, response.text
    _assert_no_secret_in_logs(caplog)


def test_validation_error_does_not_log_health_values(client, db, caplog):
    """A23b: 422 요청이 체중·증상·메모를 로그에 남기지 않는다 (README 규칙 6).

    검증 오류 로그에 입력값(input)이 딸려 나가기 쉬운 경로다.
    """
    caplog.set_level(logging.DEBUG)
    user = make_user(db)
    response = client.post(
        URL, json=_secret_body(appetiteLevel=9), headers=_headers(user)
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    _assert_no_secret_in_logs(caplog)


# ─────────────────────────── OpenAPI 문서 ───────────────────────────


@pytest.fixture(scope="module")
def schema() -> dict:
    return app.openapi()


def _responses(schema: dict) -> dict:
    return schema["paths"][URL]["post"]["responses"]


def test_openapi_declares_401(schema):
    """A24: 문서의 POST /api/v1/user-states 에 401 이 선언돼 있다.

    FE 는 Swagger 를 계약으로 읽는다 — 문서에 없는 상태코드는 분기를 안 만든다.
    """
    assert "401" in _responses(schema)


@pytest.mark.parametrize("status", ["404", "422"])
def test_openapi_declares_404_and_422(schema, status):
    """A25: 문서의 POST /api/v1/user-states 에 404 · 422 가 선언돼 있다."""
    assert status in _responses(schema)
