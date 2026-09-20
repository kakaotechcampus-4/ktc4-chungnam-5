"""애플리케이션 설정.

환경변수는 부팅 시점에 검증한다. 없거나 형식이 틀리면 여기서 죽는다 —
런타임에 이상하게 죽는 것보다 기동 시 명확히 죽는 게 낫다.
"""

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── PostgreSQL ──────────────────────────────────────────────
    # DB_URL 은 자격증명을 뺀 `host:port/dbname` 형태다.
    # 비밀번호를 DSN 문자열에 섞어두면 로그·에러 트레이스에 그대로 새기 때문에
    # 분리해서 받고 여기서만 조립한다.
    DB_URL: str = Field(description="host:port/dbname (예: localhost:5432/glp1_dev)")
    DB_USER: str
    DB_PASSWORD: str

    DB_ECHO: bool = Field(default=False, description="SQL 로그 출력 (로컬 디버깅용)")
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    APP_ENV: str = Field(
        default="local",
        description=(
            "local | production. get_current_user_id 가 X-User-Id 를 신뢰하는 "
            "임시 인증 이음새를 production 에서 강제로 죽이는 가드에 쓰인다 "
            "(app/core/deps.py)."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_dsn(self) -> str:
        """psycopg3 드라이버를 쓰는 SQLAlchemy 접속 문자열."""
        user = quote_plus(self.DB_USER)
        password = quote_plus(self.DB_PASSWORD)
        return f"postgresql+psycopg://{user}:{password}@{self.DB_URL}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
