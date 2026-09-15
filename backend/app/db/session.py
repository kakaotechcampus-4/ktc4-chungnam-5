"""엔진·세션 팩토리와 FastAPI 의존성.

동기 Session 을 쓴다. `crud/` 가 유일한 DB 접근 지점이고(규칙 5) worker 도 같은
세션을 쓰기 때문에, async 컨텍스트 전파를 신경 쓰지 않는 쪽이 단순하다.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.sqlalchemy_dsn,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # 유휴 커넥션이 끊긴 걸 모르고 쓰다 죽는 걸 막는다
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 의존성. 요청 하나당 세션 하나."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
