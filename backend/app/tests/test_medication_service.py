"""투약 등록 유스케이스 통합 테스트 — 실제 Postgres 로 돌린다.

여기서 검증하는 건 **기간 전이**다. `medication_records` 에는
`UNIQUE (user_id) WHERE effective_to IS NULL` 부분 인덱스가 걸려 있어서
"이전 행 닫기 → flush → 새 행 열기" 순서가 틀리면 그 자리에서 죽는다.
순수 함수 테스트로는 절대 잡히지 않는 부분이라 컨테이너를 띄운다.

**행 하나 = 용량 변경 1건**이다 (명세: "용량 변경 자동 기록" / "용량 변경 이력").
같은 용량으로 다시 보내도 행이 늘지 않는다는 게 이 파일의 중심 주장이다.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.errors import ApiError, ErrorCode
from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.models.enums import DrugName, MedicationStage
from app.schemas.medication import DoseDirection, MedicationRegisterRequest
from app.services import medication as service

TODAY = date(2026, 9, 20)


@pytest.fixture
def user_id(db: Session) -> uuid.UUID:
    user = user_crud.create(
        db,
        nickname="투약테스트",
        height_cm=Decimal("170"),
        baseline_meal_kcal=Decimal("700"),
    )
    db.flush()
    return user.id


def _req(
    dose: str, started_at: date | None = None, drug: DrugName = DrugName.WEGOVY
) -> MedicationRegisterRequest:
    return MedicationRegisterRequest(
        drug_name=drug, dose_mg=Decimal(dose), started_at=started_at
    )


def _rows(db: Session, user_id: uuid.UUID) -> int:
    return len(medication_crud.list_history(db, user_id))


# ── 행의 의미: 용량 변경 1건 ────────────────────────────────────


def test_first_registration_opens_a_record(db: Session, user_id: uuid.UUID) -> None:
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)

    current = medication_crud.get_current(db, user_id)
    assert current is not None
    assert current.dose_mg == Decimal("0.250")
    assert current.effective_from == date(2026, 9, 1)
    assert current.effective_to is None


def test_same_dose_does_not_create_a_row(db: Session, user_id: uuid.UUID) -> None:
    """같은 약·같은 용량이면 이력에 남길 변경이 없다.

    옛 모델(1회 = 1행)과 갈리는 지점이다. 명세의 `POST /medications` 는
    "용량 변경 자동 기록"이라 변경이 없으면 기록하지 않는다.
    """
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)

    assert _rows(db, user_id) == 1


def test_dose_change_closes_previous_row_the_day_before(
    db: Session, user_id: uuid.UUID
) -> None:
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)
    service.register(db, user_id, _req("0.5", date(2026, 9, 1)), today=TODAY)

    history = medication_crud.list_history(db, user_id)
    assert len(history) == 2
    assert history[0].effective_to == TODAY - timedelta(days=1)
    assert history[1].effective_from == TODAY
    assert history[1].effective_to is None


def test_only_one_open_record_survives(db: Session, user_id: uuid.UUID) -> None:
    """부분 UNIQUE 인덱스 위반이면 여기서 죽는다.

    날짜를 주 단위로 벌리는 건 **등록이 하루에 한 번뿐**이기 때문이다. 같은 날 다시
    보내면 정정이라 409 다 (`PATCH` 의 몫).
    """
    for week, dose in enumerate(("0.25", "0.5", "1.0", "1.7")):
        service.register(
            db, user_id, _req(dose, date(2026, 9, 1)), today=TODAY + timedelta(weeks=week)
        )

    open_rows = [r for r in medication_crud.list_history(db, user_id) if r.effective_to is None]
    assert len(open_rows) == 1


def test_drug_change_also_creates_a_row(db: Session, user_id: uuid.UUID) -> None:
    service.register(db, user_id, _req("1.0", date(2026, 9, 1)), today=TODAY)
    service.register(
        db, user_id, _req("5.0", date(2026, 9, 1), DrugName.MOUNJARO), today=TODAY
    )

    assert _rows(db, user_id) == 2
    assert medication_crud.get_current(db, user_id).drug_name == DrugName.MOUNJARO


# ── startedAt: 전체 투약 시작일 ────────────────────────────────


def test_omitted_started_at_keeps_the_existing_start_date(
    db: Session, user_id: uuid.UUID
) -> None:
    """`startedAt` 생략은 '오늘'이 아니라 '건드리지 마라'다.

    오늘로 채우면 용량만 바꾸는 요청이 가장 오래된 행을 오늘로 밀어 회차가 1 로
    리셋된다 — 클라이언트는 200 을 받고 이력이 멀쩡하다고 믿는다.
    """
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)

    service.register(db, user_id, _req("1.0"), today=TODAY)

    assert medication_crud.get_dosing_start_date(db, user_id) == date(2026, 9, 1)
    assert service.get_current_view(db, user_id, today=TODAY).dose_count == 3


def test_omitted_started_at_on_first_registration_uses_today(
    db: Session, user_id: uuid.UUID
) -> None:
    """기록이 없으면 옮길 시작일도 없다 — 오늘로 연다."""
    service.register(db, user_id, _req("0.25"), today=TODAY)

    assert medication_crud.get_dosing_start_date(db, user_id) == TODAY


def test_omitted_started_at_never_trips_the_boundary_check(
    db: Session, user_id: uuid.UUID
) -> None:
    """첫 변경 이후 용량만 다시 바꿔도 거부되면 안 된다."""
    _open_two_records(db, user_id)

    service.register(db, user_id, _req("1.0"), today=TODAY)

    assert medication_crud.get_dosing_start_date(db, user_id) == date(2026, 9, 1)


def test_dose_count_is_computed_from_started_at(db: Session, user_id: uuid.UUID) -> None:
    """명세 「서버 계산 항목」 — doseCount = floor((today - startedAt) / 7) + 1.

    행을 세지 않는다. 등록을 한 번만 해도 시간이 지나면 회차가 오른다.
    """
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)

    view = service.get_current_view(db, user_id, today=TODAY)
    assert view.started_at == date(2026, 9, 1)
    assert view.dose_count == 3  # 9/1 부터 19일 = 2주 + 5일 → 3회차
    assert _rows(db, user_id) == 1


def test_future_start_date_is_rejected(db: Session, user_id: uuid.UUID) -> None:
    """미래 시작일이면 회차 공식이 0 이나 음수를 낸다."""
    with pytest.raises(service.FutureStartDateError):
        service.register(db, user_id, _req("0.25", date(2026, 12, 25)), today=TODAY)

    assert _rows(db, user_id) == 0


# ── 단계 판정 연동 ─────────────────────────────────────────────


def test_stage_moves_with_dose(db: Session, user_id: uuid.UUID) -> None:
    """사다리를 오르면 단계가 따라 바뀐다 (위고비 0.25 → 1.0 → 2.4)."""
    steps = [
        ("0.25", MedicationStage.INITIAL),
        ("1.0", MedicationStage.TITRATION),
        ("2.4", MedicationStage.MAINTENANCE),
    ]
    for week, (dose, expected) in enumerate(steps):
        service.register(
            db, user_id, _req(dose, date(2026, 9, 1)), today=TODAY + timedelta(weeks=week)
        )
        assert medication_crud.get_current(db, user_id).stage is expected


def test_dose_reduction_is_marked_reduced(db: Session, user_id: uuid.UUID) -> None:
    """2.4 → 1.7 로 내리면 감량기다. 사다리 위치만 보면 TITRATION 으로 잡혔을 자리다."""
    service.register(db, user_id, _req("2.4", date(2026, 9, 1)), today=TODAY)
    service.register(db, user_id, _req("1.7", date(2026, 9, 1)), today=TODAY)

    assert medication_crud.get_current(db, user_id).stage is MedicationStage.REDUCED


def test_no_record_is_pre_dose(db: Session, user_id: uuid.UUID) -> None:
    """투약 기록이 없으면 PRE_DOSE. 에러가 아니다 — 투약 전 식사 평가가 제품 기능이다 (D8)."""
    view = service.get_current_view(db, user_id, today=TODAY)
    assert view.stage is MedicationStage.PRE_DOSE
    assert view.drug_name is None
    assert view.dose_count is None


# ── 스냅샷 ─────────────────────────────────────────────────────


def test_snapshot_freezes_current_medication(db: Session, user_id: uuid.UUID) -> None:
    """스냅샷은 만든 시점의 값을 얼린다. 원본을 고쳐도 따라 변하지 않는다."""
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=TODAY)
    snapshot = service.create_snapshot_for_meal(db, user_id)

    assert snapshot.stage is MedicationStage.INITIAL
    assert snapshot.dose_mg == Decimal("0.250")

    service.register(db, user_id, _req("2.4", date(2026, 9, 1)), today=TODAY)
    db.refresh(snapshot)

    assert medication_crud.get_current(db, user_id).stage is MedicationStage.MAINTENANCE
    assert snapshot.stage is MedicationStage.INITIAL  # ← 얼어 있다
    assert snapshot.dose_mg == Decimal("0.250")


def test_snapshot_for_pre_dose_user_is_empty(db: Session, user_id: uuid.UUID) -> None:
    """투약 전 사용자도 빈 스냅샷이 붙는다 — meals.medication_snapshot_id 가 NOT NULL."""
    snapshot = service.create_snapshot_for_meal(db, user_id)

    assert snapshot.stage is MedicationStage.PRE_DOSE
    assert snapshot.drug_name is None
    assert snapshot.dose_mg is None
    assert snapshot.source_record_id is None


# ── 시작일 정정의 상한 ─────────────────────────────────────────


def _open_two_records(db: Session, user_id: uuid.UUID) -> None:
    """[(09-01, 09-14, 0.25), (09-15, None, 0.5)] 을 만든다."""
    service.register(db, user_id, _req("0.25", date(2026, 9, 1)), today=date(2026, 9, 10))
    service.register(db, user_id, _req("0.5", date(2026, 9, 1)), today=date(2026, 9, 15))


def test_started_at_cannot_be_moved_after_registration(
    db: Session, user_id: uuid.UUID
) -> None:
    """전체 시작일을 옮기는 건 정정이다 — 등록이 아니라 409 다.

    이미 지나간 날을 다시 쓰는 일이라 INSERT 로 표현되지 않는다. 받아 주면 용량만
    바꾸는 요청이 회차를 통째로 흔든다. `PATCH /medications/{id}` 의 몫이다.
    """
    _open_two_records(db, user_id)

    with pytest.raises(ApiError) as exc:
        service.register(db, user_id, _req("0.5", date(2026, 9, 15)), today=TODAY)

    assert exc.value.code is ErrorCode.CONFLICT
    assert exc.value.http_status == 409


def test_rejected_start_date_leaves_history_intact(db: Session, user_id: uuid.UUID) -> None:
    """거부된 요청이 이력을 건드리고 가면 안 된다."""
    _open_two_records(db, user_id)
    before = [(r.effective_from, r.effective_to, r.dose_mg) for r in medication_crud.list_history(db, user_id)]

    with pytest.raises(ApiError):
        service.register(db, user_id, _req("0.5", date(2026, 9, 15)), today=TODAY)
    db.rollback()

    after = [(r.effective_from, r.effective_to, r.dose_mg) for r in medication_crud.list_history(db, user_id)]
    assert after == before


def test_every_period_stays_ordered(db: Session, user_id: uuid.UUID) -> None:
    """모든 행이 effective_from <= effective_to 를 지킨다.

    등록만 하는 한 이 불변식은 구조적으로 지켜진다 — 새 행은 항상 오늘 열리고,
    직전 행은 어제로 닫힌다. 깨질 수 있었던 건 **같은 날 두 번째 등록**뿐이고
    (오늘 연 행을 어제로 닫는다) 그건 이제 409 다.
    """
    _open_two_records(db, user_id)
    service.register(db, user_id, _req("1.0", date(2026, 9, 1)), today=TODAY)

    rows = medication_crud.list_history(db, user_id)
    assert [(r.effective_from, r.effective_to) for r in rows] == [
        (date(2026, 9, 1), date(2026, 9, 14)),
        (date(2026, 9, 15), TODAY - timedelta(days=1)),
        (TODAY, None),
    ]
    for row in rows:
        assert row.effective_to is None or row.effective_from <= row.effective_to


# ── 단계 전이: 시간이 지나야 일어난다 ──────────────────────────


def test_middle_dose_settles_into_maintenance_over_time(
    db: Session, user_id: uuid.UUID
) -> None:
    """중간 칸 용량도 회차를 채우면 유지기가 된다.

    streak 을 행으로 세던 때는 이 전이가 구조적으로 불가능했다 — 인접한 두 행은
    (약물, 용량) 이 같을 수 없어 streak 이 항상 1 이었다 (코드 리뷰 지적).
    """
    start = date(2026, 3, 2)
    raised = start + timedelta(weeks=4)
    service.register(db, user_id, _req("0.5", start), today=start)
    service.register(db, user_id, _req("1.0"), today=raised)  # 1.0 으로 올린 날

    # 올린 당일은 1회차 — 아직 정착 전이다
    assert service.get_current_view(db, user_id, today=raised).stage is MedicationStage.TITRATION
    # 4주 뒤 5회차 — 예정일에 안 올렸으니 정착
    settled = raised + timedelta(weeks=4)
    assert service.get_current_view(db, user_id, today=settled).stage is MedicationStage.MAINTENANCE


def test_stage_advances_without_any_write(db: Session, user_id: uuid.UUID) -> None:
    """앱을 안 켜도 단계가 흐른다.

    단계는 시간만 지나도 바뀌는데 그 순간에는 쓰기 이벤트가 없다. 저장값을 그대로
    읽으면 사용자가 요청을 보낼 때까지 단계가 멈춘다 — 그래서 조회 시 재판정한다.
    """
    start = date(2026, 3, 2)
    raised = start + timedelta(weeks=4)
    service.register(db, user_id, _req("0.5", start), today=start)
    service.register(db, user_id, _req("1.0"), today=raised)
    stored = medication_crud.get_current(db, user_id).stage

    view = service.get_current_view(db, user_id, today=raised + timedelta(weeks=4))

    assert stored is MedicationStage.TITRATION  # 저장값은 쓰기 시점 그대로
    assert view.stage is MedicationStage.MAINTENANCE  # 응답은 오늘 기준
    # 조회가 쓰기를 하지 않는다
    assert medication_crud.get_current(db, user_id).stage is stored


def test_resending_same_dose_updates_the_stored_stage(
    db: Session, user_id: uuid.UUID
) -> None:
    """같은 용량 재전송은 행을 안 만들지만 단계는 갱신한다."""
    start = date(2026, 3, 2)
    raised = start + timedelta(weeks=4)
    service.register(db, user_id, _req("0.5", start), today=start)
    service.register(db, user_id, _req("1.0"), today=raised)

    result = service.register(db, user_id, _req("1.0"), today=raised + timedelta(weeks=4))

    assert result.dose_changed is False
    assert result.stage_changed is True
    assert _rows(db, user_id) == 2  # 행은 안 늘었다
    assert medication_crud.get_current(db, user_id).stage is MedicationStage.MAINTENANCE


def test_reduced_releases_once_the_lower_dose_settles(
    db: Session, user_id: uuid.UUID
) -> None:
    """감량 후 회차를 채우면 REDUCED 에서 풀린다.

    MAINTENANCE_STREAK docstring 이 약속한 동작인데, streak 이 1 에 갇혀 있던 동안은
    감량하면 영원히 REDUCED 였다.
    """
    start = date(2026, 3, 2)
    lowered = start + timedelta(weeks=4)
    service.register(db, user_id, _req("1.7", start), today=start)
    service.register(db, user_id, _req("1.0"), today=lowered)  # 감량

    assert service.get_current_view(db, user_id, today=lowered).stage is MedicationStage.REDUCED
    settled = lowered + timedelta(weeks=4)
    assert service.get_current_view(db, user_id, today=settled).stage is MedicationStage.MAINTENANCE


# ── 같은 날 재등록은 '정정'이다 ─────────────────────────────────
#
# 같은 날 다시 보내면 새 행을 만들지 않고 현재 행을 고친다. 그때 **덮어쓰는 값은
# 한 번도 맞은 적이 없다** — 몇 초 존재했다 지워지는 오타다. 그 값을 '직전 용량'으로
# 잡으면 오타를 고친 것이 감량으로 기록된다 (코드 리뷰 지적).


def _register_view(
    db: Session, user_id: uuid.UUID, req: MedicationRegisterRequest, *, today: date
):
    """POST 응답까지 만들어서 돌려준다 — stage 와 direction 을 함께 봐야 한다."""
    result = service.register(db, user_id, req, today=today)
    return service.build_register_view(db, user_id, result, today=today)


def test_reduction_on_a_later_day_is_still_a_reduction(
    db: Session, user_id: uuid.UUID
) -> None:
    """다른 날 내린 것은 진짜 감량이다 — 정정 처리가 여기까지 번지면 안 된다.

    새 행을 여는 분기에서는 직전 행이 실제로 맞은 용량이므로 이력에서 빼면 안 된다.
    """
    lowered = TODAY + timedelta(weeks=4)
    service.register(db, user_id, _req("1.7", TODAY), today=TODAY)

    view = _register_view(db, user_id, _req("1.0"), today=lowered)

    assert _rows(db, user_id) == 2
    assert view.stage is MedicationStage.REDUCED
    assert view.dose_event.direction is DoseDirection.DECREASE


def test_increase_on_a_later_day_is_unaffected(db: Session, user_id: uuid.UUID) -> None:
    """증량도 그대로여야 한다."""
    raised = TODAY + timedelta(weeks=4)
    service.register(db, user_id, _req("0.5", TODAY), today=TODAY)

    view = _register_view(db, user_id, _req("1.0"), today=raised)

    assert view.dose_event.direction is DoseDirection.INCREASE
    assert view.stage is MedicationStage.TITRATION
