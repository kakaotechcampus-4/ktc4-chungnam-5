"""SQLAlchemy 선언적 베이스.

제약 조건 이름을 규약으로 고정한다. 이름이 없으면 PostgreSQL 이 자동으로 붙이는데,
그러면 Alembic 이 `DROP CONSTRAINT` 를 생성할 때 이름을 몰라 마이그레이션이 깨진다.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
