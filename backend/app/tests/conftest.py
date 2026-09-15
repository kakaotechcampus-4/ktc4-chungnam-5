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
import platform
from collections.abc import Generator

# app.* 를 import 하기 전에 심어야 한다. Settings 가 없는 값에 죽는다.
os.environ.setdefault("DB_URL", "localhost:5432/placeholder")
os.environ.setdefault("DB_USER", "placeholder")
os.environ.setdefault("DB_PASSWORD", "placeholder")

# testcontainers 는 기본적으로 Ryuk(정리용 리퍼 컨테이너)를 띄우고, 그 컨테이너 자체의
# 포트 매핑을 곧바로 조회한다. Windows + Docker Desktop 조합에서는 이 조회가 Docker 데몬이
# NetworkSettings 를 채우기 전에 일어나는 경우가 있어 `ConnectionError: Port mapping ...
# is not available` 로 매번 죽는다 (컨테이너 자체는 정상적으로 뜬다 — 타이밍 문제다).
# 이 우회는 Windows 로만 좁힌다 — Ryuk 의 존재 이유는 원래 *비정상* 종료 경로(테스트
# 프로세스가 죽거나, 디버거 인터럽트, CI 잡 타임아웃/OOM)에서 남은 컨테이너를 정리하는
# 것이다. CI(보통 Linux 러너)에는 이 포트 매핑 레이스가 없으니 Ryuk 를 그대로 켜둬서
# 비정상 종료 시의 실제 leak 보호를 유지한다. 정상 종료 경로는 어느 플랫폼에서든
# `with PostgresContainer(...)` 컨텍스트 매니저가 직접 컨테이너를 정리하므로 Ryuk 유무와
# 무관하다.
if platform.system() == "Windows":
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

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
