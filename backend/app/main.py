import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.v1 import api_router
from app.core.response import register_exception_handlers
from app.db.session import get_db
from app.infra.storage import StorageSettings

app = FastAPI(title="GLP-1 Meal Coach API", version="0.1.0")

register_exception_handlers(app)
app.include_router(api_router)

# LocalDiskStorage 가 저장한 파일을 서빙한다. 실제 S3Storage 로 교체되면 이 마운트는
# 필요 없어진다 — presigned URL 이 S3 를 직접 가리키게 된다.
_storage_settings = StorageSettings()
Path(_storage_settings.LOCAL_STORAGE_DIR).mkdir(parents=True, exist_ok=True)
app.mount(
    "/media",
    StaticFiles(directory=_storage_settings.LOCAL_STORAGE_DIR),
    name="media",
)


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
async def health() -> dict[str, str | None]:
    """살아 있는지와 **지금 떠 있는 빌드**.

    `version` 은 운영 릴리스 태그(`v2026.10.19`), `sha` 는 그 이미지를 만든 **전체** 커밋
    SHA 다. 둘 다 `infra/deploy.sh` 가 git 에서 읽어 이미지 빌드 인자로 넘기고
    (`backend/Dockerfile` 의 `ARG` → `ENV`), 여기서 그대로 낸다. 배포 워크플로가
    `"sha":"<GITHUB_SHA>"` 를 찾아 이번 커밋이 실제로 떠 있는지 확인하므로 값을 자르지 않는다.

    빈 값은 `null` 이다 — 스테이징과 태그 없는 배포는 `APP_VERSION` 이 빈 문자열이고,
    로컬 실행은 둘 다 없다.
    """
    return {
        "status": "ok",
        "version": os.environ.get("APP_VERSION") or None,
        "sha": os.environ.get("GIT_SHA") or None,
    }


@app.get("/health/ready")
def health_ready(db: Session = Depends(get_db)) -> JSONResponse:
    """요청을 받을 준비가 됐는지 — DB 에 `select 1` 까지 해 본다.

    Blue/Green 배포가 새 api 로 트래픽을 넘기기 **전에** 이 경로로 확인한다
    (컨테이너 healthcheck, `infra/bg-deploy.sh` 의 Caddy 쪽 확인). `/health` 는 DB 를
    보지 않아서 DB 에 못 붙는 api 로도 전환될 수 있다. `/health` 는 Caddy·배포 워크플로가
    키 없이 부르는 경로라 그대로 두고, 여기만 DB 를 본다. Caddy 는 `/health` 만 키 없이
    통과시키므로 이 경로는 밖에서 보이지 않는다.
    """
    try:
        db.execute(text("select 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(content={"status": "ok"})
