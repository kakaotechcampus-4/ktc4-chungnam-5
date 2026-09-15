"""사용자 프로필 API 3개. 응답 래퍼와 camelCase 직렬화까지 함께 검증한다."""

import uuid

CREATE_BODY = {
    "nickname": "종호",
    "heightCm": 174.0,
    "weightKg": 79.0,
    "baselineIntake": 700,
}


def _create(client):
    response = client.post("/api/v1/users/profile", json=CREATE_BODY)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_create_profile_returns_201_wrapped(client):
    body = client.post("/api/v1/users/profile", json=CREATE_BODY).json()
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    assert data["nickname"] == "종호"
    assert data["heightCm"] == 174.0
    assert data["weightKg"] == 79.0
    assert data["baselineIntake"] == 700.0
    assert data["onboardingStatus"] == "MEDICATION_REQUIRED"
    assert "createdAt" in data
    assert isinstance(data["userId"], str)


def test_numeric_fields_are_json_numbers_not_strings(client):
    """Decimal 을 그대로 내보내면 pydantic v2 가 문자열로 쓴다. 명세는 숫자다."""
    data = _create(client)
    assert isinstance(data["heightCm"], (int, float))
    assert isinstance(data["weightKg"], (int, float))
    assert isinstance(data["baselineIntake"], (int, float))


def test_get_me_returns_profile(client):
    created = _create(client)
    response = client.get("/api/v1/users/me", headers={"X-User-Id": created["userId"]})
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["userId"] == created["userId"]
    assert data["nickname"] == "종호"
    assert "createdAt" not in data  # GET 응답에는 없다


def test_get_me_without_header_is_401(client):
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_get_me_with_malformed_header_is_401(client):
    response = client.get("/api/v1/users/me", headers={"X-User-Id": "not-a-uuid"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_get_me_unknown_user_is_404(client):
    response = client.get("/api/v1/users/me", headers={"X-User-Id": str(uuid.uuid4())})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "USER_NOT_FOUND"


def test_patch_me_updates_weight(client):
    created = _create(client)
    headers = {"X-User-Id": created["userId"]}

    response = client.patch(
        "/api/v1/users/me", json={"weightKg": 78.4}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["data"]["weightKg"] == 78.4

    # 다시 읽어도 유지된다
    reread = client.get("/api/v1/users/me", headers=headers).json()["data"]
    assert reread["weightKg"] == 78.4


def test_patch_me_partial_update_keeps_other_fields(client):
    created = _create(client)
    headers = {"X-User-Id": created["userId"]}

    data = client.patch(
        "/api/v1/users/me", json={"nickname": "종호2"}, headers=headers
    ).json()["data"]
    assert data["nickname"] == "종호2"
    assert data["heightCm"] == 174.0
    assert data["weightKg"] == 79.0


def test_patch_me_rejects_invalid_value(client):
    created = _create(client)
    response = client.patch(
        "/api/v1/users/me",
        json={"weightKg": -1},
        headers={"X-User-Id": created["userId"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_profile_rejects_missing_field(client):
    response = client.post("/api/v1/users/profile", json={"nickname": "종호"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_me_rejects_typo_field(client):
    """오타난 필드(weigthKg)를 조용히 무시하지 않고 422 를 낸다.

    무시하면 200 + 예전 값이 담긴 응답이 나가 "저장됐다"는 착각을 준다
    (ProfileUpdateRequest 의 extra="forbid").
    """
    created = _create(client)
    response = client.patch(
        "/api/v1/users/me",
        json={"weigthKg": 78.4},  # 오타: weightKg
        headers={"X-User-Id": created["userId"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_profile_rejects_typo_field(client):
    """ProfileCreateRequest 도 동일하게 오타난 필드를 거부한다.

    필수 필드는 모두 정상적으로 채우고, 오타난 필드 하나를 추가로 보낸다 —
    "필수값 누락" 이 아니라 "extra=forbid" 자체를 검증하기 위해서다.
    """
    body = dict(CREATE_BODY)
    body["weigthKg"] = 79.0  # 오타: weightKg 는 그대로 두고 오타 필드를 추가
    response = client.post("/api/v1/users/profile", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
