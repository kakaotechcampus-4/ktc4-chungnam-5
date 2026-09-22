"""투약 API 통합 테스트 — 응답 래퍼·인증·검증까지 실제 요청으로 확인한다."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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


def _history(db: Session, user_id: uuid.UUID, *periods: tuple[str, str]) -> None:
    """지난 구간들을 직접 깔아 둔다.

    `POST /medications` 로는 못 만든다 — 용량 변경은 언제나 **오늘부터**다
    (`change_date = max(today, current.effective_from)`). 과거 날짜로 용량을 바꾼 척할
    방법이 없고, 같은 날 여러 번 보내면 in_place 로 한 줄에 합쳐진다.
    """
    previous = None
    for index, (dose, started) in enumerate(periods):
        effective_from = date.fromisoformat(started)
        if previous is not None:
            medication_crud.close_current(
                db, previous, effective_to=effective_from - timedelta(days=1)
            )
        previous = medication_crud.create(
            db,
            user_id=user_id,
            drug_name="위고비",
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


def test_start_date_after_first_change_is_422(client: TestClient, user_id: uuid.UUID) -> None:
    """시작일을 첫 용량 변경일 뒤로 밀면 422 다.

    막지 않으면 첫 행의 기간이 뒤집힌다 (effective_from > effective_to).
    미래 시작일과 같은 이유로 VALIDATION_ERROR 에 흡수한다 —
    명세의 에러 코드 목록에 날짜 전용 코드가 없다.
    """
    old = (date.today() - timedelta(days=30)).isoformat()
    changed_today = date.today().isoformat()
    _post(client, user_id, drugName="위고비", doseMg=0.25, startedAt=old)
    _post(client, user_id, drugName="위고비", doseMg=0.5, startedAt=old)

    res = client.post(
        "/api/v1/medications",
        json={"drugName": "위고비", "doseMg": 0.5, "startedAt": changed_today},
        headers=_h(user_id),
    )

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


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

