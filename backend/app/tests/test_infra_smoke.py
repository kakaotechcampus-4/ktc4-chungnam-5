"""테스트 인프라가 실제 Postgres 에 붙고 마이그레이션이 적용됐는지만 확인한다."""

from sqlalchemy import inspect, text


def test_migrations_created_all_tables(db):
    """alembic upgrade head 가 돌았으면 도메인 테이블 18개 + alembic_version = 19."""
    table_count = db.execute(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    ).scalar_one()
    assert table_count == 19


def test_users_table_exists(db):
    columns = {c["name"] for c in inspect(db.get_bind()).get_columns("users")}
    assert "nickname" in columns
    assert "baseline_meal_kcal" in columns


def test_health_endpoint_is_reachable(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_users_has_height_cm_column(db):
    columns = {c["name"]: c for c in inspect(db.get_bind()).get_columns("users")}
    assert "height_cm" in columns
    assert columns["height_cm"]["nullable"] is True
