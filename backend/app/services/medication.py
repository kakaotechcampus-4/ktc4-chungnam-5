"""투약 도메인 로직 — 용량 사다리 · 단계 판정 · 등록/수정 유스케이스.

세 덩어리로 나뉜다. 위에서 아래로 의존한다:

1. **용량 사다리** — 약물별 증량 스케줄 상수
2. **순수 함수** — 단계 판정. DB·네트워크 의존 0
3. **유스케이스** — DB 를 거치는 등록/수정/조회

**행 하나 = 용량 변경 1건이다.** 투약 1회가 아니다 — 같은 용량으로 매주 맞아도 행은
늘지 않는다. `GET /medications/dose-events` 가 곧 이 행들이다 (명세: "용량 변경 이력").

**회차는 행이 아니라 날짜로 계산한다** (명세 「서버 계산 항목」):

    doseCount = floor((today - startedAt) / 7) + 1

그래서 전체 투약 시작일(`startedAt`)이 진실의 출처다. 가장 오래된 행의 `effective_from`
이 그 값이고, 사용자가 회차 스테퍼로 바꾸면 그 행의 날짜가 움직인다.

규칙 5: DB 접근은 전부 `crud/` 를 거친다. 여기서 Session 을 직접 쿼리하지 않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Final, NamedTuple

from sqlalchemy.orm import Session

from app.crud import medication as crud
from app.models.enums import DrugName, MedicationStage
from app.models.medication import MedicationRecord, MedicationSnapshot
from app.schemas.medication import (
    CurrentMedicationResponse,
    MedicationUpsertRequest,
)

# ── 1. 용량 사다리 ──────────────────────────────────────────────
#
# GLP-1 약물은 정해진 순서대로 용량을 올린다. 이 사다리에서 현재 용량의 위치를 보면
# 단계가 나온다. 숫자를 여기 한 곳에만 둔다 — 리스크 R1("단계별 차이가 체감 안 됨")
# 튜닝 때 이 블록만 고치면 되게 하려는 것이다.
#
# ⚠️ 아래 수치는 일반적으로 알려진 증량 스케줄이고 **아직 팀 확정 전이다**
#    (`docs/be-medication-domain.md` 「단계 판정 규칙」). 출처 확인 후 갱신할 것.
#    이 값은 의료 조언이 아니라 단계 **분류**용 경계값이다 (절대 규칙 1).

DOSE_LADDERS: Final[dict[DrugName, tuple[Decimal, ...]]] = {
    DrugName.WEGOVY: tuple(Decimal(v) for v in ("0.25", "0.5", "1.0", "1.7", "2.4")),
    DrugName.MOUNJARO: tuple(Decimal(v) for v in ("2.5", "5.0", "7.5", "10", "12.5", "15")),
}

DOSE_INTERVAL_DAYS: Final = 7
"""표준 투약 간격. 위고비·마운자로 둘 다 주 1회다.

명세 「서버 계산 항목」의 분모다 — `doseCount`, `nextDoseDate` 가 전부 이 값에서 나온다.
증량 간격 4주와 나눠져 MAINTENANCE_STREAK 의 근거도 된다 (28일 / 7일 = 한 칸에 4회).
"""

MAINTENANCE_STREAK: Final = 4
"""같은 용량으로 몇 회 연속 맞으면 '정착했다'로 볼 것인가.

표준 증량은 한 칸에 4회다 (4주 간격 x 주 1회). 그래서 이 값은 실질적으로
**"예정보다 몇 회 늦어야 유지로 볼 것인가"** 를 정한다.

⚠️ 4 는 곧 "예정대로"다. 정상적으로 증량 중인 사용자도 매 칸의 4회차에 한 번씩
   MAINTENANCE 로 잡힌다 (`docs/be-medication-stage-rule.md` 안건 2). 팀 피드백 대기 중이고,
   R1 튜닝 상수라 여기 숫자만 바꾸면 된다.

감량기(REDUCED) 지속 기간도 이 값이 정한다 — 내린 용량으로 이만큼 채우면 유지기로 넘어간다.
"""


class DoseContext(NamedTuple):
    """`judge_stage` 가 사다리 위치 말고 더 봐야 하는 두 값."""

    same_dose_streak: int
    """현재 용량으로 연속 투약한 회차. 이번 회차를 포함한다."""
    previous_different_dose_mg: Decimal | None
    """직전의 *다른* 용량. 첫 용량이거나 쭉 같은 용량뿐이면 None."""


# ── 2. 순수 함수 ────────────────────────────────────────────────
#
# 인자로 받은 값만 보고 계산한다. 공통 토대 없이도 단위 테스트로 검증된다.
# 절대 규칙 1: 여기서 하는 건 **분류**까지다. 증량·감량·단약 판단은 하지 않는다.
#
# TODO(회의 후): 단계 판정 방식은 아직 미확정이다. 사다리 기반 판정을 잠정으로 두고
# 있으며, 결정되면 judge_stage 와 DOSE_LADDERS 를 함께 고친다.


def dose_context(
    drug_name: DrugName,
    dose_mg: Decimal,
    previous: Sequence[tuple[str, Decimal]],
) -> DoseContext:
    """판정에 필요한 두 값을 과거 이력에서 한 번에 뽑는다.

    `previous` 는 **이번 회차를 뺀** (약물, 용량) 목록이고 **최신이 먼저**다
    (`crud.list_doses_desc`). 최신부터 거꾸로 훑다가 **둘 중 먼저 오는 지점에서 멈춘다.**

    1. **약물이 다르다** → 거기서 이력이 끝난다. 사다리가 통째로 다르면 용량을 비교할 수 없다.
       위고비 2.4 다음 마운자로 5.0 은 증량이 아니고, 마운자로 15 다음 위고비 2.4 도 감량이 아니다.
       약을 바꾸면 새 사다리를 처음부터 다시 타는 것으로 본다.
    2. **용량이 다르다** → 그게 `previous_different_dose_mg` 다.

    1번이 2번보다 앞이라, 약을 바꿨다 **되돌아온** 경우에도 옛 구간과 이어붙지 않는다.
    (위고비 1.7 x3 → 마운자로 → 다시 위고비 1.7 은 연속 4회차가 아니라 1회차다.)

    - `same_dose_streak` — 현재 용량 구간의 길이. 창(window)이 아니다. 최근 N 개를
      보는 게 아니라 **끊기는 지점까지만** 센다.
    - `previous_different_dose_mg` — 같은 약물 안에서 직전의 *다른* 용량. 없으면 None.

    직전 **회차**가 아니라 직전의 **다른 용량**인 이유: 회차로 비교하면 감량 2회차부터
    "직전과 같음"이 되어 REDUCED 가 1회 만에 풀린다.
    """
    streak = 1
    for past_drug, past_dose in previous:
        if past_drug != drug_name:
            return DoseContext(same_dose_streak=streak, previous_different_dose_mg=None)
        if past_dose != dose_mg:
            return DoseContext(same_dose_streak=streak, previous_different_dose_mg=past_dose)
        streak += 1
    return DoseContext(same_dose_streak=streak, previous_different_dose_mg=None)


def judge_stage(
    drug_name: DrugName,
    dose_mg: Decimal,
    *,
    previous_different_dose_mg: Decimal | None,
    same_dose_streak: int,
) -> MedicationStage:
    """사다리 위 위치 + 직전 대비 방향·안정성으로 단계를 판정한다.

    **순서가 곧 규칙이다.** 위에서부터 먼저 걸리는 것이 답이다.

    1. 직전의 다른 용량보다 낮음 + 아직 정착 전 → REDUCED
    2. 첫 칸 이하                             → INITIAL
    3. 마지막 칸 이상                          → MAINTENANCE
    4. 같은 용량 연속 >= MAINTENANCE_STREAK    → MAINTENANCE
    5. 그 외                                  → TITRATION

    5번(TITRATION)은 **"증량기"가 아니다.** 걸러지지 않은 것이 떨어지는 자리라
    방금 올린 회차와 같은 용량 2~3회차가 같이 들어온다 — "중간 용량, 아직 정착 전"이다.

    양 끝을 `<=` · `>=` 로 비교하는 이유: 사다리 양 끝은 최소·최대 승인 용량이라
    정상 경로에서는 `==` 와 같지만, 오타(`2.4` -> `24`)나 낡은 사다리 같은 예외 입력이
    `==` 에서는 전부 TITRATION 으로 샌다. 최대 용량의 10배가 "아직 올라가는 중"으로
    분류되는 게 제일 나쁜 결과다.

    지원하지 않는 약물은 여기 오지 않는다 — 스키마(`DrugName`)가 경계에서 막는다.
    투약 기록이 아예 없는 경우(PRE_DOSE)도 오지 않는다. 호출자가 처리한다.

    판정 근거 전체: `docs/be-medication-stage-rule.md`
    """
    ladder = DOSE_LADDERS[drug_name]
    if previous_different_dose_mg is not None and dose_mg < previous_different_dose_mg:
        if same_dose_streak < MAINTENANCE_STREAK:
            return MedicationStage.REDUCED
    if dose_mg <= ladder[0]:
        return MedicationStage.INITIAL
    if dose_mg >= ladder[-1]:
        return MedicationStage.MAINTENANCE
    if same_dose_streak >= MAINTENANCE_STREAK:
        return MedicationStage.MAINTENANCE
    return MedicationStage.TITRATION








def count_doses(started_at: date, *, today: date) -> int:
    """투약 회차. 명세 「서버 계산 항목」 공식 그대로다.

        doseCount = floor((today - startedAt) / 7) + 1

    행을 세지 않는다. 사용자가 매주 등록하지 않아도 회차가 맞아야 하고,
    FE 의 회차 스테퍼가 `startedAt` 을 역산해서 보내기 때문이다.

    시작일 당일이 1회차다. 시작일이 미래면 호출자가 막는다.
    """
    return (today - started_at).days // DOSE_INTERVAL_DAYS + 1


# ── 3. 유스케이스 ───────────────────────────────────────────────


class FutureStartDateError(ValueError):
    """투약 시작일이 미래인 경우.

    회차 공식이 `(today - startedAt) / 7 + 1` 이라 미래 날짜면 0 이나 음수가 나온다.
    FE 의 날짜 선택기도 오늘까지만 열려 있어 정상 경로에서는 오지 않는다.
    """

    def __init__(self, started_at: date, today: date) -> None:
        super().__init__(f"투약 시작일 {started_at} 이 오늘 {today} 보다 미래다")
        self.started_at = started_at
        self.today = today


def upsert(
    db: Session,
    user_id: uuid.UUID,
    payload: MedicationUpsertRequest,
    *,
    today: date | None = None,
) -> MedicationRecord:
    """투약 정보 등록·수정 겸용. 커밋까지 한다.

    **행은 용량 변경 1건이다.** 같은 약·같은 용량으로 다시 보내면 행을 만들지 않는다 —
    명세의 `POST /medications` 가 "용량 변경 자동 기록"이라 변경이 없으면 기록할 게 없다.

    네 갈래다:

    1. 기록이 없다              → 첫 행을 연다 (`effective_from` = startedAt)
    2. 약·용량이 그대로다        → 행을 안 만든다. `startedAt` 만 반영한다
    3. 약이나 용량이 바뀌었다     → 현재 행을 어제로 닫고 새 행을 연다
    4. `startedAt` 이 바뀌었다   → 가장 오래된 행의 날짜를 옮긴다

    4번은 FE 의 회차 스테퍼다. 회차를 올리면 시작일이 과거로 밀리고, 그 값이 그대로
    온다. 회차를 직접 받는 자리가 명세에 없어서 이렇게 들어온다.
    """
    today = today or date.today()
    started_at = payload.started_at or today
    if started_at > today:
        raise FutureStartDateError(started_at, today)

    current = crud.get_current(db, user_id)

    if current is None:
        record = crud.create(
            db,
            user_id=user_id,
            drug_name=payload.drug_name,
            dose_mg=payload.dose_mg,
            injection_count=1,  # 몇 번째 용량 변경인지. 회차가 아니다
            stage=judge_stage(
                payload.drug_name,
                payload.dose_mg,
                previous_different_dose_mg=None,
                same_dose_streak=1,
            ),
            effective_from=started_at,
        )
        db.commit()
        return record

    # startedAt 정정 — 가장 오래된 행의 날짜가 곧 전체 시작일이다.
    first = crud.get_first(db, user_id)
    if first is not None and first.effective_from != started_at:
        first.effective_from = started_at

    unchanged = current.drug_name == payload.drug_name and current.dose_mg == payload.dose_mg
    if unchanged:
        # 변경이 없으면 이력에 남길 게 없다. startedAt 반영만 하고 끝낸다.
        db.commit()
        return current

    context = dose_context(
        payload.drug_name,
        payload.dose_mg,
        crud.list_doses_desc(db, user_id),
    )
    # 변경일은 오늘이다 — 명세 body 에 '언제 바꿨는지' 자리가 없다.
    change_date = max(today, current.effective_from)
    if change_date > current.effective_from:
        crud.close_current(db, current, effective_to=change_date - timedelta(days=1))
    else:
        # 같은 날 두 번 바꾸면 이력이 두 줄이 될 이유가 없다. 현재 행을 고친다.
        current.drug_name = payload.drug_name
        current.dose_mg = payload.dose_mg
        current.stage = judge_stage(payload.drug_name, payload.dose_mg, **context._asdict())
        db.commit()
        return current

    record = crud.create(
        db,
        user_id=user_id,
        drug_name=payload.drug_name,
        dose_mg=payload.dose_mg,
        injection_count=current.injection_count + 1,
        stage=judge_stage(payload.drug_name, payload.dose_mg, **context._asdict()),
        effective_from=change_date,
    )
    db.commit()
    return record


def get_current_view(
    db: Session,
    user_id: uuid.UUID,
    *,
    today: date | None = None,
) -> CurrentMedicationResponse:
    """`GET /medications/current` 응답.

    투약 기록이 없으면 stage=PRE_DOSE 에 나머지가 전부 null 이다. 에러가 아니다 —
    "아직 투약 전"은 정상 상태이고, 투약 전 식사 평가가 제품 기능이다 (D8).
    """
    today = today or date.today()
    current = crud.get_current(db, user_id)
    if current is None:
        return CurrentMedicationResponse(stage=MedicationStage.PRE_DOSE)

    started_at = crud.get_dosing_start_date(db, user_id) or current.effective_from
    return CurrentMedicationResponse(
        stage=current.stage,
        drug_name=current.drug_name,
        dose_mg=current.dose_mg,
        dose_count=count_doses(started_at, today=today),
        started_at=started_at,
        effective_from=current.effective_from,
    )


def create_snapshot_for_meal(db: Session, user_id: uuid.UUID) -> MedicationSnapshot:
    """식사 등록 시점의 투약 맥락을 얼려 스냅샷 한 건을 만든다.

    `POST /meals` 가 식사를 INSERT 하기 **전에** 부른다 —
    `meals.medication_snapshot_id` 가 NOT NULL 이라 먼저 있어야 한다.

    커밋하지 않는다. 식사와 한 트랜잭션이어야 한다
    (`crud/__init__.py` 의 POST /meals 예시 참고).

    규칙 5: `services/meal.py` 에서 직접 부르면 안 된다 — services 끼리는 참조하지
    않는다. 조합은 `api/` · `worker/` 레이어에서 한다.
    """
    return crud.add_snapshot(db, user_id=user_id, source=crud.get_current(db, user_id))
