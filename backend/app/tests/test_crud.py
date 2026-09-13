"""crud 모듈 전체를 실제 Postgres 로 훑는다.

문법만 맞고 실제로는 안 도는 쿼리를 잡는 게 목적이다. 도메인 규칙 중
**제약과 맞물린 것**(UNIQUE upsert · 부분 유니크 인덱스 · 변경 이력)에 집중한다.

DB 가 없으면 통째로 건너뛴다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.crud import evaluation as crud_eval
from app.crud import feedback as crud_fb
from app.crud import handoff as crud_handoff
from app.crud import meal as crud_meal
from app.crud import medication as crud_med
from app.crud import user as crud_user
from app.db.session import SessionLocal
from app.models.enums import (
    FeedbackPeriodType,
    GoalStatus,
    HandoffStatus,
    HandoffTriggerType,
    MealItemSource,
    MealType,
    MedicationStage,
    SafetyStatus,
)
from app.models.user import User


def _db_is_up() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_is_up(), reason="Postgres 가 떠 있지 않다.")

NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session
        session.rollback()


@pytest.fixture
def user(db):
    row = crud_user.create(db, nickname="크루드", baseline_meal_kcal=Decimal("650.00"))
    db.flush()
    yield row
    db.rollback()
    with SessionLocal() as cleanup:
        stale = cleanup.get(User, row.id)
        if stale is not None:
            cleanup.delete(stale)
            cleanup.commit()


@pytest.fixture
def meal(db, user):
    snapshot = crud_med.add_snapshot(db, user.id, stage=MedicationStage.MAINTENANCE)
    db.flush()
    row = crud_meal.create(
        db,
        user_id=user.id,
        medication_snapshot_id=snapshot.id,
        meal_type=MealType.LUNCH,
        eaten_at=NOW,
        raw_text="김밥 한 줄",
    )
    db.flush()
    return row


# ─────────────────────────── user ───────────────────────────


def test_get_by_provider(db, user):
    user.auth_provider = "kakao"
    user.provider_user_id = "kakao-123"
    db.flush()

    assert crud_user.get_by_provider(db, "kakao", "kakao-123").id == user.id
    assert crud_user.get_by_provider(db, "kakao", "없음") is None


def test_active_goal_is_one_per_user(db, user):
    """진행 중인 목표가 둘이면 진행률을 무엇 대비로 볼지 모호해진다."""
    crud_user.add_goal(db, user.id, Decimal("62.00"))
    db.flush()

    assert crud_user.get_active_goal(db, user.id) is not None

    crud_user.add_goal(db, user.id, Decimal("60.00"))
    with pytest.raises(Exception):  # 부분 유니크 인덱스 위반
        db.flush()


def test_closed_goal_frees_the_slot(db, user):
    first = crud_user.add_goal(db, user.id, Decimal("62.00"))
    db.flush()
    crud_user.close_goal(db, first, GoalStatus.COMPLETED)
    db.flush()

    crud_user.add_goal(db, user.id, Decimal("60.00"))
    db.flush()  # 이제 통과해야 한다

    assert crud_user.get_active_goal(db, user.id).target_weight_kg == Decimal("60.00")


def test_latest_state_is_by_recorded_at(db, user):
    crud_user.add_state(db, user.id, recorded_at=NOW - dt.timedelta(days=1), weight_kg=Decimal("70"))
    crud_user.add_state(db, user.id, recorded_at=NOW, weight_kg=Decimal("69"))
    db.flush()

    assert crud_user.get_latest_state(db, user.id).weight_kg == Decimal("69.00")
    assert len(crud_user.list_states(db, user.id)) == 2


def test_fcm_token_clears_timestamp(db, user):
    crud_user.set_fcm_token(db, user, "token-1")
    db.flush()
    assert user.fcm_token_updated_at is not None

    crud_user.set_fcm_token(db, user, None)
    db.flush()
    assert user.fcm_token_updated_at is None


# ─────────────────────────── medication ───────────────────────────


def test_only_one_current_record(db, user):
    crud_med.add_record(
        db,
        user.id,
        drug_name="위고비",
        dose_mg=Decimal("0.25"),
        injection_count=1,
        stage=MedicationStage.INITIAL,
        effective_from=TODAY - dt.timedelta(days=30),
    )
    db.flush()

    crud_med.add_record(
        db,
        user.id,
        drug_name="위고비",
        dose_mg=Decimal("0.5"),
        injection_count=2,
        stage=MedicationStage.TITRATION,
        effective_from=TODAY,
    )
    with pytest.raises(Exception):  # effective_to IS NULL 인 행은 하나뿐이다
        db.flush()


def test_snapshot_copies_record(db, user):
    record = crud_med.add_record(
        db,
        user.id,
        drug_name="위고비",
        dose_mg=Decimal("0.5"),
        injection_count=2,
        stage=MedicationStage.TITRATION,
        effective_from=TODAY,
    )
    db.flush()

    snapshot = crud_med.add_snapshot(
        db, user.id, stage=MedicationStage.TITRATION, source_record=record
    )
    db.flush()

    assert snapshot.drug_name == "위고비"
    assert snapshot.dose_mg == Decimal("0.500")


def test_snapshot_without_record_is_allowed(db, user):
    """PRE_DOSE 사용자는 투약 기록이 없다. 그래도 스냅샷은 하나 붙는다."""
    snapshot = crud_med.add_snapshot(db, user.id, stage=MedicationStage.PRE_DOSE)
    db.flush()

    assert snapshot.drug_name is None


# ─────────────────────────── meal ───────────────────────────


def test_recent_meals_are_newest_first(db, user, meal):
    snapshot = crud_med.add_snapshot(db, user.id, stage=MedicationStage.MAINTENANCE)
    db.flush()
    crud_meal.create(
        db,
        user_id=user.id,
        medication_snapshot_id=snapshot.id,
        meal_type=MealType.DINNER,
        eaten_at=NOW + dt.timedelta(hours=6),
        raw_text="저녁",
    )
    db.flush()

    rows = crud_meal.list_recent(db, user.id, limit=5)
    assert [r.meal_type for r in rows] == [MealType.DINNER, MealType.LUNCH]


def test_meal_needs_image_or_text(db, user, meal):
    snapshot = crud_med.add_snapshot(db, user.id, stage=MedicationStage.MAINTENANCE)
    db.flush()
    crud_meal.create(
        db,
        user_id=user.id,
        medication_snapshot_id=snapshot.id,
        meal_type=MealType.SNACK,
        eaten_at=NOW,
    )
    with pytest.raises(Exception):  # CHECK (image_key IS NOT NULL OR raw_text IS NOT NULL)
        db.flush()


def test_confirm_records_correction_only_when_changed(db, meal):
    item = crud_meal.add_item(
        db,
        meal,
        original_food_name="참치김밥",
        estimated_amount_g=Decimal("250.00"),
        confidence=Decimal("0.62"),
    )
    db.flush()

    assert crud_meal.confirm_item(db, item) is None, "확인만 한 것은 수정이 아니다"

    correction = crud_meal.confirm_item(db, item, confirmed_amount_g=Decimal("300.00"))
    db.flush()

    assert correction is not None
    assert correction.original_value["confirmedAmountG"] is None
    assert correction.corrected_value["confirmedAmountG"] == 300.0
    assert len(crud_meal.list_corrections(db, item.id)) == 1


def test_reanalysis_keeps_user_items(db, meal):
    crud_meal.add_item(
        db, meal, original_food_name="AI 가 찾은 것", estimated_amount_g=None, confidence=None
    )
    crud_meal.add_user_item(db, meal, display_name="사용자가 넣은 것")
    db.flush()

    removed = crud_meal.delete_model_items(db, meal)
    db.flush()

    assert removed == 1
    assert [i.source for i in meal.items] == [MealItemSource.USER]


def test_satiety_upsert_does_not_erase_earlier_input(db, meal):
    """식전에 한 번, 식후에 한 번 채워진다. 뒤 입력이 앞 값을 지우면 안 된다."""
    crud_meal.upsert_satiety(db, meal.id, logged_at=NOW, satiety_before=20)
    db.flush()

    crud_meal.upsert_satiety(db, meal.id, logged_at=NOW, satiety_after=68)
    db.flush()

    row = crud_meal.get_satiety(db, meal.id)
    assert (row.satiety_before, row.satiety_after) == (20, 68)


# ─────────────────────────── evaluation ───────────────────────────


def test_qqs_upsert_overwrites(db, meal):
    crud_eval.upsert(
        db,
        meal.id,
        stage_at_evaluation=MedicationStage.MAINTENANCE,
        quantity_score=Decimal("70"),
        quality_score=Decimal("60"),
        satiety_score=Decimal("50"),
    )
    db.flush()

    crud_eval.upsert(
        db,
        meal.id,
        stage_at_evaluation=MedicationStage.MAINTENANCE,
        quantity_score=Decimal("75"),
        quality_score=None,  # 성분이 비면 Quality 를 낼 수 없다
        satiety_score=Decimal("68"),
    )
    db.flush()

    row = crud_eval.get_by_meal(db, meal.id)
    assert row.quantity_score == Decimal("75.00")
    assert row.quality_score is None


def test_qqs_series_is_ordered_by_eaten_at(db, user, meal):
    crud_eval.upsert(
        db,
        meal.id,
        stage_at_evaluation=MedicationStage.MAINTENANCE,
        quantity_score=Decimal("70"),
        quality_score=Decimal("60"),
        satiety_score=Decimal("50"),
    )
    db.flush()

    series = crud_eval.list_series(
        db, user.id, since=NOW - dt.timedelta(days=1), until=NOW + dt.timedelta(days=1)
    )
    assert len(series) == 1
    assert series[0][0] is not None  # eaten_at


# ─────────────────────────── feedback ───────────────────────────


def test_meal_feedback_upsert_and_default_safety(db, user, meal):
    row = crud_fb.upsert_meal_feedback(
        db,
        user_id=user.id,
        meal_id=meal.id,
        body="포만감이 짧게 끝난 식사예요.",
        suggestions=None,
        reasoning=None,
        model_version="stub-short-0",
    )
    db.flush()

    assert row.safety_status is SafetyStatus.REVIEW_REQUIRED, "가드레일 통과 전에는 노출하지 않는다"

    crud_fb.upsert_meal_feedback(
        db,
        user_id=user.id,
        meal_id=meal.id,
        body="다시 쓴 문장",
        suggestions=None,
        reasoning=None,
        model_version="stub-short-0",
        safety_status=SafetyStatus.SAFE,
    )
    db.flush()

    assert crud_fb.get_by_meal(db, meal.id).body == "다시 쓴 문장"


def test_daily_feedback_replaces_sources(db, user, meal):
    mf = crud_fb.upsert_meal_feedback(
        db,
        user_id=user.id,
        meal_id=meal.id,
        body="끼니",
        suggestions=None,
        reasoning=None,
        model_version=None,
    )
    db.flush()

    daily = crud_fb.upsert_daily_feedback(
        db,
        user_id=user.id,
        feedback_date=TODAY,
        summary="하루 요약",
        quantity_score=Decimal("68"),
        quality_score=Decimal("71"),
        satiety_score=Decimal("74"),
        model_version=None,
        sources=[mf],
    )
    db.flush()
    assert len(daily.sources) == 1

    crud_fb.upsert_daily_feedback(
        db,
        user_id=user.id,
        feedback_date=TODAY,
        summary="다시 쓴 요약",
        quantity_score=None,
        quality_score=None,
        satiety_score=None,
        model_version=None,
        sources=[],
    )
    db.flush()

    assert crud_fb.get_daily(db, user.id, TODAY).sources == []


def test_long_term_feedback_upsert(db, user):
    start = TODAY - dt.timedelta(days=6)
    crud_fb.upsert_long_term_feedback(
        db,
        user_id=user.id,
        period_type=FeedbackPeriodType.WEEKLY,
        period_start=start,
        period_end=TODAY,
        trend_summary="오르는 흐름",
        recommendation=None,
        chart_data={"points": []},
        model_version=None,
    )
    db.flush()

    crud_fb.upsert_long_term_feedback(
        db,
        user_id=user.id,
        period_type=FeedbackPeriodType.WEEKLY,
        period_start=start,
        period_end=TODAY,
        trend_summary="다시 쓴 추이",
        recommendation=None,
        chart_data=None,
        model_version=None,
    )
    db.flush()

    row = crud_fb.get_latest_long_term(db, user.id, FeedbackPeriodType.WEEKLY)
    assert row.trend_summary == "다시 쓴 추이"


# ─────────────────────────── handoff ───────────────────────────


def test_handoff_pending_then_reviewed(db, user, meal):
    log = crud_handoff.add(
        db,
        user.id,
        trigger_type=HandoffTriggerType.DOSAGE_QUESTION,
        detected_at=NOW,
        meal_id=meal.id,
        original_input="용량 늘려도 될까요",
    )
    db.flush()

    assert any(row.id == log.id for row in crud_handoff.list_pending(db))

    crud_handoff.review(db, log, status=HandoffStatus.RESOLVED, reviewer_note="상담 안내함")
    db.flush()

    assert all(row.id != log.id for row in crud_handoff.list_pending(db))
    assert len(crud_handoff.list_for_user(db, user.id)) == 1


def test_handoff_survives_meal_deletion(db, user, meal):
    """차단했다는 사실은 감사 대상이라 남아야 한다 — meal_id 만 NULL 이 된다."""
    log = crud_handoff.add(
        db,
        user.id,
        trigger_type=HandoffTriggerType.DISCONTINUATION_QUESTION,
        detected_at=NOW,
        meal_id=meal.id,
    )
    db.flush()

    db.delete(meal)
    db.flush()
    db.expire(log)

    assert crud_handoff.get(db, log.id) is not None
    assert crud_handoff.get(db, log.id).meal_id is None


# ─────────────────────────── food ───────────────────────────


def test_food_get_many_skips_unknown(db):
    from app.crud import food as crud_food

    assert crud_food.get_many(db, [None, "존재하지않는ID"]) == {}
    assert crud_food.get(db, None) is None


def test_food_lookup_hits_seed_data(db):
    """시드가 복원돼 있으면 임의의 한 건을 다시 찾을 수 있어야 한다."""
    from app.crud import food as crud_food
    from app.models.food import FoodRef

    sample = db.query(FoodRef).limit(1).first()
    if sample is None:
        pytest.skip("food_refs 시드가 없다")

    assert crud_food.get(db, sample.id).id == sample.id
    assert sample.id in crud_food.get_many(db, [sample.id, uuid.uuid4().hex])
