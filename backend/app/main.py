from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.api.v1 import api_router
from app.core.response import register_exception_handlers

app = FastAPI(title="GLP-1 Meal Coach API", version="0.1.0")

register_exception_handlers(app)
app.include_router(api_router)


def custom_openapi() -> dict[str, Any]:
    """FastAPI 기본 422 스키마를 걷어낸 OpenAPI 문서.

    `RequestValidationError` 를 핸들러로 가로채 `{ success, data, error }` 래퍼로
    바꿔 내보내므로, FastAPI 가 넣는 `HTTPValidationError`(detail 배열)는 실제
    응답과 다르다. 라우트마다 422 를 `ErrorResponse` 로 명시해 두었으니 이 정의는
    아무도 참조하지 않는다 — 남겨두면 FE 가 없는 키를 꺼내게 된다.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schemas = schema.get("components", {}).get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
