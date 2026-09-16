"""응답 래퍼와 예외 핸들러.

대부분은 DB 의존 0 — 테스트 안에서 작은 앱을 만들어 검사한다. 다만 마지막
두 개(`/meals` 400·405 회귀 테스트)는 실제 라우터를 타므로 `client` 픽스처 →
Postgres 컨테이너를 쓴다.
"""

import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import ApiError, ErrorCode
from app.core.response import ApiResponse, ok, register_exception_handlers


class Payload(BaseModel):
    value: int


def build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/fine", response_model=ApiResponse[Payload])
    def fine() -> ApiResponse[Payload]:
        return ok(Payload(value=7))

    @app.get("/missing")
    def missing() -> None:
        raise ApiError(ErrorCode.USER_NOT_FOUND, "사용자를 찾을 수 없습니다.", 404)

    @app.post("/strict")
    def strict(payload: Payload) -> None:
        return None

    @app.get("/http-error")
    def http_error() -> None:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    @app.get("/unhandled")
    def unhandled() -> None:
        raise ValueError("버그가 발생했습니다.")

    return app


client = TestClient(build_app())


def test_success_is_wrapped_with_null_error():
    response = client.get("/fine")
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"value": 7}, "error": None}


def test_api_error_is_wrapped_with_null_data():
    response = client.get("/missing")
    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "data": None,
        "error": {"code": "USER_NOT_FOUND", "message": "사용자를 찾을 수 없습니다."},
    }


def test_request_validation_error_is_wrapped():
    response = client.post("/strict", json={"value": "일곱"})
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_error_code_values_equal_their_names():
    """FE 가 문자열로 분기한다. 값과 이름이 어긋나면 조용히 안 맞는다."""
    for code in ErrorCode:
        assert code.value == code.name


def test_nonexistent_route_is_wrapped():
    """존재하지 않는 경로는 404로 래핑된다."""
    response = client.get("/nonexistent")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"


def test_http_exception_is_wrapped():
    """HTTPException 을 던지는 라우트의 응답이 래핑되고 원래 status 가 유지된다."""
    response = client.get("/http-error")
    assert response.status_code == 401
    assert response.json() == {
        "success": False,
        "data": None,
        "error": {"code": "UNAUTHORIZED", "message": "인증이 필요합니다."},
    }


def test_unhandled_exception_is_wrapped_and_sanitized():
    """처리되지 않은 예외는 500 + INTERNAL_ERROR 로 래핑되고 예외 메시지가 노출되지 않는다."""
    # TestClient 는 기본값 raise_server_exceptions=True 라서
    # 이 테스트를 위해 별도의 클라이언트를 만든다.
    app = build_app()
    client_no_raise = TestClient(app, raise_server_exceptions=False)

    response = client_no_raise.get("/unhandled")
    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    # 예외 메시지("버그가 발생했습니다.")가 응답에 없어야 한다.
    assert "버그가 발생했습니다." not in body["error"]["message"]
    assert body["error"]["message"] == "서버 오류가 발생했습니다."


def test_http_exception_400_from_real_endpoint_is_wrapped_as_bad_request(client):
    """회귀 테스트: /meals 엔드포인트의 400 오류가 BAD_REQUEST 로 래핑된다.

    식사 목록 조회 시 잘못된 cursor 를 전달하면 ValueError 가 발생하고,
    이것이 HTTPException(status_code=400) 으로 변환된다.
    이 400 은 명시 매핑에 없는 4xx 이므로 BAD_REQUEST 로 흡수되어야 한다
    (이 테스트가 지키는 것: 클라이언트 입력 오류가 서버 오류로 둔갑하지 않는다).
    """
    # 적절한 user_id (UUID 형식)
    user_id = uuid.uuid4()

    # 잘못된 cursor 로 요청
    response = client.get("/api/v1/meals", params={"user_id": str(user_id), "cursor": "garbage"})

    # 400 으로 응답, BAD_REQUEST 로 래핑되어야 함
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "BAD_REQUEST"
    # 상세 메시지는 에러마다 다를 수 있으니 존재만 확인
    assert isinstance(body["error"]["message"], str)


def test_method_not_allowed_from_real_endpoint_is_wrapped_as_bad_request(client):
    """회귀 테스트: 존재하지 않는 메서드(405)가 VALIDATION_ERROR 가 아닌 BAD_REQUEST 로 래핑된다.

    /api/v1/users/me 는 GET/PATCH 만 있다. DELETE 는 405 이고, 이건 "필드가
    틀렸다" 는 뜻이 아니므로 VALIDATION_ERROR 로 흡수되면 안 된다.
    """
    response = client.delete("/api/v1/users/me")
    assert response.status_code == 405
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "BAD_REQUEST"
