"""services/user_state.py — create_user_state 와 지난주 대비 체중 변화량.

기준 시각 T 는 고정값이다. 과거 기록은 user_state_crud.create 로 recorded_at 을
명시해 넣는다 — 서버 now 에 기대면 같은 시각 충돌·날짜 경계 흔들림이 생긴다.
변화량 규칙(§10 D1·D2): 비교 대상 = "KST 날짜 ≤ T 의 KST 날짜 − 7일" 인 내 체중 기록 중
가장 최근 것. 날짜 단위라 경계 날에는 그날 마지막 기록이 대표값이 된다.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.crud import user_state as user_state_crud
from app.schemas.user_state import UserStateCreateRequest
from app.services import user_state as user_state_service
from app.tests.factories import make_user

KST = ZoneInfo("Asia/Seoul")
T = datetime.fromisoformat("2026-08-21T21:30:00+09:00")


# ─────────────────────────── helper ───────────────────────────


def _record(db, user_id, *, at: datetime, weight: str | None) -> None:
    """과거 기록 하나. 체중 없이 증상만 적은 기록은 weight=None."""
    user_state_crud.create(
        db,
        user_id=user_id,
        weight_kg=Decimal(weight) if weight is not None else None,
        recorded_at=at,
    )


def _kst(day: datetime, hour: int, minute: int = 0) -> datetime:
    """day 의 KST 날짜에서 hour:minute (KST 벽시계)."""
    local = day.astimezone(KST)
    return datetime(local.year, local.month, local.day, hour, minute, tzinfo=KST)


def _create(db, user_id, weight: str = "78.4"):
    request = UserStateCreateRequest.model_validate(
        {"weightKg": Decimal(weight), "recordedAt": T}
    )
    return user_state_service.create_user_state(db, user_id=user_id, request=request)


# ─────────────────────────── 비교 대상 고르기 ───────────────────────────


def test_record_older_than_a_week_is_the_baseline(db):
    """W1: 7일 전보다 앞선 내 기록(체중만 있는 가입형 기록)이 비교 대상이 된다.

    가입 때 적은 첫 체중도 지난주 비교에 쓰여야 한다.
    """
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=8), weight="79.0")

    response = _create(db, user.id)

    assert response.weight_change_kg == -0.6
    assert response.weight_change_baseline == "LAST_WEEK"


def test_record_within_a_week_is_not_the_baseline(db):
    """W2: 7일 이내 기록은 비교 대상에서 뺀다 ("지난주 대비"가 아니게 된다)."""
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=3), weight="79.0")

    assert _create(db, user.id).weight_change_kg is None


def test_other_users_record_is_not_the_baseline(db):
    """W3: 다른 사용자의 기록은 비교 대상에서 뺀다 (남의 체중과 비교하면 안 된다)."""
    user = make_user(db)
    other = make_user(db, nickname="타인")
    _record(db, other.id, at=T - timedelta(days=8), weight="79.0")

    assert _create(db, user.id).weight_change_kg is None


def test_record_without_weight_is_not_the_baseline(db):
    """W4: 체중 없이 증상만 적은 기록은 비교 대상에서 뺀다."""
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=8), weight=None)

    assert _create(db, user.id).weight_change_kg is None


def test_no_baseline_gives_null_not_zero(db):
    """W5: 비교 대상이 없으면 weight_change_kg 는 None 이다 (0 이 아니다).

    0 이면 "변화 없음"으로 읽힌다. baseline 은 그래도 LAST_WEEK 다 (D4).
    """
    user = make_user(db)

    response = _create(db, user.id)

    assert response.weight_change_kg is None
    assert response.weight_change_baseline == "LAST_WEEK"


def test_same_weight_gives_zero_not_null(db):
    """W6: 비교 대상과 체중이 같으면 0.0 이다 (None 이 아니다 — W5 의 짝)."""
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=8), weight="78.4")

    change = _create(db, user.id).weight_change_kg

    assert change is not None
    assert change == 0.0


def test_most_recent_eligible_record_is_the_baseline(db):
    """W7: 비교 대상 후보가 여럿이면 가장 최근 것을 쓴다."""
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=10), weight="80.0")
    _record(db, user.id, at=T - timedelta(days=8), weight="79.0")

    assert _create(db, user.id).weight_change_kg == -0.6


def test_more_recent_record_within_a_week_is_skipped(db):
    """W8: 더 최근 기록이라도 7일 이내면 건너뛰고 7일 전 이전 기록을 쓴다."""
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=8), weight="79.0")
    _record(db, user.id, at=T - timedelta(days=2), weight="77.0")

    assert _create(db, user.id).weight_change_kg == -0.6


def test_baseline_is_relative_to_recorded_at_not_server_now(db):
    """W9: 기준은 서버 now 가 아니라 이번 recordedAt 이다.

    T 보다 뒤의 기록(T+20d)이 있어도 T 기준 7일 전 이전 기록과 비교한다.
    """
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=8), weight="79.0")
    _record(db, user.id, at=T + timedelta(days=20), weight="70.0")

    assert _create(db, user.id).weight_change_kg == -0.6


def test_last_record_of_the_day_represents_the_day(db):
    """W10: 같은 KST 날에 여러 번 기록했으면 그날 마지막 기록을 대표값으로 쓴다.

    GET /dashboard 의 하루 대표값 규칙과 같아야 두 화면의 숫자가 어긋나지 않는다.
    """
    user = make_user(db)
    day = T - timedelta(days=9)
    _record(db, user.id, at=_kst(day, 9), weight="79.5")
    _record(db, user.id, at=_kst(day, 22), weight="79.0")

    assert _create(db, user.id).weight_change_kg == -0.6


# ─────────────────────────── 계산 ───────────────────────────


def test_change_is_rounded_to_one_decimal(db):
    """W11: 변화량은 소수 첫째 자리로 맞춘다 (78.43 − 79.00 → -0.6)."""
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=8), weight="79.00")

    assert _create(db, user.id, weight="78.43").weight_change_kg == -0.6


@pytest.mark.parametrize(
    ("weight", "expected"),
    [
        ("78.45", -0.6),  # -0.55: HALF_UP · HALF_EVEN 모두 -0.6
        ("78.55", -0.5),  # -0.45: HALF_UP 만 -0.5 (HALF_EVEN·round() 는 -0.4)
    ],
    ids=["minus-0.55", "minus-0.45"],
)
def test_change_rounds_half_up(db, weight, expected):
    """W16: 반올림은 ROUND_HALF_UP 이다 (D3). 두 케이스를 함께 통과하는 건 HALF_UP 뿐이다."""
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=8), weight="79.00")

    assert _create(db, user.id, weight=weight).weight_change_kg == expected


# ─────────────────────────── 7일 경계 (D1·D2) ───────────────────────────


def test_record_exactly_seven_days_before_is_included(db):
    """W13: 정확히 T−7일 정각 기록은 비교 대상에 포함된다 (D1)."""
    user = make_user(db)
    _record(db, user.id, at=T - timedelta(days=7), weight="79.0")

    assert _create(db, user.id).weight_change_kg == -0.6


def test_boundary_day_uses_last_record_of_that_day(db):
    """W14: 경계 날(T−7일의 KST 날짜)의 대표값은 그날 마지막 기록이다 (D2, 날짜 단위).

    23:00 기록은 T−7일 21:30 보다 늦지만 같은 날이라 포함되고, 그날 마지막이라 대표값이 된다.
    """
    user = make_user(db)
    day = T - timedelta(days=7)
    _record(db, user.id, at=_kst(day, 9), weight="79.0")
    _record(db, user.id, at=_kst(day, 23), weight="78.8")

    assert _create(db, user.id).weight_change_kg == -0.4


def test_day_after_boundary_day_is_excluded(db):
    """W15: 경계 날 다음 날(T−6일 KST 00:30) 기록은 비교 대상에서 뺀다 (W14 의 짝)."""
    user = make_user(db)
    _record(db, user.id, at=_kst(T - timedelta(days=6), 0, 30), weight="77.0")

    assert _create(db, user.id).weight_change_kg is None


# ─────────────────────────── 트랜잭션 ───────────────────────────


def test_service_commits(db, monkeypatch):
    """W12: 서비스가 커밋한다 — 빠뜨리면 에러 없이 조용히 버려진다 (규칙 5)."""
    user = make_user(db)
    calls = []
    original_commit = db.commit

    def counting_commit():
        calls.append(1)
        original_commit()

    monkeypatch.setattr(db, "commit", counting_commit)

    _create(db, user.id)

    assert len(calls) >= 1
