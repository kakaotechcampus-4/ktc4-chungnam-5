"""단계 판정 단위 테스트.

`services/medication.py` 의 순수 함수 구역은 DB·인증 없이 검증된다.

경계값은 표로 고정한다 (R1 튜닝 때 사다리나 MAINTENANCE_STREAK 를 바꾸면 여기가 먼저 깨져야 한다).

회차·다음 예정일도 여기 있다 — 날짜 산수라 DB 가 필요 없다 (명세 「서버 계산 항목」).

판정 규칙 근거: `docs/be-medication-stage-rule.md`
"""

from datetime import date
from decimal import Decimal

import pytest

from app.models.enums import DrugName, MedicationStage
from app.services.medication import (
    DOSE_INTERVAL_DAYS,
    DOSE_LADDERS,
    MAINTENANCE_STREAK,
    count_doses,
    dose_context,
    judge_stage,
    predict_next_dose,
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


# ── dose_context: 이력에서 두 값 뽑기 ──────────────────────────
#
# previous_doses 는 **최신이 먼저**다. 창(window)이 아니라 용량이 바뀌는 지점까지만 센다.


@pytest.mark.parametrize(
    ("dose", "previous", "expected_streak", "expected_prev"),
    [
        # 첫 투약 — 비교할 과거가 없다
        ("0.25", [], 1, None),
        # 같은 용량이 이어진다
        ("0.5", W("0.5"), 2, None),
        ("0.5", W("0.5", "0.5", "0.5"), 4, None),
        # 용량이 바뀌는 지점에서 멈춘다. 그 너머는 안 본다
        ("1.0", W("0.5", "0.5", "0.25"), 1, Decimal("0.5")),
        ("1.0", W("1.0", "0.5", "0.5"), 2, Decimal("0.5")),
        # 감량 — 직전의 '다른' 용량이 더 크다
        ("1.7", W("2.4", "2.4"), 1, Decimal("2.4")),
        ("1.7", W("1.7", "1.7", "2.4"), 3, Decimal("2.4")),
    ],
)
def test_dose_context(
    dose: str,
    previous: list[tuple[str, Decimal]],
    expected_streak: int,
    expected_prev: Decimal | None,
) -> None:
    context = dose_context(WEGOVY, Decimal(dose), previous)
    assert context.same_dose_streak == expected_streak
    assert context.previous_different_dose_mg == expected_prev


# ── 약물 전환: 이력이 거기서 끊긴다 ────────────────────────────


def test_drug_switch_cuts_history() -> None:
    """마운자로 15 → 위고비 2.4 는 감량이 아니다. 사다리가 다르면 숫자를 비교할 수 없다."""
    context = dose_context(WEGOVY, Decimal("2.4"), M("15", "15"))
    assert context.same_dose_streak == 1
    assert context.previous_different_dose_mg is None


def test_returning_to_previous_drug_does_not_rejoin_old_run() -> None:
    """약을 바꿨다 되돌아와도 옛 구간과 이어붙지 않는다.

    위고비 1.7 x3 → 마운자로 → 다시 위고비 1.7 은 **연속 4회차가 아니라 1회차**다.
    이력을 SQL 에서 `drug_name = ?` 로 걸렀다면 마운자로 구간을 건너뛰고 이어붙어,
    돌아오자마자 유지기로 판정됐을 것이다.
    """
    previous = M("7.5", "5.0") + W("1.7", "1.7", "1.7")
    context = dose_context(WEGOVY, Decimal("1.7"), previous)
    assert context.same_dose_streak == 1
    assert context.previous_different_dose_mg is None
    assert judge_stage(WEGOVY, Decimal("1.7"), **context._asdict()) is TITRATION


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
    """위고비 표준 경로(한 칸에 4회)를 주차별로 재현한다.

    ⚠️ MAINTENANCE_STREAK 가 4 라서 **매 칸의 4회차가 유지기로 잡힌다.**
    정상적으로 증량 중인데도 그렇다 — 이건 버그가 아니라 현재 선택한 경계값의 결과이고,
    팀 피드백 안건이다 (`docs/be-medication-stage-rule.md` 안건 2).
    이 테스트는 그 동작을 눈에 보이게 고정해 둔다. 값을 5·6 으로 바꾸면 여기가 깨진다.
    """
    history = W("0.25", "0.25", "0.25", "0.25", "0.5", "0.5", "0.5", "0.5", "1.0", "1.0")
    stages = []
    for i, (drug, dose) in enumerate(history):
        context = dose_context(drug, dose, list(reversed(history[:i])))
        stages.append(judge_stage(drug, dose, **context._asdict()))

    assert stages == [
        INITIAL, INITIAL, INITIAL, INITIAL,      # 0.25 — 첫 칸이라 streak 무관
        TITRATION, TITRATION, TITRATION,          # 0.5 1~3회차
        MAINTENANCE,                              # 0.5 4회차 ← 다음 주에 올릴 건데 유지기
        TITRATION, TITRATION,                     # 1.0 1~2회차
    ]


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
