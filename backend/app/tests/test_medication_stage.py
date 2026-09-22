"""단계 판정 단위 테스트.

`services/medication.py` 의 순수 함수 구역은 DB·인증 없이 검증된다.

경계값은 표로 고정한다 (R1 튜닝 때 사다리나 MAINTENANCE_STREAK 를 바꾸면 여기가 먼저 깨져야 한다).

회차·다음 예정일도 여기 있다 — 날짜 산수라 DB 가 필요 없다 (명세 「서버 계산 항목」).

판정 규칙 근거: `docs/be-medication-stage-rule.md`
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.enums import DrugName, MedicationStage
from app.schemas.medication import DoseDirection
from app.services.medication import (
    DOSE_INTERVAL_DAYS,
    DOSE_LADDERS,
    ESCALATION_DOSES,
    MAINTENANCE_STREAK,
    STAGE_REASONS,
    count_doses,
    dose_context,
    dose_direction,
    judge_stage,
    predict_next_dose,
    previous_different_dose,
)

INITIAL = MedicationStage.INITIAL
TITRATION = MedicationStage.TITRATION
MAINTENANCE = MedicationStage.MAINTENANCE
REDUCED = MedicationStage.REDUCED

WEGOVY = DrugName.WEGOVY
MOUNJARO = DrugName.MOUNJARO


def W(*values: str) -> list[tuple[str, Decimal]]:
    """위고비 이력. 최신이 먼저다."""
    return [(WEGOVY, Decimal(v)) for v in values]


def M(*values: str) -> list[tuple[str, Decimal]]:
    """마운자로 이력."""
    return [(MOUNJARO, Decimal(v)) for v in values]


# ── previous_different_dose: 이력에서 직전의 '다른' 용량 ──────
#
# previous 는 **최신이 먼저**다. 용량이 바뀌는 지점 또는 약물이 바뀌는 지점에서 멈춘다.


@pytest.mark.parametrize(
    ("dose", "previous", "expected"),
    [
        # 첫 투약 — 비교할 과거가 없다
        ("0.25", [], None),
        # 쭉 같은 용량뿐이면 '다른 용량'이 없다
        ("0.5", W("0.5"), None),
        ("0.5", W("0.5", "0.5", "0.5"), None),
        # 용량이 바뀌는 지점에서 멈춘다. 그 너머는 안 본다
        ("1.0", W("0.5", "0.5", "0.25"), Decimal("0.5")),
        ("1.0", W("1.0", "0.5", "0.5"), Decimal("0.5")),
        # 감량 — 직전의 '다른' 용량이 더 크다
        ("1.7", W("2.4", "2.4"), Decimal("2.4")),
        ("1.7", W("1.7", "1.7", "2.4"), Decimal("2.4")),
    ],
)
def test_previous_different_dose(
    dose: str, previous: list[tuple[str, Decimal]], expected: Decimal | None
) -> None:
    assert previous_different_dose(WEGOVY, Decimal(dose), previous) == expected


# ── same_dose_streak: 행이 아니라 날짜에서 나온다 ──────────────


@pytest.mark.parametrize(
    ("days_since_change", "expected_streak"),
    [
        (0, 1),    # 바꾼 당일이 1회차
        (6, 1),    # 아직 한 주 안 지났다
        (7, 2),
        (20, 3),
        (21, 4),   # 정상 증량의 최댓값 — 다음 주에 올린다
        (28, 5),   # MAINTENANCE_STREAK 경계 — 예정일에 안 올렸다
        (35, 6),
    ],
)
def test_streak_counts_doses_not_rows(days_since_change: int, expected_streak: int) -> None:
    """streak 은 `count_doses` 와 같은 공식이고 앵커만 다르다 (이 행의 시작일).

    행을 세면 안 되는 이유: 행은 '용량 변경 1건'이라 인접한 두 행의 (약물, 용량) 이
    같을 수 없다. 세면 구조적으로 항상 1 이고 정착 판정이 죽는다 (코드 리뷰 지적).
    """
    change_day = date(2026, 3, 2)
    context = dose_context(
        WEGOVY,
        Decimal("1.0"),
        W("0.5"),
        effective_from=change_day,
        today=change_day + timedelta(days=days_since_change),
    )
    assert context.same_dose_streak == expected_streak
    assert context.previous_different_dose_mg == Decimal("0.5")


def test_streak_is_one_when_change_date_is_ahead_of_today() -> None:
    """아직 시작하지 않은 구간은 1 로 본다 — 0 이나 음수가 나오면 안 된다."""
    context = dose_context(
        WEGOVY, Decimal("1.0"), [],
        effective_from=date(2026, 3, 9), today=date(2026, 3, 2),
    )
    assert context.same_dose_streak == 1


# ── 약물 전환: 이력이 거기서 끊긴다 ────────────────────────────


def test_drug_switch_cuts_history() -> None:
    """마운자로 15 → 위고비 2.4 는 감량이 아니다. 사다리가 다르면 숫자를 비교할 수 없다."""
    assert previous_different_dose(WEGOVY, Decimal("2.4"), M("15", "15")) is None


def test_returning_to_previous_drug_does_not_rejoin_old_run() -> None:
    """약을 바꿨다 되돌아와도 옛 구간과 이어붙지 않는다.

    이력을 SQL 에서 `drug_name = ?` 로 걸렀다면 마운자로 구간을 건너뛰고 이어붙어,
    돌아오자마자 옛 용량과 비교됐을 것이다.
    """
    previous = M("7.5", "5.0") + W("1.7", "1.7", "1.7")
    assert previous_different_dose(WEGOVY, Decimal("1.7"), previous) is None


# ── judge_stage: 사다리 위치 축 ────────────────────────────────


@pytest.mark.parametrize(
    ("drug", "dose", "expected"),
    [
        # 위고비 사다리: 0.25 → 0.5 → 1.0 → 1.7 → 2.4
        (WEGOVY, "0.25", INITIAL),
        (WEGOVY, "0.5", TITRATION),
        (WEGOVY, "1.0", TITRATION),
        (WEGOVY, "1.7", TITRATION),
        (WEGOVY, "2.4", MAINTENANCE),
        # 마운자로 사다리: 2.5 → 5 → 7.5 → 10 → 12.5 → 15
        (MOUNJARO, "2.5", INITIAL),
        (MOUNJARO, "7.5", TITRATION),
        (MOUNJARO, "15", MAINTENANCE),
        # 사다리에 없는 용량도 범위로 판정된다
        (WEGOVY, "0.1", INITIAL),  # 첫 칸 미만
        (WEGOVY, "0.75", TITRATION),  # 칸 사이
        (WEGOVY, "3.0", MAINTENANCE),  # 마지막 칸 초과 — 오타 방어 (`==` 였으면 TITRATION 으로 샌다)
    ],
)
def test_judge_stage_by_ladder_position(drug: DrugName, dose: str, expected: MedicationStage) -> None:
    """증량 직후(연속 1회차)에는 사다리 위치가 그대로 단계가 된다."""
    assert (
        judge_stage(drug, Decimal(dose), previous_different_dose_mg=None, same_dose_streak=1)
        is expected
    )


# ── judge_stage: 정착 축 (MAINTENANCE_STREAK) ─────────────────


@pytest.mark.parametrize(
    ("streak", "expected"),
    [
        (1, TITRATION),
        (MAINTENANCE_STREAK - 1, TITRATION),
        (MAINTENANCE_STREAK, MAINTENANCE),  # 경계
        (MAINTENANCE_STREAK + 1, MAINTENANCE),
    ],
)
def test_streak_promotes_middle_dose_to_maintenance(streak: int, expected: MedicationStage) -> None:
    """중간 칸 용량도 같은 용량을 연속으로 채우면 유지기가 된다.

    이게 없으면 1.7 mg 에서 멈춘 사용자가 영원히 TITRATION 이라 단계가 한 칸에 몰린다 (R1).
    """
    assert judge_stage(WEGOVY, Decimal("1.7"), previous_different_dose_mg=None, same_dose_streak=streak) is expected


def test_first_rung_stays_initial_however_long() -> None:
    """첫 칸은 streak 과 무관하게 INITIAL 이다.

    첫 칸에 오래 머무는 건 '정착'이 아니라 부작용으로 못 올리는 상태에 가깝다.
    규칙 순서(첫 칸 검사가 streak 검사보다 앞)가 이걸 보장한다.
    """
    assert (
        judge_stage(
            WEGOVY,
            Decimal("0.25"),
            previous_different_dose_mg=None,
            same_dose_streak=MAINTENANCE_STREAK * 3,
        )
        is INITIAL
    )


# ── judge_stage: 감량 축 ───────────────────────────────────────


@pytest.mark.parametrize(
    ("streak", "expected"),
    [
        (1, REDUCED),
        (MAINTENANCE_STREAK - 1, REDUCED),
        (MAINTENANCE_STREAK, MAINTENANCE),  # 정착하면 감량기에서 벗어난다
    ],
)
def test_reduced_lasts_until_settled(streak: int, expected: MedicationStage) -> None:
    """2.4 → 1.7 로 내린 뒤 MAINTENANCE_STREAK 회를 채우면 유지기로 넘어간다.

    `previous_different_dose_mg` 가 '직전 회차'였다면 감량 2회차에 이미 풀렸을 것이다.
    """
    assert (
        judge_stage(
            WEGOVY,
            Decimal("1.7"),
            previous_different_dose_mg=Decimal("2.4"),
            same_dose_streak=streak,
        )
        is expected
    )


def test_increase_is_not_reduced() -> None:
    """올린 건 감량기가 아니다. 방향을 본다."""
    assert (
        judge_stage(
            WEGOVY, Decimal("1.7"), previous_different_dose_mg=Decimal("1.0"), same_dose_streak=1
        )
        is TITRATION
    )


def test_reduction_to_first_rung_is_reduced_not_initial() -> None:
    """첫 칸까지 내려도 감량기다 — 시작한 게 아니라 내려온 것이다.

    규칙 순서상 감량 검사가 첫 칸 검사보다 앞이라 이렇게 된다.
    """
    assert (
        judge_stage(
            WEGOVY, Decimal("0.25"), previous_different_dose_mg=Decimal("1.0"), same_dose_streak=1
        )
        is REDUCED
    )


# ── 시나리오: 표준 증량 경로를 처음부터 끝까지 ─────────────────


def test_standard_escalation_path() -> None:
    """위고비 표준 경로(한 칸에 4주)를 주차별로 재현한다.

    **이력이 아니라 날짜로 만든다.** 예전 버전은 `W("0.5","0.5","0.5")` 처럼 같은
    용량이 연속된 이력을 지어내서 돌렸는데, 그런 이력은 DB 가 만들 수 없다 — 행은
    용량 변경 1건이라 인접한 두 행의 용량이 같을 수 없다. 그래서 streak 가 실제로는
    항상 1 인데도 이 테스트는 통과했다 (코드 리뷰 지적).

    **정상 증량 중에는 유지기가 한 번도 나오면 안 된다.** MAINTENANCE_STREAK 가 4 이던
    때는 매 칸 4회차가 유지기로 잡혔다 — 다음 주에 올릴 사람한테 "약효가 줄고 식욕이
    돌아오는 구간"이라고 말하는 셈이었다 (안건 2).
    """
    start_day = date(2026, 1, 5)
    # (용량, 이 용량을 시작한 주차, 직전의 다른 용량, 머무는 주차)
    rungs = [
        ("0.25", 0, None, 4),
        ("0.5", 4, Decimal("0.25"), 4),
        ("1.0", 8, Decimal("0.5"), 4),
        ("1.7", 12, Decimal("1.0"), 2),
    ]
    stages = []
    for dose, first_week, prev, weeks in rungs:
        change_day = start_day + timedelta(weeks=first_week)
        for w in range(weeks):
            context = dose_context(
                WEGOVY,
                Decimal(dose),
                W(str(prev)) if prev is not None else [],
                effective_from=change_day,
                today=change_day + timedelta(weeks=w),
            )
            stages.append(judge_stage(WEGOVY, Decimal(dose), **context._asdict()))

    assert stages == [
        INITIAL, INITIAL, INITIAL, INITIAL,       # 0.25 — 첫 칸
        TITRATION, TITRATION, TITRATION, TITRATION,   # 0.5
        TITRATION, TITRATION, TITRATION, TITRATION,   # 1.0
        TITRATION, TITRATION,                     # 1.7
    ]


def test_streak_boundary_clears_normal_escalation() -> None:
    """경계는 정상 증량보다 최소 한 칸 위여야 한다.

    정상 증량 사용자의 streak 는 `ESCALATION_DOSES` 를 넘지 않는다. 경계가 그 이하면
    **정상 경로를 밟는 사람이 매 칸 반드시 한 번씩** 유지기로 잡힌다. 예외가 아니라
    필연이라 실사용 데이터로도 안 걸러진다. 튜닝할 때 이 관계를 깨지 않도록 고정한다.
    """
    assert MAINTENANCE_STREAK > ESCALATION_DOSES

    at_schedule = judge_stage(
        WEGOVY, Decimal("1.0"),
        previous_different_dose_mg=Decimal("0.5"),
        same_dose_streak=ESCALATION_DOSES,
    )
    one_late = judge_stage(
        WEGOVY, Decimal("1.0"),
        previous_different_dose_mg=Decimal("0.5"),
        same_dose_streak=ESCALATION_DOSES + 1,
    )
    assert at_schedule is TITRATION   # 예정대로 — 다음 주에 올린다
    assert one_late is MAINTENANCE    # 한 회차 늦음 — 정착으로 본다


# ── 사다리 상수 자체의 불변식 ──────────────────────────────────


def test_every_drug_has_a_ladder() -> None:
    """약물을 추가하고 사다리를 빠뜨리면 런타임에 KeyError 로 죽는다. 여기서 먼저 잡는다."""
    assert set(DOSE_LADDERS) == set(DrugName)


def test_ladders_are_ascending() -> None:
    """사다리가 오름차순이 아니면 첫 칸·마지막 칸 비교가 무의미해진다."""
    for drug, ladder in DOSE_LADDERS.items():
        assert list(ladder) == sorted(ladder), drug


# ── 서버 계산 항목 ─────────────────────────────────────────────
#
#   doseCount         = floor((today - startedAt) / 7) + 1
#   nextDoseDate      = startedAt + 7 x doseCount
#   daysUntilNextDose = nextDoseDate - today
#
# 명세에 적힌 식 그대로다. 세 값이 한 공식에서 파생되므로 같이 고정해 둔다.

START = date(2026, 9, 1)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 9, 1), 1),  # 시작일 당일이 1회차
        (date(2026, 9, 7), 1),  # 6일째 — 아직 1회차
        (date(2026, 9, 8), 2),  # 7일째에 2회차
        (date(2026, 9, 15), 3),
        (date(2026, 11, 10), 11),
    ],
)
def test_dose_count_counts_weeks_not_rows(today: date, expected: int) -> None:
    """회차는 행이 아니라 날짜에서 나온다 — 같은 용량으로 계속 맞아도 늘어난다."""
    assert count_doses(START, today=today) == expected


@pytest.mark.parametrize(
    ("today", "next_date", "d_day"),
    [
        (date(2026, 9, 1), date(2026, 9, 8), 7),
        (date(2026, 9, 7), date(2026, 9, 8), 1),
        (date(2026, 9, 8), date(2026, 9, 15), 7),
        (date(2026, 9, 14), date(2026, 9, 15), 1),
    ],
)
def test_next_dose_follows_the_spec_formula(
    today: date, next_date: date, d_day: int
) -> None:
    assert predict_next_dose(START, today=today) == (next_date, d_day)


@pytest.mark.parametrize("elapsed", range(0, 60))
def test_d_day_never_leaves_one_to_seven(elapsed: int) -> None:
    """예정일이 과거가 될 수 없다.

    회차가 오늘을 지난 첫 배수를 가리키므로, 늦게 맞아도 회차가 같이 밀린다.
    이게 깨지면 FE 의 D-day 배지에 0 이나 음수가 뜬다.
    """
    today = date.fromordinal(START.toordinal() + elapsed)
    _, d_day = predict_next_dose(START, today=today)
    assert 1 <= d_day <= DOSE_INTERVAL_DAYS


def test_every_stage_has_a_reason() -> None:
    """`stageReason` 이 비면 FE 가 빈 줄을 띄운다. 단계를 늘리면 여기서 걸린다."""
    for stage in MedicationStage:
        assert STAGE_REASONS[stage].strip()


# ── 용량 변경 방향 ────────────────────────────────────────────
#
# 명세의 표 그대로다. DB 에 저장하지 않고 이전 행과 비교해서 낸다 —
# 컬럼으로 두면 행의 용량과 방향이 어긋날 수 있다.


@pytest.mark.parametrize(
    ("dose", "previous", "expected"),
    [
        ("0.5", "0.25", DoseDirection.INCREASE),
        ("1.7", "1.0", DoseDirection.INCREASE),
        ("0.5", "1.0", DoseDirection.DECREASE),
        ("1.7", "2.4", DoseDirection.DECREASE),
        ("0.25", None, DoseDirection.MAINTAIN),  # 첫 등록 — 비교할 이전 용량이 없다
        ("1.0", "1.0", DoseDirection.MAINTAIN),  # 용량은 같고 약물만 바뀐 경우
    ],
)
def test_direction_compares_with_the_previous_dose(
    dose: str, previous: str | None, expected: DoseDirection
) -> None:
    assert (
        dose_direction(
            Decimal(dose),
            previous_dose_mg=Decimal(previous) if previous is not None else None,
        )
        is expected
    )
