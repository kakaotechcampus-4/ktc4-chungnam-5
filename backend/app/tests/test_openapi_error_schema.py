"""OpenAPI 문서가 실제 에러 응답과 일치하는지.

DB 의존 0 — 앱 객체에서 스키마만 뽑아 검사한다. `conftest.py` 가 import 전에
더미 DB_* 를 심어주므로 `app.main` import 만으로는 접속이 일어나지 않는다.

이 파일이 지키는 것: FE 는 별도 명세 파일 없이 Swagger 를 계약으로 읽는다.
문서에 없는 상태코드나 실제와 다른 스키마는 FE 의 분기를 조용히 깨뜨린다.
"""

from __future__ import annotations

import pytest

from app.main import app

# 인증 이음새(get_current_user_id)를 타는 라우트. POST /users/profile 은
# 인증이 없는 지금 이 호출이 곧 사용자 생성이라 여기 없다.
AUTHENTICATED_ROUTES = [
    ("/api/v1/meals", "get"),
    ("/api/v1/meals/{meal_id}", "delete"),
    ("/api/v1/meals/{meal_id}/items", "post"),
    ("/api/v1/meals/{meal_id}/items", "patch"),
    ("/api/v1/meals/{meal_id}/items/{item_id}", "delete"),
    ("/api/v1/nutrition/candidates", "get"),
    ("/api/v1/users/me", "get"),
    ("/api/v1/users/me", "patch"),
    ("/api/v1/dashboard", "get"),
    ("/api/v1/user-states", "post"),
]


@pytest.fixture(scope="module")
def schema() -> dict:
    return app.openapi()


def _responses(schema: dict, path: str, method: str) -> dict:
    return schema["paths"][path][method]["responses"]


def test_delete_meal_declares_404(schema):
    """없는·남의·이미 삭제된 식사는 404 다. 문서에 없으면 FE 가 분기를 안 만든다."""
    assert "404" in _responses(schema, "/api/v1/meals/{meal_id}", "delete")


def test_list_meals_declares_400(schema):
    """잘못된 cursor 는 400 이다 (endpoints/meals.py 의 ValueError 경로)."""
    assert "400" in _responses(schema, "/api/v1/meals", "get")


@pytest.mark.parametrize(("path", "method"), AUTHENTICATED_ROUTES)
def test_authenticated_route_declares_401(schema, path, method):
    """X-User-Id 가 없거나 형식이 틀리면 401 이다 (core/deps.py)."""
    assert "401" in _responses(schema, path, method)


def test_declared_error_response_uses_the_wrapper_schema(schema):
    """에러 응답 스키마가 { success, data, error } 래퍼여야 한다.

    실제 응답은 `_error_response` 가 만드는 래퍼다. 문서가 다른 모양을 약속하면
    FE 가 없는 키를 꺼내게 된다.
    """
    response = _responses(schema, "/api/v1/meals/{meal_id}", "delete")["404"]
    ref = response["content"]["application/json"]["schema"]["$ref"]
    model = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]

    assert set(model["properties"]) == {"success", "data", "error"}


def test_default_validation_error_schema_is_removed(schema):
    """FastAPI 기본 422 스키마(HTTPValidationError)는 거짓말이므로 없어야 한다.

    실제 422 는 detail 배열이 아니라 VALIDATION_ERROR 코드를 가진 래퍼다
    (`register_exception_handlers` 의 RequestValidationError 핸들러).
    """
    assert "HTTPValidationError" not in schema["components"]["schemas"]


def test_no_route_references_the_removed_validation_schema(schema):
    """지워진 `HTTPValidationError` 를 가리키는 `$ref` 가 남으면 안 된다.

    위 테스트는 컴포넌트가 **없는지**만 본다. 참조까지 보지 않으면, 라우트에
    `responses=` 를 빠뜨렸을 때 FastAPI 가 자동 생성한 422 가 지워진 스키마를
    가리킨 채로 통과한다. 그 문서는 Swagger UI 에서 에러가 나고
    `openapi-generator` · `orval` 같은 코드 생성기가 죽는다 — FE 가 계약으로
    읽는 문서다.

    라우트를 새로 추가하면서 `responses` 를 빠뜨리는 **같은 실수를 전부** 잡는다.
    """
    dangling = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and "HTTPValidationError" in str(value):
                    dangling.append(path)
                walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(schema.get("paths", {}))

    assert dangling == [], (
        "지워진 스키마를 가리키는 $ref 가 있다. 해당 라우트에 "
        "responses=error_responses(..., 422) 를 명시할 것: " + ", ".join(dangling)
    )
