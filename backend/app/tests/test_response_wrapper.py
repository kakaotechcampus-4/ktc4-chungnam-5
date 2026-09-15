"""응답 래퍼와 예외 핸들러. DB 의존 0 — 테스트 안에서 작은 앱을 만들어 검사한다."""

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
