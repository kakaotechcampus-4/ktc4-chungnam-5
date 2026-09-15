# User Profile API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /users/profile` · `GET /users/me` · `PATCH /users/me` 세 엔드포인트를, 공통 응답 래퍼와 DB 통합 테스트 기반 위에 구현한다.

**Architecture:** 기존 `meals` 와 같은 4계층(`endpoints` → `services` → `crud` → `models`)을 따른다. 이번 작업에서 `core/` 에 응답 래퍼·에러 코드·사용자 식별 의존성이 새로 생기고, `app/tests/conftest.py` 에 Testcontainers 기반 Postgres 픽스처가 생긴다. 키는 `users` 에 새 컬럼으로, 체중은 `user_states` 를 유일한 출처로 둔다.

**Tech Stack:** Python 3.12 · FastAPI 0.141 · SQLAlchemy 2.0 (동기 Session) · Alembic · PostgreSQL 17 · pytest · testcontainers

**Spec:** `contracts/API.md` — "사용자" 절 (`POST /users/profile`, `GET /users/me`, `PATCH /users/me`) 및 "규약" · "에러 코드" 절

---

## Global Constraints

- Base path 는 `/api/v1`. `app/api/v1/__init__.py` 의 `api_router` 가 이미 `prefix="/api/v1"` 을 갖고 있으므로 엔드포인트 데코레이터에는 `/users/...` 만 쓴다.
- JSON 필드는 camelCase, 파이썬은 snake_case. `app/schemas/base.py` 의 `CamelModel` 을 상속해 해결한다 — 스키마마다 `alias_generator` 를 다시 쓰지 않는다.
- 모든 응답은 `{ "success": ..., "data": ..., "error": ... }` 래퍼에 담긴다. FE 는 HTTP status 가 아니라 `error.code` 로 분기한다.
- ID 는 UUID 문자열 그대로 내보낸다. `contracts/API.md` 의 `usr_01H8` 표기는 예시일 뿐이며 prefix 를 붙이지 않는다.
- 날짜·시각은 ISO 8601. `DateTime(timezone=True)` 컬럼이므로 tz 가 붙어 나간다.
- `services/` 하위 모듈은 서로 직접 참조하지 않는다. DB 접근은 `crud/` 를 통해서만 한다 (backend/README.md 규칙 5).
- `baseline_meal_kcal` 은 **투약 전 평소 한 끼** 열량이다 (D7 · 규칙 4). `contracts/API.md` 의 `baselineIntake: 2200` 은 잘못된 예시이지만 **이번 작업에서 명세 파일을 수정하지 않는다.**
- 커밋 메시지 prefix 는 `[BE-5]`. 브랜치는 `be/5-feat-user-api`.
- 모든 테스트는 `pytest` 한 번으로 돌아야 한다 (`pytest.ini` 의 `testpaths = app/tests`).

## 브레인스토밍에서 확정된 결정

| 항목 | 결정 | 근거 |
| --- | --- | --- |
| `heightCm` 저장 | `users.height_cm` 신규 컬럼, nullable | 키는 시계열이 아니라 프로필 속성. nullable 로 둬야 나중에 "로그인했지만 프로필 미입력" 상태를 지원할 수 있다 |
| `weightKg` 저장 | `user_states` 가 유일한 출처. `users` 에 체중 컬럼을 만들지 않는다 | 같은 사실이 두 테이블에 살면 `PATCH /users/me` 와 `POST /user-states` 가 어긋난다. 명세 예시(POST 79.0 → GET 78.4)도 이 해석과 맞는다 |
| `baselineIntake` | 기존 `users.baseline_meal_kcal` 재사용. 컬럼 추가·개명 없음 | 의미가 "평소 한 끼 kcal" 로 일치한다 |
| 응답 래퍼 | 이번 작업에서 `core/` 에 신규 생성, users 부터 적용 | `meals` 소급 적용은 **이 계획의 범위가 아니다** |
| ID 형식 | UUID 문자열 | 기존 `schemas/meal.py` 와 일치 |
| 사용자 식별 | `core/deps.py` 의 `get_current_user_id` 가 `X-User-Id` 헤더에서 읽는다 | JWT 가 붙으면 이 함수 본문만 바뀐다. `meals.py` 의 쿼리 파라미터 교체는 별도 작업 |
| 테스트 | Testcontainers + 실제 Postgres 17 | README 의 테스트 전략과 일치. 이후 `/medications` · `/user-states` 작업이 그대로 재사용 |
| 트랜잭션 | `crud/` 는 `add` · `flush` 까지, `commit` 은 `services/` 가 한다 | 라우트에 트랜잭션 로직이 새지 않게 한다 |

**명세에 없어 이번에 새로 정의하는 에러 코드 4개** — 리뷰어가 이의를 제기할 수 있도록 명시한다. `contracts/API.md` 의 에러 코드 표에는 이 네 가지가 없지만, 세 엔드포인트를 구현하려면 필요하다.

| code | HTTP | 언제 |
| --- | --- | --- |
| `UNAUTHORIZED` | 401 | `X-User-Id` 헤더가 없거나 UUID 형식이 아님 |
| `USER_NOT_FOUND` | 404 | 해당 `userId` 의 사용자가 없음 |
| `VALIDATION_ERROR` | 422 | 요청 본문 검증 실패 (FastAPI `RequestValidationError`) |
| `INTERNAL_ERROR` | 500 | 예기치 못한 예외 |

## 범위 밖 — 기록만 해둔다

- `contracts/API.md` 의 `MedicationStage` 에 `PRE_DOSE` 가 없는데 `app/models/enums.py` 에는 있다.
- `contracts/API.md` 의 `MealStatus` 에 `CONFIRMED` 가 있는데 backend/README.md 는 "4개가 전부, 중간 상태 추가 금지" 라고 못 박고 있다.
- `users.nickname` · `users.baseline_meal_kcal` 의 `NOT NULL` 제약은 나중에 "카카오 로그인 → 프로필 미입력(`PROFILE_REQUIRED`)" 흐름을 막는다. 인증 작업에서 다룬다.
- `restrictions` · `notificationEnabled` · `fcmToken` 은 명세에 없으므로 건드리지 않는다.
- `GET /meals` 를 응답 래퍼로 감싸는 소급 적용.

---

## File Structure

| 파일 | 책임 | 상태 |
| --- | --- | --- |
| `backend/requirements.txt` | testcontainers 추가 | 수정 |
| `backend/app/tests/conftest.py` | Postgres 컨테이너 · 마이그레이션 · 세션 · TestClient 픽스처 | 신규 |
| `backend/app/core/errors.py` | `ErrorCode` enum + `ApiError` 예외 | 신규 |
| `backend/app/core/response.py` | `ApiResponse` 래퍼 · `ok()` · 예외 핸들러 등록 | 신규 |
| `backend/app/core/deps.py` | `get_current_user_id` — 인증 이음새 | 신규 |
| `backend/app/main.py` | 예외 핸들러 등록 호출 | 수정 |
| `backend/app/models/user.py` | `User.height_cm` 컬럼 | 수정 |
| `backend/alembic/versions/*_add_users_height_cm.py` | 마이그레이션 | 신규 |
| `backend/app/schemas/user.py` | 요청·응답 스키마 + `OnboardingStatus` | 신규 |
| `backend/app/crud/user.py` | `users` 테이블 접근 | 신규 |
| `backend/app/crud/user_state.py` | `user_states` 테이블 접근 | 신규 |
| `backend/app/crud/medication.py` | `medication_records` 현재 행 조회 1개 | 신규 |
| `backend/app/services/user.py` | 조립 + `onboardingStatus` 계산 + commit | 수정 (현재 빈 파일) |
| `backend/app/api/v1/endpoints/users.py` | 라우트 3개 | 수정 (현재 빈 파일) |
| `backend/app/api/v1/__init__.py` | users 라우터 등록 | 수정 |

테스트 파일: `app/tests/test_infra_smoke.py` · `test_response_wrapper.py` · `test_user_crud.py` · `test_user_service.py` · `test_users_api.py`

---

### Task 1: 테스트 인프라 — Testcontainers + conftest

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/tests/conftest.py`
- Test: `backend/app/tests/test_infra_smoke.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces: pytest 픽스처 `db: sqlalchemy.orm.Session` (테스트마다 롤백되는 세션), `client: fastapi.testclient.TestClient` (`get_db` 가 `db` 로 오버라이드된 앱)

**배경 — 왜 이 태스크가 먼저인가.** backend/README.md 의 테스트 전략 표는 "API 는 `httpx.ASGITransport` + Testcontainers" 라고 적어두었지만 **실제로는 아무것도 없다.** `requirements.txt` 에 `pytest` 만 있고 `conftest.py` 도 없으며, 기존 `test_meal_service.py` 는 DB 를 쓰지 않는 순수 함수 테스트다. 이번 작업은 DB 왕복이 본체라 인프라를 먼저 깔아야 한다.

`fastapi.testclient.TestClient` 는 내부적으로 `httpx` 의 ASGI 전송을 동기로 감싼 것이라 README 가 말하는 방식과 같다. `pytest-asyncio` 없이 동기 테스트로 쓸 수 있어서 이쪽을 쓴다.

- [ ] **Step 1: testcontainers 설치 후 버전 고정**

```bash
cd backend
pip install "testcontainers[postgres]"
pip show testcontainers | grep ^Version
```

출력된 버전을 `requirements.txt` 의 `# 테스트` 절에 `pytest==9.1.1` 아래로 추가한다. 예를 들어 `Version: 4.13.2` 가 나왔다면:

```
# 테스트
pytest==9.1.1
testcontainers[postgres]==4.13.2
```

- [ ] **Step 2: Docker 가 떠 있는지 확인**

Run: `docker info`
Expected: 데몬 정보가 출력된다. 실패하면 Docker Desktop 을 켜고 다시 시도한다. 이 태스크부터는 Docker 없이 테스트가 돌지 않는다.

- [ ] **Step 3: 실패하는 스모크 테스트를 먼저 쓴다**

Create `backend/app/tests/test_infra_smoke.py`:

```python
"""테스트 인프라가 실제 Postgres 에 붙고 마이그레이션이 적용됐는지만 확인한다."""

from sqlalchemy import inspect, text


def test_migrations_created_all_tables(db):
    """alembic upgrade head 가 돌았으면 도메인 테이블 17개 + alembic_version = 18."""
    table_count = db.execute(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    ).scalar_one()
    assert table_count == 18


def test_users_table_exists(db):
    columns = {c["name"] for c in inspect(db.get_bind()).get_columns("users")}
    assert "nickname" in columns
    assert "baseline_meal_kcal" in columns


def test_health_endpoint_is_reachable(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 4: 실패를 확인한다**

Run: `pytest app/tests/test_infra_smoke.py -v`
Expected: FAIL — `fixture 'db' not found` (conftest.py 가 아직 없다)

- [ ] **Step 5: conftest.py 를 쓴다**

Create `backend/app/tests/conftest.py`:

```python
"""테스트 픽스처.

실제 PostgreSQL 컨테이너를 띄우고 alembic 으로 스키마를 올린다. SQLite 로 대체할 수
없다 — 이 스키마는 JSONB · native ENUM · 부분 UNIQUE 인덱스를 쓴다.

`app.db.session` 은 import 시점에 전역 엔진을 만든다. 그래서 아래 두 가지를 한다:
  1. import 전에 더미 DB_* 환경변수를 심어 Settings 검증이 통과하게 한다
     (create_engine 은 게으르다 — 이 시점에 접속하지 않는다)
  2. 테스트에서는 get_db 의존성을 오버라이드해 전역 엔진을 아예 쓰지 않는다
"""

from __future__ import annotations

import os
from collections.abc import Generator

# app.* 를 import 하기 전에 심어야 한다. Settings 가 없는 값에 죽는다.
os.environ.setdefault("DB_URL", "localhost:5432/placeholder")
os.environ.setdefault("DB_USER", "placeholder")
os.environ.setdefault("DB_PASSWORD", "placeholder")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402

_BACKEND_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


@pytest.fixture(scope="session")
def test_engine() -> Generator[Engine, None, None]:
    """Postgres 컨테이너를 띄우고 alembic upgrade head 를 돌린 뒤 엔진을 준다.

    세션당 한 번. 컨테이너 기동이 수 초 걸린다.
    """
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)

        # alembic/env.py 가 alembic.ini 가 아니라 Settings 에서 DSN 을 읽는다.
        # 그래서 환경변수를 바꾸고 lru_cache 를 비우는 것이 유일한 주입 지점이다.
        os.environ["DB_URL"] = f"{host}:{port}/{container.dbname}"
        os.environ["DB_USER"] = container.username
        os.environ["DB_PASSWORD"] = container.password
        get_settings.cache_clear()

        alembic_config = Config(os.path.join(_BACKEND_ROOT, "alembic.ini"))
        alembic_config.set_main_option(
            "script_location", os.path.join(_BACKEND_ROOT, "alembic")
        )
        command.upgrade(alembic_config, "head")

        engine = create_engine(get_settings().sqlalchemy_dsn)
        yield engine
        engine.dispose()


@pytest.fixture
def db(test_engine: Engine) -> Generator[Session, None, None]:
    """테스트 하나당 세션 하나. 끝나면 통째로 롤백한다.

    `join_transaction_mode="create_savepoint"` 가 핵심이다. services/ 가 부르는
    session.commit() 이 바깥 트랜잭션을 실제로 커밋하지 않고 SAVEPOINT 만 놓는다.
    이게 없으면 테스트가 서로의 데이터를 본다.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """get_db 가 위 db 세션을 돌려주도록 오버라이드한 앱."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
```

- [ ] **Step 6: 통과를 확인한다**

Run: `pytest app/tests/test_infra_smoke.py -v`
Expected: PASS 3개. 첫 실행은 이미지 pull 때문에 수십 초 걸릴 수 있다.

- [ ] **Step 7: 기존 테스트가 깨지지 않았는지 확인한다**

Run: `pytest -v`
Expected: `test_meal_service.py` · `test_worker_loop.py` · `test_queue_integration.py` 가 이전과 같은 결과를 낸다. 기존에 실패하던 테스트가 있었다면 그대로 실패해도 된다 — 이 태스크가 새로 깨뜨린 것만 없으면 된다.

- [ ] **Step 8: 커밋**

```bash
git add requirements.txt app/tests/conftest.py app/tests/test_infra_smoke.py
git commit -m "[BE-5] test: Testcontainers 기반 DB 테스트 픽스처 추가"
```

---

### Task 2: 응답 래퍼와 에러 코드

**Files:**
- Create: `backend/app/core/errors.py`
- Create: `backend/app/core/response.py`
- Modify: `backend/app/main.py`
- Test: `backend/app/tests/test_response_wrapper.py`

**Interfaces:**
- Consumes: 없음 (DB 를 쓰지 않는다)
- Produces:
  - `app.core.errors.ErrorCode` — str Enum
  - `app.core.errors.ApiError(code: ErrorCode, message: str, http_status: int)` — Exception
  - `app.core.response.ApiResponse[T]` — `success: bool` · `data: T | None` · `error: ErrorBody | None`
  - `app.core.response.ok(data: T) -> ApiResponse[T]`
  - `app.core.response.register_exception_handlers(app: FastAPI) -> None`

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다**

Create `backend/app/tests/test_response_wrapper.py`:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest app/tests/test_response_wrapper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.errors'`

- [ ] **Step 3: errors.py 를 쓴다**

Create `backend/app/core/errors.py`:

```python
"""API 에러 코드와 예외.

FE 는 HTTP status 가 아니라 `error.code` 로 분기한다 (contracts/API.md 규약).
그래서 status 는 예외가 함께 들고 다니고, 분기의 진실은 code 다.

`UNAUTHORIZED` · `USER_NOT_FOUND` · `VALIDATION_ERROR` · `INTERNAL_ERROR` 는
contracts/API.md 의 에러 코드 표에 없다. 세 엔드포인트를 구현하는 데 필요해서
여기서 정의했고, 명세에 역반영이 필요하다.
"""

import enum


class ErrorCode(str, enum.Enum):
    # contracts/API.md 에 있는 것
    PROFILE_REQUIRED = "PROFILE_REQUIRED"
    STAGE_NOT_SET = "STAGE_NOT_SET"

    # 이번 작업에서 새로 정의한 것
    UNAUTHORIZED = "UNAUTHORIZED"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    """핸들러가 응답 래퍼로 바꿔 내보내는 도메인 예외."""

    def __init__(self, code: ErrorCode, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
```

- [ ] **Step 4: response.py 를 쓴다**

Create `backend/app/core/response.py`:

```python
"""공통 응답 래퍼 `{ success, data, error }` 와 예외 핸들러.

성공 응답은 각 엔드포인트가 `response_model=ApiResponse[X]` 를 선언하고 `ok()` 로
감싼다. 미들웨어로 자동 래핑하지 않는다 — OpenAPI 문서가 실제 응답과 어긋나기 때문이다.
"""

from typing import Generic, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.errors import ApiError, ErrorCode

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: ErrorBody | None = None


def ok(data: T) -> ApiResponse[T]:
    # ApiResponse[T] 로 subscript 하지 않는다 — T 가 바인딩되지 않은 TypeVar 라
    # 런타임에 의미가 없다. 실제 직렬화는 라우트의 response_model 이 한다.
    return ApiResponse(success=True, data=data, error=None)


def _error_response(code: ErrorCode, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={
            "success": False,
            "data": None,
            "error": {"code": code.value, "message": message},
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """앱에 예외 → 응답 래퍼 변환을 붙인다. main.py 에서 한 번 부른다."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.code, exc.message, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 필드별 상세는 삼킨다. FE 는 code 로 분기하고, 상세를 그대로 내보내면
        # 내부 스키마 구조가 노출된다.
        return _error_response(
            ErrorCode.VALIDATION_ERROR, "요청 형식이 올바르지 않습니다.", 422
        )
```

- [ ] **Step 5: main.py 에 핸들러를 등록한다**

Modify `backend/app/main.py` — 전체를 아래로 바꾼다:

```python
from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.response import register_exception_handlers

app = FastAPI(title="GLP-1 Meal Coach API", version="0.1.0")

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 6: 통과를 확인한다**

Run: `pytest app/tests/test_response_wrapper.py -v`
Expected: PASS 4개

- [ ] **Step 7: 커밋**

```bash
git add app/core/errors.py app/core/response.py app/main.py app/tests/test_response_wrapper.py
git commit -m "[BE-5] feat: 공통 응답 래퍼와 에러 코드 추가"
```

---

### Task 3: `users.height_cm` 컬럼과 마이그레이션

**Files:**
- Modify: `backend/app/models/user.py`
- Create: `backend/alembic/versions/<자동생성>_add_users_height_cm.py`
- Test: `backend/app/tests/test_infra_smoke.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 1 의 `db` 픽스처
- Produces: `User.height_cm: Mapped[Decimal | None]` — `Numeric(4, 1)`, nullable

**왜 nullable 인가.** 명세의 `POST /users/profile` 은 `heightCm` 을 항상 받으므로 NOT NULL 로 둘 수도 있다. 하지만 나중에 카카오 로그인이 붙으면 "유저 행은 있는데 프로필은 아직" 인 상태(`onboardingStatus = PROFILE_REQUIRED`)가 생긴다. 그때 NOT NULL 이면 마이그레이션을 한 번 더 해야 한다. 지금 nullable 로 두고, 값이 있는지 여부를 `onboardingStatus` 판정에 그대로 쓴다.

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다**

`backend/app/tests/test_infra_smoke.py` 끝에 추가:

```python
def test_users_has_height_cm_column(db):
    columns = {c["name"]: c for c in inspect(db.get_bind()).get_columns("users")}
    assert "height_cm" in columns
    assert columns["height_cm"]["nullable"] is True
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest app/tests/test_infra_smoke.py::test_users_has_height_cm_column -v`
Expected: FAIL — `AssertionError: assert 'height_cm' in {...}`

- [ ] **Step 3: 모델에 컬럼을 추가한다**

Modify `backend/app/models/user.py` — `User` 클래스의 `nickname` 선언 바로 아래에 넣는다:

```python
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)

    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    """키(cm). 프로필 미입력 상태를 표현하려고 nullable 로 둔다."""
```

`Decimal` 과 `Numeric` 은 이 파일이 이미 import 하고 있으므로 import 문은 건드리지 않는다.

- [ ] **Step 4: 마이그레이션을 생성한다**

Run:

```bash
cd ../infra && docker compose up -d && cd ../backend
alembic upgrade head
alembic revision --autogenerate -m "add users height_cm"
```

생성된 `alembic/versions/*_add_users_height_cm.py` 를 **눈으로 검토한다.** 아래 두 줄만 있어야 한다. ENUM 관련 구문이나 다른 테이블 변경이 섞여 있으면 지운다.

```python
def upgrade() -> None:
    op.add_column('users', sa.Column('height_cm', sa.Numeric(precision=4, scale=1), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'height_cm')
```

- [ ] **Step 5: 로컬 DB 에 적용하고 모델과 어긋나지 않았는지 확인한다**

Run:

```bash
alembic upgrade head
alembic check
```

Expected: `No new upgrade operations detected.`

- [ ] **Step 6: 테스트 통과를 확인한다**

Run: `pytest app/tests/test_infra_smoke.py -v`
Expected: PASS 4개. `test_migrations_created_all_tables` 의 테이블 수는 18 그대로다 (컬럼 추가는 테이블 수를 바꾸지 않는다).

- [ ] **Step 7: 커밋**

```bash
git add app/models/user.py alembic/versions/ app/tests/test_infra_smoke.py
git commit -m "[BE-5] feat: users.height_cm 컬럼 추가"
```

---

### Task 4: crud 3개

**Files:**
- Create: `backend/app/crud/user.py`
- Create: `backend/app/crud/user_state.py`
- Create: `backend/app/crud/medication.py`
- Test: `backend/app/tests/test_user_crud.py`

**Interfaces:**
- Consumes: Task 1 의 `db` 픽스처, Task 3 의 `User.height_cm`
- Produces:
  - `app.crud.user.create(db, *, nickname: str, height_cm: Decimal | None, baseline_meal_kcal: Decimal) -> User`
  - `app.crud.user.get(db, user_id: uuid.UUID) -> User | None`
  - `app.crud.user.update(db, user: User, *, nickname: str | None = None, height_cm: Decimal | None = None, baseline_meal_kcal: Decimal | None = None) -> User`
  - `app.crud.user_state.create(db, *, user_id: uuid.UUID, weight_kg: Decimal | None, recorded_at: datetime, appetite_level: int | None = None, gi_symptoms: list | None = None, note: str | None = None) -> UserState`
  - `app.crud.user_state.get_latest_weight(db, user_id: uuid.UUID) -> Decimal | None`
  - `app.crud.medication.get_current(db, user_id: uuid.UUID) -> MedicationRecord | None`

**규약.** `crud/` 는 `commit` 하지 않는다. `add` 후 `flush` 만 해서 PK 가 채워지게 하고, 커밋 시점은 `services/` 가 정한다. 이렇게 해야 "유저 생성 + 첫 체중 기록" 이 한 트랜잭션으로 묶인다.

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다**

Create `backend/app/tests/test_user_crud.py`:

```python
"""crud/user · user_state · medication 의 DB 왕복 테스트."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.crud import user_state as user_state_crud
from app.models.enums import MedicationStage
from app.models.medication import MedicationRecord


def _make_user(db):
    return user_crud.create(
        db,
        nickname="종호",
        height_cm=Decimal("174.0"),
        baseline_meal_kcal=Decimal("700.00"),
    )


def test_create_assigns_id_without_commit(db):
    user = _make_user(db)
    assert user.id is not None
    assert user.nickname == "종호"
    assert user.height_cm == Decimal("174.0")


def test_get_returns_created_user(db):
    user = _make_user(db)
    assert user_crud.get(db, user.id) is user


def test_get_returns_none_for_unknown_id(db):
    assert user_crud.get(db, uuid.uuid4()) is None


def test_update_changes_only_given_fields(db):
    user = _make_user(db)
    updated = user_crud.update(db, user, nickname="종호2")
    assert updated.nickname == "종호2"
    assert updated.height_cm == Decimal("174.0")
    assert updated.baseline_meal_kcal == Decimal("700.00")


def test_latest_weight_is_none_without_records(db):
    user = _make_user(db)
    assert user_state_crud.get_latest_weight(db, user.id) is None


def test_latest_weight_returns_most_recent_by_recorded_at(db):
    user = _make_user(db)
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=Decimal("79.0"),
        recorded_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=Decimal("78.4"),
        recorded_at=datetime(2026, 8, 21, 21, 30, tzinfo=timezone.utc),
    )
    assert user_state_crud.get_latest_weight(db, user.id) == Decimal("78.40")


def test_latest_weight_skips_records_without_weight(db):
    """체중 없이 증상만 기록한 행은 '최신 체중' 이 아니다."""
    user = _make_user(db)
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=Decimal("79.0"),
        recorded_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=None,
        recorded_at=datetime(2026, 8, 21, 21, 30, tzinfo=timezone.utc),
        appetite_level=3,
    )
    assert user_state_crud.get_latest_weight(db, user.id) == Decimal("79.00")


def test_current_medication_is_none_without_records(db):
    user = _make_user(db)
    assert medication_crud.get_current(db, user.id) is None


def test_current_medication_ignores_ended_records(db):
    user = _make_user(db)
    db.add(
        MedicationRecord(
            user_id=user.id,
            drug_name="위고비",
            dose_mg=Decimal("0.25"),
            injection_count=1,
            stage=MedicationStage.INITIAL,
            effective_from=date(2026, 6, 14),
            effective_to=date(2026, 7, 12),
        )
    )
    db.flush()
    assert medication_crud.get_current(db, user.id) is None


def test_current_medication_returns_open_record(db):
    user = _make_user(db)
    db.add(
        MedicationRecord(
            user_id=user.id,
            drug_name="위고비",
            dose_mg=Decimal("1.0"),
            injection_count=10,
            stage=MedicationStage.MAINTENANCE,
            effective_from=date(2026, 8, 9),
            effective_to=None,
        )
    )
    db.flush()
    current = medication_crud.get_current(db, user.id)
    assert current is not None
    assert current.stage == MedicationStage.MAINTENANCE
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest app/tests/test_user_crud.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.crud.user'`

- [ ] **Step 3: crud/user.py 를 쓴다**

Create `backend/app/crud/user.py`:

```python
"""users 테이블 접근. 여기 말고는 아무도 User 를 직접 쿼리하지 않는다.

commit 하지 않는다 — 커밋 시점은 services/ 가 정한다.
"""

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.user import User


def create(
    db: Session,
    *,
    nickname: str,
    height_cm: Decimal | None,
    baseline_meal_kcal: Decimal,
) -> User:
    """새 사용자를 만든다. flush 까지만 하고 id 가 채워진 객체를 돌려준다."""
    user = User(
        nickname=nickname,
        height_cm=height_cm,
        baseline_meal_kcal=baseline_meal_kcal,
    )
    db.add(user)
    db.flush()
    return user


def get(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def update(
    db: Session,
    user: User,
    *,
    nickname: str | None = None,
    height_cm: Decimal | None = None,
    baseline_meal_kcal: Decimal | None = None,
) -> User:
    """None 으로 들어온 필드는 건드리지 않는다 — PATCH 의미 그대로다."""
    if nickname is not None:
        user.nickname = nickname
    if height_cm is not None:
        user.height_cm = height_cm
    if baseline_meal_kcal is not None:
        user.baseline_meal_kcal = baseline_meal_kcal
    db.flush()
    return user
```

- [ ] **Step 4: crud/user_state.py 를 쓴다**

Create `backend/app/crud/user_state.py`:

```python
"""user_states 테이블 접근.

체중의 유일한 출처다. users 테이블에는 체중 컬럼이 없다.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import UserState


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    weight_kg: Decimal | None,
    recorded_at: datetime,
    appetite_level: int | None = None,
    gi_symptoms: list | None = None,
    note: str | None = None,
) -> UserState:
    state = UserState(
        user_id=user_id,
        weight_kg=weight_kg,
        recorded_at=recorded_at,
        appetite_level=appetite_level,
        gi_symptoms=gi_symptoms if gi_symptoms is not None else [],
        note=note,
    )
    db.add(state)
    db.flush()
    return state


def get_latest_weight(db: Session, user_id: uuid.UUID) -> Decimal | None:
    """가장 최근에 '체중이 적힌' 기록의 체중. 없으면 None.

    체중 없이 증상만 기록한 행이 있을 수 있어서 NULL 을 걸러낸다.
    인덱스 ix_user_states_user_id_recorded_at 을 탄다.
    """
    stmt = (
        select(UserState.weight_kg)
        .where(UserState.user_id == user_id, UserState.weight_kg.is_not(None))
        .order_by(UserState.recorded_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()
```

- [ ] **Step 5: crud/medication.py 를 쓴다**

Create `backend/app/crud/medication.py`:

```python
"""medication_records 테이블 접근.

지금은 onboardingStatus 판정에 필요한 함수 하나뿐이다. POST /medications 작업에서
나머지가 붙는다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.medication import MedicationRecord


def get_current(db: Session, user_id: uuid.UUID) -> MedicationRecord | None:
    """진행 중인 투약 기록. effective_to 가 NULL 인 행이 현재 상태다.

    부분 UNIQUE 인덱스 uq_medication_records_current 가 이 행이 둘이 되는 걸 막는다.
    """
    stmt = select(MedicationRecord).where(
        MedicationRecord.user_id == user_id,
        MedicationRecord.effective_to.is_(None),
    )
    return db.execute(stmt).scalar_one_or_none()
```

- [ ] **Step 6: 통과를 확인한다**

Run: `pytest app/tests/test_user_crud.py -v`
Expected: PASS 10개

- [ ] **Step 7: 커밋**

```bash
git add app/crud/user.py app/crud/user_state.py app/crud/medication.py app/tests/test_user_crud.py
git commit -m "[BE-5] feat: user · user_state · medication crud 추가"
```

---

### Task 5: 스키마와 서비스

**Files:**
- Create: `backend/app/schemas/user.py`
- Modify: `backend/app/services/user.py` (현재 빈 파일)
- Test: `backend/app/tests/test_user_service.py`

**Interfaces:**
- Consumes: Task 2 의 `ApiError` · `ErrorCode`, Task 4 의 crud 3개
- Produces:
  - `app.schemas.user.OnboardingStatus` — `PROFILE_REQUIRED` · `MEDICATION_REQUIRED` · `READY`
  - `app.schemas.user.ProfileCreateRequest` — `nickname` · `height_cm` · `weight_kg` · `baseline_intake`
  - `app.schemas.user.ProfileUpdateRequest` — 위 네 필드가 전부 optional
  - `app.schemas.user.UserProfileResponse` — `user_id` · `nickname` · `height_cm` · `weight_kg` · `baseline_intake` · `onboarding_status`
  - `app.schemas.user.ProfileCreatedResponse(UserProfileResponse)` — `+ created_at`
  - `app.services.user.create_profile(db, *, request: ProfileCreateRequest) -> ProfileCreatedResponse`
  - `app.services.user.get_me(db, *, user_id: uuid.UUID) -> UserProfileResponse`
  - `app.services.user.update_me(db, *, user_id: uuid.UUID, request: ProfileUpdateRequest) -> UserProfileResponse`

**응답의 수치 타입.** 요청 스키마는 `Decimal` 로 받고 응답 스키마는 `float` 로 내보낸다. pydantic v2 는 JSON 직렬화에서 `Decimal` 을 **문자열**로 쓰기 때문에, 그대로 두면 명세의 `"heightCm": 174.0` 이 `"174.0"` 으로 나간다. 입력은 정밀도가 중요하니 `Decimal`, 출력은 명세 형태가 중요하니 `float` 다.

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다**

Create `backend/app/tests/test_user_service.py`:

```python
"""services/user.py — 조립과 onboardingStatus 판정."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import ApiError, ErrorCode
from app.crud import user as user_crud
from app.models.enums import MedicationStage
from app.models.medication import MedicationRecord
from app.schemas.user import (
    OnboardingStatus,
    ProfileCreateRequest,
    ProfileUpdateRequest,
)
from app.services import user as user_service


def _create_request(**overrides) -> ProfileCreateRequest:
    payload = {
        "nickname": "종호",
        "heightCm": 174.0,
        "weightKg": 79.0,
        "baselineIntake": 700,
    }
    payload.update(overrides)
    return ProfileCreateRequest.model_validate(payload)


def _add_current_medication(db, user_id) -> None:
    db.add(
        MedicationRecord(
            user_id=user_id,
            drug_name="위고비",
            dose_mg=Decimal("1.0"),
            injection_count=10,
            stage=MedicationStage.MAINTENANCE,
            effective_from=date(2026, 8, 9),
            effective_to=None,
        )
    )
    db.flush()


def test_create_profile_returns_medication_required(db):
    response = user_service.create_profile(db, request=_create_request())
    assert response.nickname == "종호"
    assert response.height_cm == 174.0
    assert response.weight_kg == 79.0
    assert response.baseline_intake == 700.0
    assert response.onboarding_status is OnboardingStatus.MEDICATION_REQUIRED
    assert response.created_at is not None


def test_create_profile_records_first_weight(db):
    response = user_service.create_profile(db, request=_create_request())
    me = user_service.get_me(db, user_id=response.user_id)
    assert me.weight_kg == 79.0


def test_get_me_is_ready_when_medication_exists(db):
    created = user_service.create_profile(db, request=_create_request())
    _add_current_medication(db, created.user_id)
    assert user_service.get_me(db, user_id=created.user_id).onboarding_status is (
        OnboardingStatus.READY
    )


def test_get_me_raises_for_unknown_user(db):
    with pytest.raises(ApiError) as exc_info:
        user_service.get_me(db, user_id=uuid.uuid4())
    assert exc_info.value.code is ErrorCode.USER_NOT_FOUND
    assert exc_info.value.http_status == 404


def test_get_me_is_profile_required_when_height_missing(db):
    """인증이 붙기 전에는 나올 수 없는 경로지만, 판정 자체는 맞아야 한다."""
    user = user_crud.create(
        db, nickname="미입력", height_cm=None, baseline_meal_kcal=Decimal("700.00")
    )
    assert user_service.get_me(db, user_id=user.id).onboarding_status is (
        OnboardingStatus.PROFILE_REQUIRED
    )


def test_update_me_changes_only_given_fields(db):
    created = user_service.create_profile(db, request=_create_request())
    updated = user_service.update_me(
        db,
        user_id=created.user_id,
        request=ProfileUpdateRequest.model_validate({"nickname": "종호2"}),
    )
    assert updated.nickname == "종호2"
    assert updated.height_cm == 174.0
    assert updated.weight_kg == 79.0


def test_update_me_weight_creates_new_state_record(db):
    """체중 수정은 users 를 고치지 않고 user_states 에 기록을 남긴다."""
    created = user_service.create_profile(db, request=_create_request())
    updated = user_service.update_me(
        db,
        user_id=created.user_id,
        request=ProfileUpdateRequest.model_validate({"weightKg": 78.4}),
    )
    assert updated.weight_kg == 78.4
    assert user_service.get_me(db, user_id=created.user_id).weight_kg == 78.4


def test_update_me_raises_for_unknown_user(db):
    with pytest.raises(ApiError) as exc_info:
        user_service.update_me(
            db,
            user_id=uuid.uuid4(),
            request=ProfileUpdateRequest.model_validate({"nickname": "없음"}),
        )
    assert exc_info.value.code is ErrorCode.USER_NOT_FOUND
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest app/tests/test_user_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.user'`

- [ ] **Step 3: schemas/user.py 를 쓴다**

Create `backend/app/schemas/user.py`:

```python
"""사용자 프로필 API 요청/응답 스키마.

요청은 Decimal 로 받고 응답은 float 로 내보낸다. pydantic v2 는 JSON 직렬화에서
Decimal 을 문자열로 쓰기 때문에, Decimal 그대로 두면 명세의 `"heightCm": 174.0` 이
`"174.0"` 으로 나간다.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.schemas.base import CamelModel


class OnboardingStatus(str, enum.Enum):
    """FE 의 진입 화면을 정한다. DB 컬럼이 아니라 파생값이다."""

    PROFILE_REQUIRED = "PROFILE_REQUIRED"
    MEDICATION_REQUIRED = "MEDICATION_REQUIRED"
    READY = "READY"


class ProfileCreateRequest(CamelModel):
    """POST /users/profile 요청."""

    nickname: str = Field(min_length=1, max_length=64)
    height_cm: Decimal = Field(gt=0, le=300)
    weight_kg: Decimal = Field(gt=0, le=500)
    baseline_intake: Decimal = Field(gt=0, le=10000)
    """투약 전 평소 한 끼 열량(kcal). Quantity 감소폭의 분모 (D7)."""


class ProfileUpdateRequest(CamelModel):
    """PATCH /users/me 요청. 준 필드만 바꾼다."""

    nickname: str | None = Field(default=None, min_length=1, max_length=64)
    height_cm: Decimal | None = Field(default=None, gt=0, le=300)
    weight_kg: Decimal | None = Field(default=None, gt=0, le=500)
    baseline_intake: Decimal | None = Field(default=None, gt=0, le=10000)


class UserProfileResponse(CamelModel):
    """GET /users/me · PATCH /users/me 응답."""

    user_id: uuid.UUID
    nickname: str
    height_cm: float | None
    weight_kg: float | None
    baseline_intake: float
    onboarding_status: OnboardingStatus


class ProfileCreatedResponse(UserProfileResponse):
    """POST /users/profile 201 응답. 생성 시각이 하나 더 붙는다."""

    created_at: datetime
```

- [ ] **Step 4: services/user.py 를 쓴다**

Modify `backend/app/services/user.py` (현재 빈 파일이므로 전체를 쓴다):

```python
"""사용자 프로필 도메인 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 커밋 시점은 여기서 정한다 —
"유저 생성 + 첫 체중 기록" 이 한 트랜잭션이어야 하기 때문이다.

체중은 users 에 없다. user_states 가 유일한 출처이고, 프로필 응답의 weightKg 는
가장 최근 기록에서 읽는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import ApiError, ErrorCode
from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.crud import user_state as user_state_crud
from app.models.user import User
from app.schemas.user import (
    OnboardingStatus,
    ProfileCreatedResponse,
    ProfileCreateRequest,
    ProfileUpdateRequest,
    UserProfileResponse,
)


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _resolve_onboarding_status(db: Session, user: User) -> OnboardingStatus:
    """진입 화면 판정.

    height_cm 이 비어 있으면 프로필이 아직 안 채워진 것으로 본다. 인증이 붙기
    전에는 프로필 생성이 곧 유저 생성이라 이 분기가 나오지 않지만, 카카오 로그인이
    붙으면 바로 쓰인다.
    """
    if user.height_cm is None:
        return OnboardingStatus.PROFILE_REQUIRED
    if medication_crud.get_current(db, user.id) is None:
        return OnboardingStatus.MEDICATION_REQUIRED
    return OnboardingStatus.READY


def _build_profile(db: Session, user: User) -> UserProfileResponse:
    return UserProfileResponse(
        user_id=user.id,
        nickname=user.nickname,
        height_cm=_to_float(user.height_cm),
        weight_kg=_to_float(user_state_crud.get_latest_weight(db, user.id)),
        baseline_intake=float(user.baseline_meal_kcal),
        onboarding_status=_resolve_onboarding_status(db, user),
    )


def _get_user_or_raise(db: Session, user_id: uuid.UUID) -> User:
    user = user_crud.get(db, user_id)
    if user is None:
        raise ApiError(ErrorCode.USER_NOT_FOUND, "사용자를 찾을 수 없습니다.", 404)
    return user


def create_profile(
    db: Session, *, request: ProfileCreateRequest
) -> ProfileCreatedResponse:
    """사용자를 만들고 첫 체중을 user_states 에 남긴다."""
    user = user_crud.create(
        db,
        nickname=request.nickname,
        height_cm=request.height_cm,
        baseline_meal_kcal=request.baseline_intake,
    )
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=request.weight_kg,
        recorded_at=datetime.now(timezone.utc),
    )
    db.commit()
    db.refresh(user)

    profile = _build_profile(db, user)
    return ProfileCreatedResponse(**profile.model_dump(), created_at=user.created_at)


def get_me(db: Session, *, user_id: uuid.UUID) -> UserProfileResponse:
    return _build_profile(db, _get_user_or_raise(db, user_id))


def update_me(
    db: Session, *, user_id: uuid.UUID, request: ProfileUpdateRequest
) -> UserProfileResponse:
    """준 필드만 바꾼다. 체중은 users 를 고치지 않고 새 기록을 남긴다."""
    user = _get_user_or_raise(db, user_id)

    user_crud.update(
        db,
        user,
        nickname=request.nickname,
        height_cm=request.height_cm,
        baseline_meal_kcal=request.baseline_intake,
    )
    if request.weight_kg is not None:
        user_state_crud.create(
            db,
            user_id=user.id,
            weight_kg=request.weight_kg,
            recorded_at=datetime.now(timezone.utc),
        )
    db.commit()
    db.refresh(user)

    return _build_profile(db, user)
```

- [ ] **Step 5: 통과를 확인한다**

Run: `pytest app/tests/test_user_service.py -v`
Expected: PASS 8개

- [ ] **Step 6: 커밋**

```bash
git add app/schemas/user.py app/services/user.py app/tests/test_user_service.py
git commit -m "[BE-5] feat: 사용자 프로필 스키마와 서비스 추가"
```

---

### Task 6: 인증 이음새와 엔드포인트 3개

**Files:**
- Create: `backend/app/core/deps.py`
- Modify: `backend/app/api/v1/endpoints/users.py` (현재 빈 파일)
- Modify: `backend/app/api/v1/__init__.py`
- Test: `backend/app/tests/test_users_api.py`

**Interfaces:**
- Consumes: Task 2 의 `ApiResponse` · `ok` · `ApiError` · `ErrorCode`, Task 5 의 서비스 3개
- Produces:
  - `app.core.deps.get_current_user_id(x_user_id: str | None = Header(...)) -> uuid.UUID`
  - `POST /api/v1/users/profile` · `GET /api/v1/users/me` · `PATCH /api/v1/users/me`

**이 태스크의 핵심은 `deps.py` 다.** JWT 가 붙을 때 바뀌는 파일이 여기 하나가 되도록, 라우트는 `Depends(get_current_user_id)` 만 본다. 헤더 이름을 쓰는 이유는 나중에 `Authorization` 으로 자리를 옮겨도 호출부가 그대로이기 때문이다.

- [ ] **Step 1: 실패하는 테스트를 먼저 쓴다**

Create `backend/app/tests/test_users_api.py`:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pytest app/tests/test_users_api.py -v`
Expected: FAIL — `POST /api/v1/users/profile` 이 404 를 낸다 (라우트가 없다)

- [ ] **Step 3: core/deps.py 를 쓴다**

Create `backend/app/core/deps.py`:

```python
"""요청에서 사용자를 식별한다 — 인증 이음새.

지금은 X-User-Id 헤더를 그대로 믿는다. **인증이 아니다.** JWT 가 붙으면 이 파일의
get_current_user_id 본문만 바뀌고, 이 의존성을 쓰는 라우트는 하나도 바뀌지 않는다.
api/v1/endpoints/meals.py 의 user_id 쿼리 파라미터도 나중에 여기로 옮긴다.
"""

import uuid

from fastapi import Header

from app.core.errors import ApiError, ErrorCode


def get_current_user_id(x_user_id: str | None = Header(default=None)) -> uuid.UUID:
    if x_user_id is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "X-User-Id 헤더가 필요합니다.", 401)
    try:
        return uuid.UUID(x_user_id)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.UNAUTHORIZED, "X-User-Id 형식이 올바르지 않습니다.", 401
        ) from exc
```

- [ ] **Step 4: endpoints/users.py 를 쓴다**

Modify `backend/app/api/v1/endpoints/users.py` (현재 빈 파일이므로 전체를 쓴다):

```python
"""사용자 프로필 공개 API.

경로에 /api/v1 을 쓰지 않는다 — api/v1/__init__.py 의 api_router 가 prefix 로 갖고 있다.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, ok
from app.db.session import get_db
from app.schemas.user import (
    ProfileCreatedResponse,
    ProfileCreateRequest,
    ProfileUpdateRequest,
    UserProfileResponse,
)
from app.services import user as user_service

router = APIRouter()


@router.post(
    "/users/profile",
    response_model=ApiResponse[ProfileCreatedResponse],
    status_code=201,
)
def create_profile(
    payload: ProfileCreateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse[ProfileCreatedResponse]:
    """프로필 최초 등록. 인증이 없는 지금은 이 호출이 곧 사용자 생성이다."""
    return ok(user_service.create_profile(db, request=payload))


@router.get("/users/me", response_model=ApiResponse[UserProfileResponse])
def get_me(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[UserProfileResponse]:
    return ok(user_service.get_me(db, user_id=user_id))


@router.patch("/users/me", response_model=ApiResponse[UserProfileResponse])
def update_me(
    payload: ProfileUpdateRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[UserProfileResponse]:
    return ok(user_service.update_me(db, user_id=user_id, request=payload))
```

- [ ] **Step 5: 라우터를 등록한다**

Modify `backend/app/api/v1/__init__.py` — 전체를 아래로 바꾼다:

```python
"""v1 API 라우터 묶음."""

from fastapi import APIRouter

from app.api.v1.endpoints import meals, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(meals.router, tags=["meals"])
api_router.include_router(users.router, tags=["users"])
```

- [ ] **Step 6: 통과를 확인한다**

Run: `pytest app/tests/test_users_api.py -v`
Expected: PASS 10개

- [ ] **Step 7: 전체 스위트와 스키마 정합을 확인한다**

Run:

```bash
pytest
alembic upgrade head && alembic check
```

Expected: 전체 통과 · `No new upgrade operations detected.`

- [ ] **Step 8: 커밋**

```bash
git add app/core/deps.py app/api/v1/endpoints/users.py app/api/v1/__init__.py app/tests/test_users_api.py
git commit -m "[BE-5] feat: 사용자 프로필 API 3개 추가"
```

---

## 완료 조건

- `pytest` 전체 통과 — 신규 테스트 36개 + 기존 테스트
- `alembic check` → `No new upgrade operations detected.`
- `uvicorn app.main:app --reload` 후 http://127.0.0.1:8000/docs 에서 세 엔드포인트가 보이고, 응답이 `{ success, data, error }` 형태다
