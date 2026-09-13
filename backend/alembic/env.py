"""Alembic 실행 환경.

접속 정보는 alembic.ini 가 아니라 app.core.config 의 Settings 에서 가져온다.
DB 비밀번호가 git 에 올라가는 파일에 남지 않게 하려는 것이다.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.models import Base  # 모든 모델을 import 해 metadata 를 채운다

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_dsn.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """DB 접속 없이 SQL 스크립트만 뽑는다 (`alembic upgrade head --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
