"""Compose 전용 DB 초기화: 원본 시드 이전 스키마와 최종 스키마를 분리한다.

기존 DB를 지우거나 downgrade하지 않는다. 알려진 실험 스키마/행 수가 아니면 중단한다.
BE 이미지의 /srv에서 실행하며, 서비스 구현 또는 영양 계산은 변경하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib.parse import urlsplit

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory

BASE_REVISION = "b7e2c4a9d1f3"
FINAL_REVISION = "3a9c7e5b2d41"
# The committed dump already contains the deduplicated 276,092 rows. 336,351
# describes the historical source import in the migration docstring, not this
# SHA-pinned seed. Still apply the repository's migration sequence to the seed.
RAW_ROW_COUNT = 276092
FINAL_COUNTS = {"GENERAL": 14991, "PROCESSED": 261101}


def connect() -> psycopg.Connection:
    # 이 초기화 도구를 다른 DB에 실수로 적용하지 않도록 Compose 전용 주소를 고정한다.
    if os.environ.get("DB_URL") != "db:5432/food_live":
        raise RuntimeError("Bootstrap only supports the isolated food_live Compose database")
    address = urlsplit("postgresql://" + os.environ["DB_URL"])
    return psycopg.connect(
        host=address.hostname,
        port=address.port,
        dbname=address.path.lstrip("/"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        autocommit=True,
    )


def revision(connection: psycopg.Connection) -> str | None:
    exists = connection.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
    if not exists:
        return None
    rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one existing Alembic revision")
    return rows[0][0]


def check_database(connection: psycopg.Connection) -> None:
    settings = connection.execute(
        "SELECT current_setting('server_encoding'), datcollate, datctype "
        "FROM pg_database WHERE datname = current_database()"
    ).fetchone()
    if settings != ("UTF8", "C", "C"):
        raise RuntimeError("Expected the original seed database's UTF8/C/C settings")
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    if heads != [FINAL_REVISION]:
        raise RuntimeError("Alembic head changed; review and version the live experiment bootstrap")


def upgrade(target: str) -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", target], check=True)


def prepare(connection: psycopg.Connection) -> None:
    current = revision(connection)
    if current is None:
        table_count = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchone()[0]
        if table_count:
            raise RuntimeError("Refusing to initialize an existing unversioned database")
        upgrade(BASE_REVISION)
    elif current not in {BASE_REVISION, FINAL_REVISION}:
        raise RuntimeError("Unexpected database revision; no downgrade or reset was attempted")
    print(json.dumps({"stage": "schema-base", "revision": revision(connection)}))


def finalize(connection: psycopg.Connection) -> None:
    current = revision(connection)
    if current == BASE_REVISION:
        count = connection.execute("SELECT count(*) FROM food_refs").fetchone()[0]
        if count != RAW_ROW_COUNT:
            raise RuntimeError("Original food_refs seed row count does not match")
        upgrade(FINAL_REVISION)
    elif current != FINAL_REVISION:
        raise RuntimeError("Seed must be restored before migrating to head")
    counts = dict(connection.execute(
        "SELECT category::text, count(*) FROM food_refs GROUP BY category ORDER BY category"
    ).fetchall())
    if counts != FINAL_COUNTS:
        raise RuntimeError("Final food_refs counts do not match the existing experiment snapshot")
    connection.execute("ANALYZE food_refs")
    print(json.dumps({"stage": "ready", "revision": revision(connection), "counts": counts}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "finalize"))
    args = parser.parse_args()
    with connect() as connection:
        check_database(connection)
        {"prepare": prepare, "finalize": finalize}[args.stage](connection)


if __name__ == "__main__":
    main()
