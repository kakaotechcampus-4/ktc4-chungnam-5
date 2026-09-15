"""core/deps.py — X-User-Id 인증 이음새의 production 가드.

DB 의존 0. get_current_user_id 를 직접 호출한다.
"""

import pytest

from app.core.config import get_settings
from app.core.deps import get_current_user_id


def test_get_current_user_id_raises_in_production(monkeypatch):
    """APP_ENV=production 이면 X-User-Id 인증 이음새를 아예 쓸 수 없다.

    타인의 UUID 를 헤더에 넣으면 그 사용자의 프로필(민감 건강정보)을 읽고 쓸 수
    있으므로, 조용히 동작하는 것보다 명확히 죽는 게 낫다.
    """
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            get_current_user_id(x_user_id="11111111-1111-1111-1111-111111111111")
    finally:
        get_settings.cache_clear()


def test_get_current_user_id_still_works_outside_production(monkeypatch):
    """가드가 로컬/테스트 환경(기본값)의 정상 동작을 막지 않는지 확인한다."""
    monkeypatch.setenv("APP_ENV", "local")
    get_settings.cache_clear()
    try:
        user_id = get_current_user_id(
            x_user_id="11111111-1111-1111-1111-111111111111"
        )
        assert str(user_id) == "11111111-1111-1111-1111-111111111111"
    finally:
        get_settings.cache_clear()
