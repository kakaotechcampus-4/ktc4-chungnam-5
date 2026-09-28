"""투약 API 통합 테스트 — 응답 래퍼·인증·검증까지 실제 요청으로 확인한다."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.time import today_kst
from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.models.enums import MedicationStage


@pytest.fixture
def user_id(db: Session) -> uuid.UUID:
    user = user_crud.create(
        db, nickname="투약API", height_cm=Decimal("170"), baseline_meal_kcal=Decimal("700")
    )
    db.flush()
    return user.id


def _h(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def test_post_returns_wrapped_current_state(client: TestClient, user_id: uuid.UUID) -> None:
    """오늘 시작했으면 1회차다 — `floor((today - startedAt) / 7) + 1`."""
    res = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.25, "startedAt": date.today().isoformat()},
        headers=_h(user_id),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["stage"] == "INITIAL"
    assert body["data"]["doseCount"] == 1
    assert body["data"]["startedAt"] == date.today().isoformat()


def test_numeric_fields_are_json_numbers_not_strings(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """Decimal 을 그대로 두면 "0.25" 처럼 문자열로 나간다."""
    data = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.25, "startedAt": "2026-09-01"},
        headers=_h(user_id),
    ).json()["data"]
    assert isinstance(data["doseMg"], (int, float)), data["doseMg"]
    assert isinstance(data["doseCount"], int)


def test_unsupported_drug_is_rejected(client: TestClient, user_id: uuid.UUID) -> None:
    """DrugName ENUM 이 경계에서 막는다 — 모르는 약의 단계를 추측하지 않는다."""
    res = client.post(
        "/api/v1/medications",
        json={"drugName": "오젬픽", "doseMg": 1.0},
        headers=_h(user_id),
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_future_start_date_is_rejected(client: TestClient, user_id: uuid.UUID) -> None:
    """미래 시작일은 422 다.

    명세의 에러 코드 목록에 날짜 전용 코드가 없어 VALIDATION_ERROR 로 흡수한다.
    회차 공식이 `(today - startedAt) / 7 + 1` 이라 미래 날짜면 0 이나 음수가 나온다.
    """
    res = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.25, "startedAt": "2099-01-01"},
        headers=_h(user_id),
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_same_dose_does_not_add_history(client: TestClient, user_id: uuid.UUID) -> None:
    """같은 용량으로 다시 보내면 용량 변경 이력이 늘지 않는다."""
    body = {"drugName": "위고비", "doseMg": 0.25, "startedAt": "2026-09-01"}
    client.post("/api/v1/medications", json=body, headers=_h(user_id))
    res = client.post("/api/v1/medications", json=body, headers=_h(user_id))

    assert res.status_code == 200
    assert res.json()["data"]["doseMg"] == 0.25


def test_without_header_is_401(client: TestClient) -> None:
    res = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.25},
    )
    assert res.status_code == 401


def test_unknown_user_is_404_not_500(client: TestClient) -> None:
    """UUID 형식은 맞지만 없는 사용자는 404 다. 500 이 아니다.

    막지 않으면 `crud.create` 가 users FK 를 위반해 IntegrityError 가 나고
    전역 핸들러가 500 INTERNAL_ERROR 로 내린다. `core/response.py` 가
    "INTERNAL_ERROR 는 5xx 전용, 4xx 는 클라이언트 책임"이라고 규정해 두었고,
    500 으로 나가면 서버가 멀쩡한데 장애 알림이 뜬다 (코드 리뷰 지적).
    """
    res = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.25, "startedAt": "2026-03-02"},
        headers={"X-User-Id": "00000000-0000-0000-0000-000000000000"},
    )

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "USER_NOT_FOUND"


def test_unknown_user_leaves_no_rows(db: Session, client: TestClient) -> None:
    """거부된 요청이 이력을 남기고 가면 안 된다."""
    ghost = uuid.UUID("00000000-0000-0000-0000-000000000000")
    client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.25, "startedAt": "2026-03-02"},
        headers={"X-User-Id": str(ghost)},
    )

    assert medication_crud.list_history(db, ghost) == []


# ── 명세 응답 형식 ─────────────────────────────────────────────
#
# POST 응답은 GET /medications/current 와 다르다. 현재 상태에 더해 "이번 요청으로
# 무엇이 바뀌었는지"를 싣고, `effectiveFrom` 은 싣지 않는다.

SPEC_FIELDS = {
    "medicationId",
    "drugName",
    "doseMg",
    "startedAt",
    "doseCount",
    "nextDoseDate",
    "daysUntilNextDose",
    "stage",
    "stageReason",
    "ruleVersion",
    "doseChanged",
    "doseEvent",
    "stageChanged",
    "decidedAt",
}


def _post(client: TestClient, user_id: uuid.UUID, **body: object) -> dict:
    return client.post("/api/v1/medications", json=body, headers=_h(user_id)).json()["data"]


def test_decided_at_is_kst(client: TestClient, user_id: uuid.UUID) -> None:
    """응답 시각은 `+09:00` 이다 — 규약 "날짜 ISO 8601 (+09:00)".

    `datetime.now()` 도 DB 도 UTC 라 그냥 두면 "…Z" 로 나간다. 값을 만들 때 KST 로
    바꾸는 방식으로는 부족하다 — DB 에서 읽어온 시각이 그대로 샌다. 직렬화 자리에서
    바꾸는 `KstDatetime`(`schemas/base.py`)을 쓴다.
    """
    data = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    assert data["decidedAt"].endswith("+09:00"), data["decidedAt"]


def _history(db: Session, user_id: uuid.UUID, *periods: tuple[str, ...]) -> None:
    """지난 구간들을 직접 깔아 둔다. `(용량, 시작일)` 또는 `(용량, 시작일, 약물)`.

    `POST /medications` 로는 못 만든다 — 용량 변경은 언제나 **오늘부터**라
    (`change_date = today`) 과거 날짜로 바꾼 척할 방법이 없고, 같은 날 두 번째 등록은
    정정으로 보아 409 다.
    """
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
    db.flush()


def test_response_has_exactly_the_spec_fields(client: TestClient, user_id: uuid.UUID) -> None:
    """빠진 필드도 남는 필드도 없어야 한다.

    `effectiveFrom` 이 여기 있으면 안 된다 — 명세에 없고, 현재 용량으로 바꾼 날은
    `doseEvent.effectiveFrom` 에 이미 들어 있다.
    """
    data = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")
    assert set(data) == SPEC_FIELDS


def test_next_dose_is_computed_not_omitted(client: TestClient, user_id: uuid.UUID) -> None:
    """nextDoseDate = startedAt + 7 x doseCount, daysUntilNextDose = nextDoseDate - today."""
    started_at = date.today() - timedelta(days=3)
    data = _post(
        client, user_id, drugName="위고비", doseMg=0.25, startedAt=started_at.isoformat()
    )

    assert data["doseCount"] == 1
    assert data["nextDoseDate"] == (started_at + timedelta(days=7)).isoformat()
    assert data["daysUntilNextDose"] == 4


def test_first_registration_reports_change(client: TestClient, user_id: uuid.UUID) -> None:
    """첫 등록은 PRE_DOSE 에서 넘어온 것이라 단계도 용량도 바뀐 것으로 본다."""
    data = _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt="2026-09-01")

    assert data["doseChanged"] is True
    assert data["stageChanged"] is True
    assert data["doseEvent"]["doseMg"] == 0.25
    # 비교할 이전 용량이 없다 — 명세 dose-events 예시의 de_001 이 이 경우다.
    assert data["doseEvent"]["direction"] == "MAINTAIN"
    assert set(data["doseEvent"]) == {"doseEventId", "doseMg", "direction", "effectiveFrom"}
    assert data["stage"] == "INITIAL"
    assert data["stageReason"]
    assert data["ruleVersion"] == "v1"


def test_same_dose_reports_no_change(client: TestClient, user_id: uuid.UUID) -> None:
    """같은 값으로 다시 보내면 변경이 아니다. 이벤트도 null 이다 —
    현재 행을 그대로 실으면 FE 가 "방금 바뀐 것"과 구분하지 못한다."""
    body = {"drugName": "위고비", "doseMg": 0.25, "startedAt": "2026-09-01"}
    _post(client, user_id, **body)
    data = _post(client, user_id, **body)

    assert data["doseChanged"] is False
    assert data["doseEvent"] is None
    assert data["stageChanged"] is False


def test_dose_change_reports_the_event(client: TestClient, user_id: uuid.UUID) -> None:
    """증량하면 그 변경 1건이 doseEvent 로 나온다. 사다리 첫 칸을 벗어나 단계도 바뀐다."""
    started_at = (date.today() - timedelta(days=14)).isoformat()
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt=started_at)
    data = _post(client, user_id, drugName="위고비", doseMg=0.5, startedAt=started_at)

    assert data["doseChanged"] is True
    assert data["stageChanged"] is True
    assert data["stage"] == "TITRATION"
    assert data["doseEvent"]["doseMg"] == 0.5
    assert data["doseEvent"]["direction"] == "INCREASE"
    assert data["doseEvent"]["effectiveFrom"] == date.today().isoformat()


def test_dose_decrease_is_reported_as_such(client: TestClient, user_id: uuid.UUID) -> None:
    """감량은 DECREASE 다. 방향을 저장하지 않고 이전 행과 비교해서 낸다."""
    started_at = (date.today() - timedelta(days=14)).isoformat()
    _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt=started_at)
    data = _post(client, user_id, drugName="위고비", doseMg=0.5, startedAt=started_at)

    assert data["doseEvent"]["direction"] == "DECREASE"


def test_medication_id_points_at_the_current_row(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """증량하면 id 가 새 행으로 바뀐다 — 같은 id 면 이력이 안 쌓였다는 뜻이다."""
    started_at = (date.today() - timedelta(days=14)).isoformat()
    first = _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt=started_at)
    second = _post(client, user_id, drugName="위고비", doseMg=0.5, startedAt=started_at)

    assert uuid.UUID(first["medicationId"]) != uuid.UUID(second["medicationId"])


def test_moving_the_start_date_is_409(client: TestClient, user_id: uuid.UUID) -> None:
    """등록한 뒤 전체 시작일을 옮기려 하면 409 다.

    이미 지나간 날을 다시 쓰는 일이라 등록이 아니라 **정정**이다 —
    `PATCH /medications/{id}` 의 몫이다. 값이 잘못된 게 아니므로 422 가 아니다.
    """
    started = (date.today() - timedelta(days=30)).isoformat()
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt=started)

    res = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.5, "startedAt": date.today().isoformat()},
        headers=_h(user_id),
    )

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"


def test_moving_the_start_date_is_409_even_when_the_date_is_future(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """기록이 있으면 미래 날짜라도 422 가 아니라 409 다.

    `startedAt` 은 애초에 쓸 수 없는 자리다. 미래 날짜라고 422 를 주면 "날짜만 고치면
    되겠네" 로 읽히는데, 과거 날짜를 넣어도 여전히 거부된다. 미래 시작일 422 는
    첫 등록에만 해당한다.
    """
    started = (date.today() - timedelta(days=30)).isoformat()
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt=started)

    res = client.post(
        "/api/v1/medications",
        json={
            "drugName": "위고비",
            "doseMg": 0.5,
            "startedAt": (date.today() + timedelta(days=7)).isoformat(),
        },
        headers=_h(user_id),
    )

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"


def test_same_day_re_registration_is_409(client: TestClient, user_id: uuid.UUID) -> None:
    """오늘 등록한 걸 같은 날 다시 등록하면 409 다.

    "용량을 바꾼 날 새로 맞았다" 와 "방금 잘못 넣어서 고친다" 가 요청만 봐서는
    구분되지 않는다. 앞은 감량기 판정을 낳고 뒤는 낳으면 안 되므로 엔드포인트로 가른다.
    """
    today = date.today().isoformat()
    _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt=today)

    res = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.5, "startedAt": today},
        headers=_h(user_id),
    )

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"


def test_typo_field_is_rejected(client: TestClient, user_id: uuid.UUID) -> None:
    """오타난 필드를 조용히 무시하지 않는다 (`extra="forbid"`).

    무시하면 `startedAt` 이 빠진 것으로 처리돼 오늘로 등록되고 회차가 1 로 리셋된다.
    클라이언트는 200 을 받고 저장됐다고 믿는다. `schemas/user.py` 와 같은 정책이다.
    """
    res = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 1.0, "startedAtt": "2026-06-14"},
        headers=_h(user_id),
    )

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


# ── GET /medications/current ────────────────────────────────────


def _current(client: TestClient, user_id: uuid.UUID) -> dict:
    res = client.get("/api/v1/medications/current", headers=_h(user_id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True and body["error"] is None
    return body["data"]


def test_current_has_exactly_the_spec_fields(client: TestClient, user_id: uuid.UUID) -> None:
    """명세: "POST /medications 응답과 동일 구조". 같은 14필드다.

    `effectiveFrom` 이 여기 있으면 안 된다 — POST 응답에 없는 필드라 "동일 구조"가
    깨진다. 현재 용량으로 바꾼 날은 `GET /medications/dose-events` 가 갖고 있다.
    """
    _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    assert set(_current(client, user_id)) == SPEC_FIELDS


def test_current_matches_what_post_returned(client: TestClient, user_id: uuid.UUID) -> None:
    """같은 날 조회하면 POST 가 준 상태와 같아야 한다 — 쓰기 결과 필드만 빼고."""
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")
    fetched = _current(client, user_id)

    state = SPEC_FIELDS - {"doseChanged", "doseEvent", "stageChanged", "decidedAt"}
    assert {k: fetched[k] for k in state} == {k: posted[k] for k in state}


def test_current_decided_at_is_kst(client: TestClient, user_id: uuid.UUID) -> None:
    """조회 응답도 `+09:00` 이다.

    `CurrentMedicationResponse` 가 `decided_at` 을 재선언한다(설명을 덮어쓰려고).
    타입까지 같이 적으므로 부모만 고치면 여기서 다시 `datetime` 으로 덮인다.
    """
    _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    data = _current(client, user_id)
    assert data["decidedAt"].endswith("+09:00"), data["decidedAt"]


def test_current_write_result_fields_are_fixed(client: TestClient, user_id: uuid.UUID) -> None:
    """조회는 아무것도 바꾸지 않는다 — 쓰기 결과 자리 셋은 구조상 고정값이다."""
    _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    data = _current(client, user_id)
    assert data["doseChanged"] is False
    assert data["stageChanged"] is False
    assert data["doseEvent"] is None
    # decidedAt 은 저장값이 아니라 "방금 판정했다" 는 뜻이라 null 이 아니다.
    assert data["decidedAt"]


def test_current_restages_as_days_pass(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """용량을 안 바꿔도 날짜가 지나면 단계가 옮겨간다.

    저장된 단계를 읽으면 마지막 POST 시점에 멈춘 값이 나간다 — 그 사이에는 쓰기가
    없어서 갱신할 기회 자체가 없다. 그래서 조회 때마다 다시 판정한다.
    """
    long_ago = date.today() - timedelta(weeks=20)
    _history(db, user_id, ("1.0", long_ago.isoformat()))

    data = _current(client, user_id)
    assert data["stage"] == "MAINTENANCE"
    assert data["stageReason"]
    assert data["doseCount"] == 21


def test_current_dday_is_within_a_week(client: TestClient, user_id: uuid.UUID) -> None:
    """다음 투약일은 7일 간격이라 D-day 는 구조상 1~7 이다."""
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt="2026-06-14")

    data = _current(client, user_id)
    assert 1 <= data["daysUntilNextDose"] <= 7
    assert data["nextDoseDate"] > date.today().isoformat()


def test_current_before_registration_is_409(client: TestClient, user_id: uuid.UUID) -> None:
    """투약 미등록은 409 STAGE_NOT_SET 이다 (명세).

    ⚠️ 팀 안건 — 200 + stage=PRE_DOSE 가 FE 에 낫다는 반론이 있다. 투약 전 식사
    평가가 제품 기능(D8)이라 "아직 투약 전"은 정상 상태인데, 에러로 내리면 FE 가
    정상 화면을 그리려고 에러를 삼켜야 한다. 지금은 명세를 그대로 따른다.
    """
    res = client.get("/api/v1/medications/current", headers=_h(user_id))

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "STAGE_NOT_SET"


def test_current_without_header_is_401(client: TestClient) -> None:
    assert client.get("/api/v1/medications/current").status_code == 401


def test_current_for_unknown_user_is_404_not_409(client: TestClient) -> None:
    """없는 사용자는 404 다. STAGE_NOT_SET 을 주면 "등록만 하면 된다" 로 읽힌다."""
    res = client.get("/api/v1/medications/current", headers=_h(uuid.UUID(int=0)))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "USER_NOT_FOUND"


def test_current_is_scoped_to_the_caller(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """남의 투약 정보가 보이지 않는다."""
    other = user_crud.create(
        db, nickname="남", height_cm=Decimal("160"), baseline_meal_kcal=Decimal("600")
    )
    db.flush()
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt="2026-06-14")

    res = client.get("/api/v1/medications/current", headers=_h(other.id))
    assert res.status_code == 409



# ── GET /medications/dose-events ────────────────────────────────


def _events(client: TestClient, user_id: uuid.UUID) -> list[dict]:
    res = client.get("/api/v1/medications/dose-events", headers=_h(user_id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True and body["error"] is None
    return body["data"]["events"]


def test_dose_events_is_empty_before_any_registration(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """투약 전 사용자도 200 이다 — 빈 목록이 "아직 없다" 를 그대로 말한다.

    404 로 내리면 FE 가 "아직 투약 전" 과 "에러" 를 구분하지 못한다.
    """
    assert _events(client, user_id) == []


def test_dose_events_follow_the_spec_shape(client: TestClient, user_id: uuid.UUID) -> None:
    """명세 `dose-events` 예시의 4필드. 초과도 누락도 없다."""
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt="2026-06-14")

    (event,) = _events(client, user_id)
    assert set(event) == {"doseEventId", "doseMg", "direction", "effectiveFrom"}
    assert event["doseMg"] == 0.25
    assert event["effectiveFrom"] == "2026-06-14"


def test_first_event_is_maintain(client: TestClient, user_id: uuid.UUID) -> None:
    """첫 등록은 비교할 이전 용량이 없다 — 명세 예시의 `de_001` 이 MAINTAIN 이다."""
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt="2026-06-14")

    assert _events(client, user_id)[0]["direction"] == "MAINTAIN"


def test_dose_events_are_oldest_first_with_directions(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """명세 예시 그대로 — 오래된 순, 앞 행 대비 방향.

    0.25 → 0.5 → 1.0 → 0.5 로 올렸다 내린다. 마지막이 DECREASE 로 잡혀야
    감량기 판정과 이력이 같은 사실을 말한다.
    """
    _history(
        db,
        user_id,
        ("0.25", "2026-06-14"),
        ("0.5", "2026-07-12"),
        ("1.0", "2026-08-09"),
        ("0.5", "2026-09-06"),
    )

    events = _events(client, user_id)
    assert [e["effectiveFrom"] for e in events] == [
        "2026-06-14", "2026-07-12", "2026-08-09", "2026-09-06",
    ]
    assert [e["doseMg"] for e in events] == [0.25, 0.5, 1.0, 0.5]
    assert [e["direction"] for e in events] == [
        "MAINTAIN", "INCREASE", "INCREASE", "DECREASE",
    ]


def test_drug_change_has_no_comparison_basis(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """약을 바꾸면 앞 행과 비교하지 않는다 — 사다리가 다르다.

    위고비 2.4 → 마운자로 2.5 는 mg 만 보면 증량이지만 증량이 아니다. 사다리가
    통째로 달라 나란히 둘 수 없다. 비교 기준이 없으므로 `MAINTAIN` 이다.

    `previous_different_dose()` 가 단계 판정에서 같은 판단을 하고 `POST` 의
    `doseEvent` 도 그 경로를 탄다 — 여기만 다르면 같은 사실에 두 답이 나온다.
    """
    _history(
        db,
        user_id,
        ("1.7", "2026-06-14"),
        ("2.4", "2026-07-12"),
        ("2.5", "2026-08-09", "마운자로"),
    )

    directions = [e["direction"] for e in _events(client, user_id)]

    assert directions == ["MAINTAIN", "INCREASE", "MAINTAIN"]


def test_drug_change_downward_is_not_a_decrease(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """반대 방향도 같다 — 마운자로 15 → 위고비 2.4 는 감량이 아니다.

    감량으로 잡히면 `REDUCED` 와 같은 사실을 말하는 것처럼 보이는데, 단계 판정은
    약물이 바뀌면 이력을 끊어 그렇게 보지 않는다.
    """
    _history(
        db,
        user_id,
        ("10.0", "2026-06-14", "마운자로"),
        ("15.0", "2026-07-12", "마운자로"),
        ("2.4", "2026-08-09"),
    )

    directions = [e["direction"] for e in _events(client, user_id)]

    assert directions == ["MAINTAIN", "INCREASE", "MAINTAIN"]


def _direction_from_both(
    client: TestClient, user_id: uuid.UUID, **body: object
) -> tuple[str, str]:
    """방금 등록한 그 한 행을 `POST` 응답과 `GET` 목록 양쪽에서 읽어 방향만 꺼낸다.

    `doseEventId` 로 짝지어 **같은 행**임을 못박는다 — 마지막 원소끼리 비교하면
    정렬이 어긋났을 때도 통과해 버린다.
    """
    posted = _post(client, user_id, **body)["doseEvent"]
    listed = {e["doseEventId"]: e for e in _events(client, user_id)}
    assert posted["doseEventId"] in listed, (posted, listed)
    return posted["direction"], listed[posted["doseEventId"]]["direction"]


def test_post_and_dose_events_agree_across_a_drug_change(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """위 두 테스트가 `GET` 에서 확인한 규칙을 `POST` 도 지키는지 본다.

    `register()` 가 직전 행의 용량(`current.dose_mg`)을 그대로 넘기던 때에는 위고비
    2.4 → 마운자로 2.5 가 `POST` 에서 `INCREASE`, `GET` 에서 `MAINTAIN` 이었다 —
    **같은 `doseEventId` 에 두 답**이다. 넘겨야 하는 값은 `previous_different_dose()`
    가 계산해 둔 `context.previous_different_dose_mg` 이고, 그 함수는 약물이 바뀌면
    None 을 준다.
    """
    _history(db, user_id, ("1.7", "2026-06-14"), ("2.4", "2026-07-12"))

    posted, listed = _direction_from_both(
        client, user_id, drugName="마운자로", doseMg=2.5
    )

    assert (posted, listed) == ("MAINTAIN", "MAINTAIN")


def test_post_and_dose_events_agree_within_one_drug(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """약이 그대로면 증량은 여전히 증량이다 — 비교를 통째로 끊어 버린 게 아니다."""
    _history(db, user_id, ("1.7", "2026-06-14"), ("2.4", "2026-07-12"))

    posted, listed = _direction_from_both(
        client, user_id, drugName="위고비", doseMg=5.0
    )

    assert (posted, listed) == ("INCREASE", "INCREASE")


def test_same_dose_adds_no_event(client: TestClient, user_id: uuid.UUID) -> None:
    """같은 값으로 다시 보내도 이력이 늘지 않는다 — 변경이 없으면 행도 안 생긴다."""
    body = {"drugName": "위고비", "doseMg": 0.25, "startedAt": "2026-06-14"}
    _post(client, user_id, **body)
    _post(client, user_id, **body)

    assert len(_events(client, user_id)) == 1


def test_rejected_same_day_registration_adds_no_event(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """같은 날 재등록은 409 로 막히므로 이력이 늘지 않는다.

    한 번도 맞은 적 없는 용량이 이력에 남으면 **감량 이력이 있는 것처럼 보인다** —
    그건 사실이 아니다. 정정은 `PATCH` 가 맡는다.
    """
    today = date.today().isoformat()
    _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt=today)
    client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.5, "startedAt": today},
        headers=_h(user_id),
    )

    (event,) = _events(client, user_id)
    assert event["doseMg"] == 1.0


def test_dose_events_are_scoped_to_the_caller(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """남의 이력이 섞이지 않는다."""
    other = user_crud.create(
        db, nickname="남", height_cm=Decimal("160"), baseline_meal_kcal=Decimal("600")
    )
    db.flush()
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt="2026-06-14")

    assert _events(client, other.id) == []


def test_dose_events_without_header_is_401(client: TestClient) -> None:
    assert client.get("/api/v1/medications/dose-events").status_code == 401


def test_dose_events_for_unknown_user_is_404(client: TestClient) -> None:
    """UUID 형식은 맞지만 없는 사용자는 404 다. 500 이 아니다."""
    res = client.get("/api/v1/medications/dose-events", headers=_h(uuid.UUID(int=0)))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "USER_NOT_FOUND"


# ── PATCH /medications/{medicationId} ───────────────────────────


def _patch(client: TestClient, user_id: uuid.UUID, medication_id, **body: object):
    return client.patch(
        f"/api/v1/medications/{medication_id}", json=body, headers=_h(user_id)
    )


def test_correction_updates_in_place_without_adding_history(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """**이력을 만들지 않는다.** 한 번도 맞은 적 없는 용량이 dose-events 에 남으면 안 된다.

    `POST` 와 나뉘는 지점이다 — 그쪽은 앞 행을 닫고 새 행을 만든다.
    """
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    res = _patch(client, user_id, posted["medicationId"], doseMg=0.5)

    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["doseMg"] == 0.5
    assert data["recordId"] == posted["medicationId"]  # 같은 행이다
    assert len(medication_crud.list_history(db, user_id)) == 1


CORRECT_FIELDS = {
    "recordId", "drugName", "doseMg", "injectionCount",
    "effectiveFrom", "effectiveTo", "stage", "stageReason",
    "stageChanged", "ruleVersion",
}


def test_correction_returns_exactly_the_spec_fields(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """명세의 10필드다. **`POST` 응답과 다르다.**

    그쪽은 "지금 투약이 어떤 상태인가" 라 전체 시작일·회차·다음 투약일이 들어가고,
    여기는 "그 기록이 어떻게 고쳐졌나" 라 고친 행 자체를 돌려준다.

    `doseChanged` · `doseEvent` 가 없는 게 핵심이다 — 정정은 용량을 바꾼 사건이
    아니라서 `dose-events` 에 새 점을 찍지 않는다.
    """
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    data = _patch(client, user_id, posted["medicationId"], doseMg=2.4).json()["data"]

    assert set(data) == CORRECT_FIELDS
    assert data["recordId"] == posted["medicationId"]
    assert data["effectiveTo"] is None  # 현재 구간이다


def test_correcting_a_past_row_is_allowed(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """과거 행도 고칠 수 있다 — 기록을 고치는 것이라 지난 화면이 바뀌는 게 맞다.

    이미 나온 끼니 평가·피드백은 안 흔들린다. 식사는 만들 때 복사해 둔 스냅샷을
    보고, 장기 피드백은 일일 피드백을 재료로 쓴다.
    """
    _history(db, user_id, ("0.25", "2026-06-14"), ("0.5", "2026-07-12"))
    oldest = medication_crud.get_first(db, user_id)

    res = _patch(client, user_id, oldest.id, doseMg=0.3)

    assert res.status_code == 200, res.text
    db.refresh(oldest)
    assert oldest.dose_mg == Decimal("0.3")
    # dose-events 는 기록을 그대로 보여주므로 같이 바뀐다.
    events = _events(client, user_id)
    assert [float(e["doseMg"]) for e in events] == [0.3, 0.5]


def test_correction_restages_the_row(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """값이 바뀌었으니 저장된 단계 판정도 다시 낸다."""
    posted = _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt="2026-06-14")
    assert posted["stage"] == "INITIAL"

    data = _patch(client, user_id, posted["medicationId"], doseMg=2.4).json()["data"]

    assert data["stage"] != "INITIAL"
    assert data["stageChanged"] is True


def test_moving_start_date_into_the_previous_period_is_409(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """앞 구간과 겹치면 막는다 — 같은 날에 용량이 둘이 되면 어느 쪽이 직전인지 모른다."""
    _history(db, user_id, ("0.25", "2026-06-14"), ("0.5", "2026-07-12"))
    second = medication_crud.get_current(db, user_id)

    res = _patch(client, user_id, second.id, effectiveFrom="2026-07-01")

    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "CONFLICT"
    # 앞 구간이 끝난 다음 날부터는 된다.
    assert _patch(
        client, user_id, second.id, effectiveFrom="2026-07-15"
    ).status_code == 200


def test_moving_start_date_into_the_future_is_409(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """현재 행은 끝이 없으니 오늘까지다 — 미래에 시작한 투약은 아직 맞은 적이 없다."""
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()

    res = _patch(client, user_id, posted["medicationId"], effectiveFrom=tomorrow)

    assert res.status_code == 409, res.text


def test_correction_returns_the_row_it_changed(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """응답의 `effectiveFrom` 은 **그 행의 값**이다 — 보낸 값이 그대로 돌아온다.

    `POST` 응답의 `startedAt`(전체 투약 시작일)과 다르다. 중간 행을 고쳐도 값이
    어긋나지 않는 이유다.
    """
    _history(db, user_id, ("0.25", "2026-06-14"), ("0.5", "2026-07-12"))
    second = medication_crud.get_current(db, user_id)

    data = _patch(
        client, user_id, second.id, effectiveFrom="2026-07-20"
    ).json()["data"]

    assert data["recordId"] == str(second.id)
    assert data["effectiveFrom"] == "2026-07-20"


def test_empty_correction_is_422(client: TestClient, user_id: uuid.UUID) -> None:
    """빈 몸통에 200 을 내리면 클라이언트가 뭔가 반영된 줄 안다."""
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    assert _patch(client, user_id, posted["medicationId"]).status_code == 422


def test_correcting_another_users_row_is_404(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """**주인은 200, 남은 404** — 짝으로 봐야 라우트를 지워도 통과하지 않는다."""
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")
    other = user_crud.create(
        db, nickname="남", height_cm=Decimal("160"), baseline_meal_kcal=Decimal("600")
    )
    db.flush()

    assert _patch(client, user_id, posted["medicationId"], doseMg=0.5).status_code == 200

    res = _patch(client, other.id, posted["medicationId"], doseMg=0.5)

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_correction_without_header_is_401(client: TestClient, db: Session) -> None:
    res = client.patch(f"/api/v1/medications/{uuid.uuid4()}", json={"doseMg": 0.5})

    assert res.status_code == 401


def test_past_row_is_not_compared_against_later_rows(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """과거 행을 고칠 때 **뒤 행과 비교하지 않는다.**

    `restage` 는 원래 현재 행 전용이라 이력을 통째로 준다. 과거 행에 그대로 쓰면
    목록 맨 앞이 그 행보다 **뒤에 있는 행**이라, 아직 오지도 않은 용량과 비교해
    "감량했다" 가 나온다 — 그 문구가 그대로 사용자에게 간다.
    """
    _history(db, user_id, ("0.5", "2026-06-14"), ("2.4", "2026-07-12"))
    oldest = medication_crud.get_first(db, user_id)

    data = _patch(client, user_id, oldest.id, doseMg=1.0).json()["data"]

    # 첫 행이라 비교할 직전 용량이 없다. 뒤의 2.4 를 집으면 REDUCED 가 된다.
    assert data["stage"] != "REDUCED", data["stageReason"]


def test_closed_row_counts_doses_only_within_its_period(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """닫힌 구간의 회차는 **그 구간 끝까지**만 센다.

    오늘까지 세면 4회차짜리 구간이 16회차가 되어, 충분히 오래된 행은 무엇을 고치든
    거의 항상 MAINTENANCE 로 저장된다.
    """
    _history(db, user_id, ("1.0", "2026-06-14"), ("1.7", "2026-07-12"))
    oldest = medication_crud.get_first(db, user_id)
    # 06-14 ~ 07-11 = 4회차. MAINTENANCE_STREAK 에 못 미친다.

    data = _patch(client, user_id, oldest.id, doseMg=1.0).json()["data"]

    assert data["stage"] == "TITRATION", data["stageReason"]


def test_correcting_a_past_row_restages_later_rows_too(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """뒤 행의 **저장된** 단계도 같이 고친다.

    A(1.0) → B(0.5) 에서 B 는 감량이다. A 를 0.25 로 정정하면 B 는 증량이 되는데,
    고친 행만 다시 내면 B.stage 가 감량으로 남는다. `crud/dashboard.py` 가 그
    저장값을 이웃끼리 비교해 단계 변경 이력을 뽑으므로, 있지도 않았던 하락이 찍힌다.
    """
    _history(db, user_id, ("1.0", "2026-06-14"), ("0.5", "2026-07-12"))
    oldest = medication_crud.get_first(db, user_id)
    current = medication_crud.get_current(db, user_id)
    # 1.0 → 0.5 는 감량이다. 그 판정이 행에 박혀 있는 상태를 만든다
    # (`_history` 는 stage 를 TITRATION 으로 고정해 넣는다).
    current.stage = MedicationStage.REDUCED
    db.flush()

    _patch(client, user_id, oldest.id, doseMg=0.25)

    db.refresh(current)
    # 0.25 → 0.5 는 증량이다. 감량기로 남아 있으면 대시보드가 없던 하락을 그린다.
    assert current.stage is not MedicationStage.REDUCED


def test_correcting_drug_name_updates_the_row(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """약물 정정이 실제로 저장되는지 본다.

    이 테스트가 없으면 `record.drug_name = ...` 두 줄을 통째로 지워도 스위트가
    초록이다 — 요청은 200 을 받고 약물만 조용히 안 바뀐다.
    """
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    data = _patch(
        client, user_id, posted["medicationId"], drugName="마운자로", doseMg=5.0
    ).json()["data"]

    assert data["drugName"] == "마운자로"
    assert data["doseMg"] == 5.0
    assert medication_crud.get_current(db, user_id).drug_name == "마운자로"


def test_correcting_drug_name_alone_is_422(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """약만 바꾸면 사다리 밖 조합이 만들어진다 — 마운자로 최소는 2.5mg 다.

    막지 않으면 "마운자로 1.0mg" 이 저장되고 `judge_stage` 가 첫 칸 이하로 보아
    INITIAL 을 준다. 존재하지 않는 처방이 화면에 뜬다.
    """
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    res = _patch(client, user_id, posted["medicationId"], drugName="마운자로")

    assert res.status_code == 422, res.text


def test_correction_rejects_absurdly_old_start_date(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """첫 행에는 앞 구간이 없어 하한이 비는데, 그 값이 곧 전체 투약 시작일이다.

    막지 않으면 `1900-01-01` 이 그대로 들어와 `doseCount` 가 6613 이 된다.
    """
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    res = _patch(client, user_id, posted["medicationId"], effectiveFrom="1900-01-01")

    assert res.status_code == 409, res.text


def test_correction_cannot_invert_a_closed_period(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """닫힌 구간은 자기 끝을 넘을 수 없다 — 넘으면 `effective_from > effective_to` 다.

    경계 양쪽을 본다: 끝나는 날 **당일**은 되고 그 다음 날은 안 된다.
    """
    _history(db, user_id, ("0.25", "2026-06-14"), ("0.5", "2026-07-12"))
    oldest = medication_crud.get_first(db, user_id)
    end = oldest.effective_to  # 2026-07-11

    assert _patch(
        client, user_id, oldest.id, effectiveFrom=end.isoformat()
    ).status_code == 200

    res = _patch(
        client, user_id, oldest.id, effectiveFrom=(end + timedelta(days=1)).isoformat()
    )

    assert res.status_code == 409, res.text


def test_correction_leaves_neighbour_periods_untouched(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """뒤 행의 구간은 안 건드린다 — 그래서 사이가 빈다. **의도한 동작이다.**

    뒤 행까지 같이 당기면 사용자가 말하지 않은 것을 서버가 지어내는 셈이다.
    """
    _history(db, user_id, ("0.25", "2026-06-14"), ("0.5", "2026-07-12"))
    first, second = medication_crud.list_history(db, user_id)

    # 첫 행의 시작일을 6일 뒤로 미룬다 (전체 투약 시작일 정정).
    assert _patch(
        client, user_id, first.id, effectiveFrom="2026-06-20"
    ).status_code == 200

    db.refresh(first)
    db.refresh(second)
    assert first.effective_from == date(2026, 6, 20)
    assert first.effective_to == date(2026, 7, 11)  # 자기 끝은 그대로
    assert second.effective_from == date(2026, 7, 12)  # 뒤 행도 그대로
    # 06-14 ~ 06-19 는 이제 비어 있다 — "그 기간에는 기록이 없다" 가 사실이다.


def test_correcting_an_unknown_medication_is_404(
    client: TestClient, user_id: uuid.UUID
) -> None:
    _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    res = _patch(client, user_id, uuid.uuid4(), doseMg=0.5)

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_correction_rejects_unknown_field(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """`extra="forbid"` — 오타난 필드를 조용히 무시하면 그 값이 통째로 사라진다."""
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    res = _patch(client, user_id, posted["medicationId"], doseMgg=0.5)

    assert res.status_code == 422


@pytest.mark.parametrize("dose", [0, -1])
def test_correction_rejects_non_positive_dose(
    client: TestClient, user_id: uuid.UUID, dose: float
) -> None:
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    assert _patch(client, user_id, posted["medicationId"], doseMg=dose).status_code == 422


def test_correction_rejects_more_than_three_decimals(
    client: TestClient, user_id: uuid.UUID
) -> None:
    """컬럼이 `Numeric(6, 3)` 이라 자릿수를 안 막으면 저장하며 조용히 반올림된다."""
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    assert _patch(client, user_id, posted["medicationId"], doseMg=0.2555).status_code == 422


def test_correcting_injection_count(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """회차도 정정 대상이다 (명세 요청 4필드 중 하나).

    이 테스트가 없으면 `record.injection_count = ...` 두 줄을 지워도 통과한다 —
    요청은 200 을 받고 값만 조용히 안 바뀐다.
    """
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    data = _patch(client, user_id, posted["medicationId"], injectionCount=12).json()["data"]

    assert data["injectionCount"] == 12
    assert medication_crud.get_current(db, user_id).injection_count == 12


def test_correction_rejects_non_positive_injection_count(
    client: TestClient, user_id: uuid.UUID
) -> None:
    posted = _post(client, user_id, drugName="위고비", doseMg=1.0, startedAt="2026-06-14")

    assert _patch(
        client, user_id, posted["medicationId"], injectionCount=0
    ).status_code == 422


def test_correcting_a_closed_row_keeps_its_effective_to(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """닫힌 행을 고치면 응답에 그 구간의 끝이 실린다 — 현재 행만 `null` 이다."""
    _history(db, user_id, ("0.25", "2026-06-14"), ("0.5", "2026-07-12"))
    oldest = medication_crud.get_first(db, user_id)

    data = _patch(client, user_id, oldest.id, doseMg=0.3).json()["data"]

    assert data["effectiveTo"] == "2026-07-11"


def test_injection_count_is_the_dose_count_when_the_period_began(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """`injection_count` 는 **그 구간이 시작될 때의 회차**다.

    `doseCount` 와 단위가 같아야 "11회차인데 9회차부터 1.0 맞고 있다" 가 성립한다.
    용량 변경 횟수를 세면 같은 행에서 11 과 3 이 나온다.
    """
    started = today_kst() - timedelta(days=70)  # 11회차
    posted = _post(
        client, user_id, drugName="위고비", doseMg=0.25, startedAt=started.isoformat()
    )
    assert posted["doseCount"] == 11

    # 과거 시작일로 첫 등록하면 이미 그만큼 맞아 온 것이다 — 1 이 아니다.
    assert medication_crud.get_first(db, user_id).injection_count == 11

    # 오늘 용량을 올리면 오늘 회차가 박힌다.
    _post(client, user_id, drugName="위고비", doseMg=0.5)

    assert medication_crud.get_current(db, user_id).injection_count == 11


def test_injection_count_tracks_elapsed_weeks_not_dose_changes(
    client: TestClient, db: Session, user_id: uuid.UUID
) -> None:
    """용량을 여러 번 바꿔도 회차는 날짜를 따른다 — 1·2·3 으로 세지 않는다."""
    _history(
        db, user_id,
        ("0.25", "2026-06-14"), ("0.5", "2026-07-12"), ("1.0", "2026-08-09"),
    )
    # `_history` 는 회차를 손으로 넣으므로, 계산 규칙만 직접 확인한다.
    from app.services.medication import count_doses

    assert count_doses(date(2026, 6, 14), today=date(2026, 6, 14)) == 1
    assert count_doses(date(2026, 6, 14), today=date(2026, 7, 12)) == 5
    assert count_doses(date(2026, 6, 14), today=date(2026, 8, 9)) == 9
