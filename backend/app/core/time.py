"""서비스 시간대.

DB 는 UTC(timestamptz)로 저장하고, "하루" 경계와 응답 표기는 KST 로 한다.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def today_kst() -> date:
    """오늘 — **KST 기준**이다.

    `date.today()` 를 쓰면 안 된다. 그건 프로세스가 도는 곳의 날짜라서, 컨테이너가
    UTC 면(우리 Dockerfile 에 TZ 설정이 없다) **KST 00:00~08:59 에 전날**을 돌려준다.
    그 시간대에 투약 회차가 하나 적게 세지고 D-day 가 하루 밀린다.

    "하루" 경계를 쓰는 곳은 전부 이걸 쓴다 — 한 곳이라도 `date.today()` 를 쓰면
    같은 순간에 두 답이 나온다.
    """
    return datetime.now(KST).date()


def now_kst() -> datetime:
    """지금 — **KST 기준** aware datetime 이다.

    "오늘"(`now_kst().date()`)과 "몇 분 전"을 같은 한 순간에서 뽑으려고 둔다.
    모듈 전역 `datetime` 을 써야 테스트의 시각 고정(monkeypatch)이 잡는다.
    """
    raise NotImplementedError("now_kst 미구현")
