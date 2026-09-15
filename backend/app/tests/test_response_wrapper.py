"""응답 래퍼와 예외 핸들러. DB 의존 0 — 테스트 안에서 작은 앱을 만들어 검사한다."""

from fastapi import FastAPI
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
