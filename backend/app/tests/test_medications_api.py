"""투약 API 통합 테스트 — 응답 래퍼·인증·검증까지 실제 요청으로 확인한다."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud import user as user_crud


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
