"""테스트 인프라가 실제 Postgres 에 붙고 마이그레이션이 적용됐는지만 확인한다."""

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from app.db.session import get_db
from app.main import app


def test_migrations_created_all_tables(db):
    """alembic upgrade head 가 돌았으면 도메인 테이블 22개 + alembic_version = 23."""
    table_count = db.execute(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    ).scalar_one()
    assert table_count == 23


def test_users_table_exists(db):
    columns = {c["name"] for c in inspect(db.get_bind()).get_columns("users")}
    assert "nickname" in columns
    assert "baseline_meal_kcal" in columns


def test_health_endpoint_is_reachable(client, monkeypatch):
    """빌드 정보가 없는 로컬 실행 — 버전 · SHA 는 null 이다."""
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": None, "sha": None}


def test_health_reports_release_version_and_full_sha(client, monkeypatch):
    """배포 이미지는 `deploy.sh` 가 넘긴 버전 태그와 **전체** 커밋 SHA 를 싣는다.

    배포 워크플로(H2)가 `"sha":"<GITHUB_SHA>"` 를 그대로 찾아 "이번 커밋이 떠 있는지" 를
    확인하므로 값을 자르거나 바꾸지 않는다.
    """
    sha = "740c7f855850638aab9dd6bc828ecb897534c1be"
    monkeypatch.setenv("APP_VERSION", "v2026.10.19")
    monkeypatch.setenv("GIT_SHA", sha)

    response = client.get("/health")

    assert response.json() == {"status": "ok", "version": "v2026.10.19", "sha": sha}
    assert f'"sha":"{sha}"' in response.text


def test_health_empty_version_is_null(client, monkeypatch):
    """스테이징 · 태그 없는 수동 배포는 `APP_VERSION` 이 빈 문자열로 들어온다 → null."""
    monkeypatch.setenv("APP_VERSION", "")
    monkeypatch.setenv("GIT_SHA", "740c7f855850638aab9dd6bc828ecb897534c1be")

    assert client.get("/health").json()["version"] is None


def test_health_ready_checks_db(client):
    """Blue/Green 전환 전 확인 — DB 에 붙으면 200."""
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_is_503_when_db_is_down(client):
    """DB 에 못 붙는 새 api 로는 전환하지 않도록 503 을 낸다."""

    class _DownSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("select 1", {}, Exception("connection refused"))

    app.dependency_overrides[get_db] = lambda: _DownSession()

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_users_has_height_cm_column(db):
    columns = {c["name"]: c for c in inspect(db.get_bind()).get_columns("users")}
    assert "height_cm" in columns
    assert columns["height_cm"]["nullable"] is True
