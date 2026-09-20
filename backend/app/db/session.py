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
    """FastAPI 의존성. 요청 하나당 세션 하나.

    commit 은 여기서 하지 않는다 — FastAPI 0.106+ 부터 yield 뒤의 코드는
    응답을 클라이언트에 이미 보낸 "뒤에" 실행되므로, 여기서 커밋하면 클라이언트가
    200을 받은 시점과 실제 DB 반영 시점 사이에 경합이 생긴다. commit은 각
    service 함수가 응답을 만들어 return 하기 직전에 직접 한다.
    이 rollback 은 service 가 커밋하기 전에 예외가 나서 세션이 애매한 상태로
    남는 걸 막는 안전망이다.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
