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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Final, NamedTuple

from sqlalchemy.orm import Session

from app.crud import medication as crud
from app.models.enums import DrugName, MedicationStage
from app.models.medication import MedicationRecord, MedicationSnapshot
from app.schemas.medication import (
    CurrentMedicationResponse,
    DoseDirection,
    DoseEvent,
    MedicationUpsertRequest,
    MedicationUpsertResponse,
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
증량 간격 4주와 나눠져 ESCALATION_DOSES 의 근거가 된다 (28일 / 7일 = 한 칸에 4회).
"""

ESCALATION_DOSES: Final = 4
"""표준 증량에서 **한 칸에 머무는 회차**. 증량 간격 28일 / 투약 간격 7일.

위고비·마운자로 둘 다 한 칸에 4주다. 정상 증량 중인 사용자의 `same_dose_streak` 는
이 값을 넘지 않는다 — 다음 회차에는 용량을 올려 구간이 새로 시작하기 때문이다.
"""

MAINTENANCE_STREAK: Final = ESCALATION_DOSES + 1
"""같은 용량으로 몇 회 연속 맞으면 '정착했다'로 볼 것인가.

**"예정보다 몇 회 늦어야 유지로 볼 것인가"** 를 정하는 값이다. 정상 증량의 최대
streak 가 `ESCALATION_DOSES`(4) 이므로, 경계는 그보다 **한 칸 위**여야 한다.
5 = "예정일에 안 올렸다".

⚠️ 예전에는 4 였다. 4 는 곧 "예정대로"라, 정상적으로 증량 중인 사용자도 매 칸의
   4회차에 반드시 한 번씩 MAINTENANCE 로 잡혔다 — 예외가 아니라 필연이었다
   (`docs/be-medication-stage-rule.md` 안건 2). 다만 `same_dose_streak` 가 행을
   세느라 항상 1 이던 동안은 이 경계에 닿지도 않아 증상이 안 보였다 — streak 를
   날짜로 세면서 드러났고, 상수 정의와 값을 맞춰 5 로 올렸다.

   "단계가 행에 박혀 영구히 남으니 보수적으로 크게 잡자"는 근거도 사라졌다 —
   조회할 때마다 다시 판정하므로 오판이 다음 회차에 저절로 복구된다.

R1 튜닝 상수다. 실사용 데이터를 보고 조정하되, `ESCALATION_DOSES` 와의 관계
(정상 증량보다 최소 1 커야 한다)는 유지해야 한다 — `test_streak_boundary_clears_normal_escalation`
가 그걸 잡는다.

감량기(REDUCED) 지속 기간도 이 값이 정한다 — 내린 용량으로 이만큼 채우면 유지기로 넘어간다.
"""


class DoseContext(NamedTuple):
    """`judge_stage` 가 사다리 위치 말고 더 봐야 하는 두 값."""

    same_dose_streak: int
    """현재 용량으로 연속 투약한 **회차**. 이번 회차를 포함한다.

    **행이 아니라 날짜에서 나온다** — `dose_context` 참고.
    """
    previous_different_dose_mg: Decimal | None
    """직전의 *다른* 용량. 첫 용량이거나 쭉 같은 용량뿐이면 None."""


# ── 2. 순수 함수 ────────────────────────────────────────────────
#
# 인자로 받은 값만 보고 계산한다. 공통 토대 없이도 단위 테스트로 검증된다.
# 절대 규칙 1: 여기서 하는 건 **분류**까지다. 증량·감량·단약 판단은 하지 않는다.
#
# TODO(회의 후): 단계 판정 방식은 아직 미확정이다. 사다리 기반 판정을 잠정으로 두고
# 있으며, 결정되면 judge_stage 와 DOSE_LADDERS 를 함께 고친다.


def previous_different_dose(
    drug_name: DrugName,
    dose_mg: Decimal,
    previous: Sequence[tuple[str, Decimal]],
) -> Decimal | None:
    """같은 약물 안에서 **직전의 다른 용량**. 없으면 None.

    `previous` 는 **이번 회차를 뺀** (약물, 용량) 목록이고 **최신이 먼저**다
    (`crud.list_doses_desc`). 최신부터 거꾸로 훑다가 **둘 중 먼저 오는 지점에서 멈춘다.**

    1. **약물이 다르다** → 거기서 이력이 끝난다. 사다리가 통째로 다르면 용량을 비교할 수 없다.
       위고비 2.4 다음 마운자로 5.0 은 증량이 아니고, 마운자로 15 다음 위고비 2.4 도 감량이 아니다.
       약을 바꾸면 새 사다리를 처음부터 다시 타는 것으로 본다.
    2. **용량이 다르다** → 그게 답이다.

    1번이 2번보다 앞이라, 약을 바꿨다 **되돌아온** 경우에도 옛 구간과 이어붙지 않는다.

    직전 **회차**가 아니라 직전의 **다른** 용량인 이유: 회차로 비교하면 감량 2회차부터
    "직전과 같음"이 되어 REDUCED 가 1회 만에 풀린다.
    """
    for past_drug, past_dose in previous:
        if past_drug != drug_name:
            return None
        if past_dose != dose_mg:
            return past_dose
    return None


def dose_context(
    drug_name: DrugName,
    dose_mg: Decimal,
    previous: Sequence[tuple[str, Decimal]],
    *,
    effective_from: date,
    today: date,
) -> DoseContext:
    """`judge_stage` 에 넘길 두 값을 모은다. **축이 서로 다르다.**

    - `same_dose_streak` — **날짜**에서 나온다. 이 용량을 시작한 날(`effective_from`)
      부터 몇 회차를 채웠는지다. `count_doses` 와 같은 공식이고 앵커만 다르다
      (`startedAt` 대신 이 행의 시작일).
    - `previous_different_dose_mg` — **이력**에서 나온다 (`previous_different_dose`).

    **streak 을 행으로 세면 안 된다.** 행은 용량 변경 1건이라 인접한 두 행의
    (약물, 용량) 이 같을 수 없다. 세면 구조적으로 항상 1 이 나오고, `judge_stage` 의
    규칙 4(정착 → MAINTENANCE)가 통째로 죽는다. 감량도 영원히 REDUCED 에서 안 풀린다.
    `doseCount` 가 행을 안 세는 것과 같은 이유다 (코드 리뷰 지적).

    `effective_from` 이 오늘보다 뒤면 1 로 본다 — 아직 한 회차도 안 채운 것이다.
    """
    return DoseContext(
        same_dose_streak=max(1, count_doses(effective_from, today=today)),
        previous_different_dose_mg=previous_different_dose(drug_name, dose_mg, previous),
    )


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


def predict_next_dose(started_at: date, *, today: date) -> tuple[date, int]:
    """(다음 투약 예정일, D-day). 명세 「서버 계산 항목」 공식 그대로다.

        nextDoseDate      = startedAt + 7 x doseCount
        daysUntilNextDose = nextDoseDate - today

    **예정일이지 사실이 아니다.** 실제로 언제 맞았는지는 기록하지 않는다 —
    명세의 요청 body 에 투약일 자리가 없고, 행은 용량 변경만 남기기 때문이다.

    D-day 는 구조상 1~7 이다. `doseCount` 가 오늘을 지난 첫 배수를 가리키므로
    예정일이 과거가 될 수 없다. 늦게 맞아도 회차가 같이 밀려서 음수가 나오지 않는다.

    절대 규칙 1: 이건 **날짜 계산**이지 "맞아라/맞지 마라"가 아니다.
    """
    next_dose_date = started_at + timedelta(
        days=DOSE_INTERVAL_DAYS * count_doses(started_at, today=today)
    )
    return next_dose_date, (next_dose_date - today).days


def dose_direction(
    dose_mg: Decimal, *, previous_dose_mg: Decimal | None
) -> DoseDirection:
    """이전 용량 대비 방향. 명세의 표 그대로다.

    | 조건 | direction |
    | --- | --- |
    | 새 값 > 현재 값 | INCREASE |
    | 새 값 < 현재 값 | DECREASE |
    | 같음 | 기록 없음 — 애초에 이벤트가 안 생긴다 |

    이전 용량이 없으면(첫 등록) MAINTAIN 이다 — 명세 `dose-events` 예시의 `de_001`.

    ⚠️ **약물이 바뀐 경우는 명세에 없다.** 위고비 1.0 → 마운자로 2.5 는 사다리가 달라
    mg 비교가 의미를 잃는다(첫 칸으로 되돌아가는 것이므로 실질은 감량에 가깝다).
    지금은 명세 그대로 mg 로 비교한다 — 문서 「논의할 내용」 11 참고.
    """
    if previous_dose_mg is None:
        return DoseDirection.MAINTAIN
    if dose_mg > previous_dose_mg:
        return DoseDirection.INCREASE
    if dose_mg < previous_dose_mg:
        return DoseDirection.DECREASE
    # 용량은 그대로인데 이벤트가 생긴 경우 = 약물만 바뀌었다.
    return DoseDirection.MAINTAIN


RULE_VERSION: Final = "v1"
"""판정 규칙의 버전. 응답의 `ruleVersion` 이다.

사다리 수치나 `judge_stage` 의 순서를 바꾸면 올린다 — 같은 입력에 다른 단계가 나온
이유를 나중에 추적하려면 어느 규칙으로 찍힌 값인지가 필요하다.

⚠️ 올릴지 말지는 팀이 정한다. streak 산출 방식과 `MAINTENANCE_STREAK` 가 바뀌어
   같은 저장 데이터가 다른 단계를 내므로 올릴 근거는 있다 — 회의 안건이다.
"""

STAGE_REASONS: Final[dict[MedicationStage, str]] = {
    MedicationStage.PRE_DOSE: "아직 투약을 시작하기 전이에요",
    MedicationStage.INITIAL: "몸이 약에 적응하는 구간",
    MedicationStage.TITRATION: "용량을 맞춰가는 구간",
    MedicationStage.MAINTENANCE: "약효가 줄고 식욕이 돌아오는 구간",
    MedicationStage.REDUCED: "용량을 낮추고 다시 적응하는 구간",
}
"""단계를 사용자에게 설명하는 한 줄. 응답의 `stageReason` 이다.

MAINTENANCE 문구는 명세 예시 그대로고 **나머지 넷은 초안이다** — 팀 확인 필요.

절대 규칙 1·FE 규칙 2(처방 톤 금지)를 지킨다. 지금 상태를 서술만 하고
"올려라/줄여라"를 말하지 않는다. 문구를 고칠 때도 이 선을 넘지 않는다.
"""


# ── 3. 유스케이스 ───────────────────────────────────────────────


class InvalidStartDateError(ValueError):
    """받아들일 수 없는 시작일. API 층은 이 조상 하나만 잡아 422 로 옮긴다."""


class FutureStartDateError(InvalidStartDateError):
    """투약 시작일이 미래인 경우.

    회차 공식이 `(today - startedAt) / 7 + 1` 이라 미래 날짜면 0 이나 음수가 나온다.
    FE 의 날짜 선택기도 오늘까지만 열려 있어 정상 경로에서는 오지 않는다.
    """

    def __init__(self, started_at: date, today: date) -> None:
        super().__init__(f"투약 시작일 {started_at} 이 오늘 {today} 보다 미래다")
        self.started_at = started_at
        self.today = today


class StartDateAfterFirstChangeError(InvalidStartDateError):
    """시작일을 첫 용량 변경일 뒤로 밀려는 경우.

    시작일 정정은 **가장 오래된 행의 날짜를 옮기는 것**인데, 그 행이 이미 닫혀 있으면
    자기 종료일을 넘어설 수 없다. 넘기면 `effective_from > effective_to` 가 되어
    기간이 뒤집힌다 — 그 행은 어느 날짜에도 걸리지 않는 유령이 된다.

        BEFORE  [(09-01, 09-14, 0.25), (09-15, None, 0.5)]
        POST    {doseMg: 0.5, startedAt: 09-15}
        AFTER   [(09-15, 09-14, 0.25), ...]   ← 뒤집힘

    의미상으로도 모순이다. 09-15 에 용량을 바꿨다는 기록이 있는데 투약을 09-15 에
    시작했다면, 그 변경은 시작 전에 일어난 일이 된다.

    FE 의 회차 스테퍼는 이 상한을 모른다 — 회차를 낮추면 시작일이 뒤로 밀리므로,
    용량 변경 이력이 있는 사용자는 첫 변경일까지만 내릴 수 있다.
    """

    def __init__(self, started_at: date, latest_allowed: date) -> None:
        super().__init__(
            f"투약 시작일 {started_at} 이 첫 용량 변경 이후다 — {latest_allowed} 까지만 가능하다"
        )
        self.started_at = started_at
        self.latest_allowed = latest_allowed


class UpsertResult(NamedTuple):
    """`upsert()` 가 남기는 것. 행 하나로는 부족하다.

    명세의 `POST /medications` 응답은 현재 상태뿐 아니라 **이번 요청으로 무엇이
    바뀌었는지**(`doseChanged` · `stageChanged` · `doseEvent`)를 함께 내린다.
    그 판단은 이전 행을 들고 있는 여기서만 할 수 있어서, 나중에 다시 조회해
    복원할 수 없다 — 그래서 결과에 실어 내보낸다.
    """

    record: MedicationRecord
    """반영이 끝난 뒤의 현재 행."""

    dose_changed: bool
    """이번 요청으로 약이나 용량이 바뀌었는지. 같은 값으로 다시 보내면 False."""

    stage_changed: bool
    """이번 요청으로 단계 판정이 달라졌는지. 첫 등록은 PRE_DOSE 에서 오므로 True."""

    previous_dose_mg: Decimal | None = None
    """직전 용량. `direction` 을 여기서 뽑는다. 첫 등록이거나 변경이 없으면 None."""


def restage(
    db: Session,
    user_id: uuid.UUID,
    record: MedicationRecord,
    *,
    today: date,
) -> MedicationStage:
    """저장된 행을 **오늘 기준으로** 다시 판정한다.

    단계는 시간만 지나도 바뀐다 — 같은 용량으로 회차를 채우면 정착(MAINTENANCE)이고,
    감량 후 회차를 채우면 REDUCED 에서 풀린다. 그런데 그 순간에는 쓰기 이벤트가 없다.
    `judge_stage` 를 쓰기 시점에만 부르면 사용자가 앱을 안 켜는 동안 단계가 멈춘다.

    그래서 `doseCount`·`nextDoseDate` 와 같은 모델을 쓴다 — **읽을 때 계산한다.**
    저장된 `record.stage` 는 "그때 판정 결과"의 기록으로 남기고 여기서 덮지 않는다
    (조회가 쓰기를 하면 안 된다).

    `exclude_id` 로 **이 행 자신을 이력에서 뺀다.** 안 빼면 직전의 '다른' 용량을 찾는
    훑기가 자기 자신부터 시작한다.
    """
    drug_name = DrugName(record.drug_name)
    context = dose_context(
        drug_name,
        record.dose_mg,
        crud.list_doses_desc(db, user_id, exclude_id=record.id),
        effective_from=record.effective_from,
        today=today,
    )
    return judge_stage(drug_name, record.dose_mg, **context._asdict())


def upsert(
    db: Session,
    user_id: uuid.UUID,
    payload: MedicationUpsertRequest,
    *,
    today: date | None = None,
) -> UpsertResult:
    """투약 정보 등록·수정 겸용. 커밋까지 한다.

    **행은 용량 변경 1건이다.** 같은 약·같은 용량으로 다시 보내면 행을 만들지 않는다 —
    명세의 `POST /medications` 가 "용량 변경 자동 기록"이라 변경이 없으면 기록할 게 없다.

    네 갈래다:

    1. 기록이 없다              → 첫 행을 연다 (`effective_from` = startedAt, 생략하면 오늘)
    2. 약·용량이 그대로다        → 행을 안 만든다. `startedAt` 만 반영한다
    3. 약이나 용량이 바뀌었다     → 현재 행을 어제로 닫고 새 행을 연다
    4. `startedAt` 이 바뀌었다   → 가장 오래된 행의 날짜를 옮긴다

    4번은 FE 의 회차 스테퍼다. 회차를 올리면 시작일이 과거로 밀리고, 그 값이 그대로
    온다. 회차를 직접 받는 자리가 명세에 없어서 이렇게 들어온다.

    `startedAt` 을 생략하면 4번을 건너뛴다. 이미 기록이 있는데 생략을 오늘로 채우면
    용량만 바꾸는 요청이 시작일을 오늘로 끌어와 회차가 1 로 리셋된다.
    """
    today = today or date.today()
    # `startedAt` 생략은 '오늘'이 아니라 '건드리지 마라'다. 오늘로 치환하면 용량만
    # 고치는 요청이 가장 오래된 행을 오늘로 밀어 전체 회차가 1 로 리셋된다.
    # 기록이 없을 때만 오늘을 첫 행의 시작일로 쓴다.
    started_at = payload.started_at
    if started_at is not None and started_at > today:
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
                # 과거 시작일로 첫 등록하면 그만큼 회차를 이미 채운 것이다.
                same_dose_streak=max(1, count_doses(started_at or today, today=today)),
            ),
            effective_from=started_at or today,
        )
        db.commit()
        # 첫 등록은 PRE_DOSE 에서 넘어온 것이라 단계도 용량도 바뀐 것으로 본다.
        return UpsertResult(record, dose_changed=True, stage_changed=True)  # 첫 등록

    # startedAt 정정 — 가장 오래된 행의 날짜가 곧 전체 시작일이다.
    first = crud.get_first(db, user_id)
    if started_at is not None and first is not None and first.effective_from != started_at:
        # 이미 닫힌 행이면 자기 종료일을 넘어설 수 없다. 여기서 막지 않으면
        # effective_from > effective_to 인 행이 남는다.
        if first.effective_to is not None and started_at > first.effective_to:
            raise StartDateAfterFirstChangeError(started_at, first.effective_to)
        first.effective_from = started_at

    unchanged = current.drug_name == payload.drug_name and current.dose_mg == payload.dose_mg
    if unchanged:
        # 변경이 없으면 이력에 남길 게 없다. 행은 안 만든다.
        # 다만 **단계는 바뀔 수 있다** — 같은 용량으로 회차를 채우면 정착이다.
        # 여기서 재판정하지 않으면 매주 같은 용량을 보내는 사용자가 영원히 TITRATION 이다.
        restaged = restage(db, user_id, current, today=today)
        stage_changed = restaged != current.stage
        current.stage = restaged
        db.commit()
        return UpsertResult(current, dose_changed=False, stage_changed=stage_changed)

    # 변경일은 오늘이다 — 명세 body 에 '언제 바꿨는지' 자리가 없다.
    change_date = max(today, current.effective_from)
    # 용량이 바뀌면 그 날부터 새 구간이라 streak 은 1 부터 다시 센다.
    context = dose_context(
        payload.drug_name,
        payload.dose_mg,
        crud.list_doses_desc(db, user_id),
        effective_from=change_date,
        today=today,
    )
    if change_date > current.effective_from:
        crud.close_current(db, current, effective_to=change_date - timedelta(days=1))
    else:
        # 같은 날 두 번 바꾸면 이력이 두 줄이 될 이유가 없다. 현재 행을 고친다.
        previous_stage = current.stage
        previous_dose_mg = current.dose_mg
        current.drug_name = payload.drug_name
        current.dose_mg = payload.dose_mg
        current.stage = judge_stage(payload.drug_name, payload.dose_mg, **context._asdict())
        db.commit()
        return UpsertResult(
            current,
            dose_changed=True,
            stage_changed=current.stage != previous_stage,
            previous_dose_mg=previous_dose_mg,
        )

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
    return UpsertResult(
        record,
        dose_changed=True,
        stage_changed=record.stage != current.stage,
        previous_dose_mg=current.dose_mg,
    )


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
    next_dose_date, days_until_next_dose = predict_next_dose(started_at, today=today)
    # 단계는 저장값을 읽지 않고 오늘 기준으로 다시 판정한다 — 시간만 지나도 바뀌는데
    # 그 순간에는 쓰기 이벤트가 없다. doseCount·nextDoseDate 와 같은 모델이다.
    return CurrentMedicationResponse(
        stage=restage(db, user_id, current, today=today),
        drug_name=current.drug_name,
        dose_mg=current.dose_mg,
        dose_count=count_doses(started_at, today=today),
        started_at=started_at,
        effective_from=current.effective_from,
        next_dose_date=next_dose_date,
        days_until_next_dose=days_until_next_dose,
    )


def _build_dose_event(result: UpsertResult) -> DoseEvent | None:
    """`UpsertResult` → 명세의 `doseEvent`. 변경이 없으면 None 이다."""
    if not result.dose_changed:
        return None
    record = result.record
    return DoseEvent(
        dose_event_id=record.id,
        dose_mg=record.dose_mg,
        direction=dose_direction(
            record.dose_mg, previous_dose_mg=result.previous_dose_mg
        ),
        effective_from=record.effective_from,
    )


def build_upsert_view(
    db: Session,
    user_id: uuid.UUID,
    result: UpsertResult,
    *,
    today: date | None = None,
) -> MedicationUpsertResponse:
    """`POST /medications` 응답을 조립한다.

    `get_current_view()` 를 쓰지 않는다 — 명세의 POST 응답은 현재 상태에 더해
    `doseChanged` · `doseEvent` · `stageChanged` · `decidedAt` 을 요구하고,
    그 넷은 `UpsertResult` 에만 있다. 반대로 `effectiveFrom` 은 POST 응답에 없다.

    여기서 새로 판정하지 않는다. 단계는 `upsert()` 가 이미 행에 박아 둔 값을 읽기만 한다 —
    두 번 판정하면 같은 요청에 두 답이 나올 수 있다.
    """
    today = today or date.today()
    record = result.record

    started_at = crud.get_dosing_start_date(db, user_id) or record.effective_from
    next_dose_date, days_until_next_dose = predict_next_dose(started_at, today=today)

    return MedicationUpsertResponse(
        medication_id=record.id,
        drug_name=record.drug_name,
        dose_mg=record.dose_mg,
        started_at=started_at,
        dose_count=count_doses(started_at, today=today),
        next_dose_date=next_dose_date,
        days_until_next_dose=days_until_next_dose,
        stage=record.stage,
        stage_reason=STAGE_REASONS[record.stage],
        rule_version=RULE_VERSION,
        dose_changed=result.dose_changed,
        # 변경이 없으면 이벤트도 없다 (명세: "같음 → 기록 없음"). 현재 행을 그대로
        # 실어 보내면 FE 가 "방금 바뀐 것"과 "예전부터 쓰던 것"을 구분하지 못한다.
        dose_event=_build_dose_event(result),
        stage_changed=result.stage_changed,
        decided_at=datetime.now(timezone.utc),
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
