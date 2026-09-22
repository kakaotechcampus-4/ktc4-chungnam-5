"""meals 테이블 접근. 여기 말고는 아무도 Meal 을 직접 쿼리하지 않는다."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Row, and_, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.models.enums import MealItemSource, MealStatus
from app.models.evaluation import QQSEvaluation
from app.models.meal import Meal, MealItem
from app.models.medication import MedicationSnapshot

_KST = "Asia/Seoul"


def _kst_day(column):
    """eaten_at(UTC 로 저장됨)을 KST 기준 '그 날' 로 자른다.

    UTC 기준으로 자르면 밤 11시(KST)에 먹은 야식이 다음날로 잘못 집계된다.
    """
    return func.date_trunc("day", func.timezone(_KST, column))


def list_meals(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int,
    before_eaten_at: datetime | None = None,
    before_id: uuid.UUID | None = None,
) -> list[Row]:
    """user_id 의 식사를 eaten_at 최신순으로 최대 limit 개 조회한다.

    각 행은 (Meal, stage, quantity_score, quality_score, satiety_score) 튜플이다.
    (before_eaten_at, before_id) 보다 "이전" 항목만 대상으로 한다 — 커서 페이지네이션.
    """
    stmt = (
        select(
            Meal,
            MedicationSnapshot.stage,
            QQSEvaluation.quantity_score,
            QQSEvaluation.quality_score,
            QQSEvaluation.satiety_score,
        )
        .join(MedicationSnapshot, Meal.medication_snapshot_id == MedicationSnapshot.id)
        .outerjoin(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(Meal.user_id == user_id, Meal.deleted_at.is_(None))
    )

    if before_eaten_at is not None:
        stmt = stmt.where(
            or_(
                Meal.eaten_at < before_eaten_at,
                and_(Meal.eaten_at == before_eaten_at, Meal.id < before_id),
            )
        )

    stmt = stmt.order_by(Meal.eaten_at.desc(), Meal.id.desc()).limit(limit)

    return list(db.execute(stmt).all())


def get_display_names(db: Session, meal_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """meal_id 별로 meal_items.display_name 을 쉼표로 이어붙인 값을 돌려준다."""
    if not meal_ids:
        return {}

    stmt = (
        select(MealItem.meal_id, MealItem.display_name)
        .where(MealItem.meal_id.in_(meal_ids))
        .order_by(MealItem.meal_id, MealItem.id)
    )

    names_by_meal: dict[uuid.UUID, list[str]] = {}
    for meal_id, display_name in db.execute(stmt).all():
        names_by_meal.setdefault(meal_id, []).append(display_name)

    return {meal_id: ", ".join(names) for meal_id, names in names_by_meal.items()}


def get_owned_meal(db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID) -> Meal | None:
    """user_id 소유의 살아있는 meal_id 를 가져온다.

    없거나, 남의 것이거나, 이미 삭제됐으면 None — 셋을 구분하지 않는다.
    호출부가 전부 404 로 뭉개야 "남의 mealId 는 존재한다" 는 정보가 새지 않는다.

    **이 WHERE 가 소유권 누출을 막는 유일한 지점이다.** 같은 조건을 다른 곳에
    복사하지 말고 이 함수를 부를 것 — 복사본은 조용히 드리프트한다.

    이름이 "owned" 인 것에 주의: 존재·소유·미삭제만 본다. 지금 고칠 수 있는
    상태인지(`status`)는 보지 않는다 — 그건 `services/meal.py` 의 판단이다.
    """
    stmt = select(Meal).where(
        Meal.id == meal_id,
        Meal.user_id == user_id,
        Meal.deleted_at.is_(None),
    )
    return db.execute(stmt).scalar_one_or_none()


def soft_delete_meal(db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID) -> Meal | None:
    """user_id 소유의 meal_id 를 soft delete 한다.

    없거나, 남의 것이거나, 이미 삭제됐으면 None (셋 다 "지울 수 있는 게 없다"로 동일 취급).
    """
    meal = get_owned_meal(db, user_id=user_id, meal_id=meal_id)
    if meal is None:
        return None

    meal.deleted_at = datetime.now(UTC)
    return meal


def get_calendar_days(
    db: Session,
    *,
    user_id: uuid.UUID,
    month_start: datetime,
    month_end: datetime,
) -> list[Row]:
    """KST 기준 날짜별 (day, count, meal_types) 를 조회한다."""
    day = _kst_day(Meal.eaten_at)
    stmt = (
        select(
            day.label("day"),
            func.count().label("count"),
            func.array_agg(distinct(Meal.meal_type)).label("meal_types"),
        )
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= month_start,
            Meal.eaten_at < month_end,
        )
        .group_by(day)
        .order_by(day)
    )
    return list(db.execute(stmt).all())


def get_calendar_day_stages(
    db: Session,
    *,
    user_id: uuid.UUID,
    month_start: datetime,
    month_end: datetime,
) -> list[Row]:
    """날짜별 대표 stage — 그날 가장 마지막(eaten_at 최신)에 먹은 식사 기준."""
    day = _kst_day(Meal.eaten_at)
    stmt = (
        select(day.label("day"), MedicationSnapshot.stage)
        .join(MedicationSnapshot, Meal.medication_snapshot_id == MedicationSnapshot.id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= month_start,
            Meal.eaten_at < month_end,
        )
        .distinct(day)
        .order_by(day, Meal.eaten_at.desc())
    )
    return list(db.execute(stmt).all())


def get_calendar_summary(
    db: Session,
    *,
    user_id: uuid.UUID,
    month_start: datetime,
    month_end: datetime,
) -> Row:
    """그 달 전체 요약 — 총 식사 수, 평균 Q/Q/S (미평가 식사는 평균에서 자동 제외)."""
    stmt = (
        select(
            func.count(Meal.id).label("total_meals"),
            func.avg(QQSEvaluation.quantity_score).label("avg_quantity"),
            func.avg(QQSEvaluation.quality_score).label("avg_quality"),
            func.avg(QQSEvaluation.satiety_score).label("avg_satiety"),
        )
        .select_from(Meal)
        .outerjoin(QQSEvaluation, QQSEvaluation.meal_id == Meal.id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.eaten_at >= month_start,
            Meal.eaten_at < month_end,
        )
    )
    return db.execute(stmt).one()


def add_item(
    db: Session,
    *,
    meal_id: uuid.UUID,
    display_name: str,
    amount_g: Decimal | None,
    food_ref_id: str | None,
    raw_input: dict,
) -> MealItem:
    """사용자가 직접 추가한 음식 1건을 넣는다. add + flush 까지만 하고 커밋하지 않는다.

    `original_food_name` 에도 같은 이름이 들어간다 — AI 추정값이 없으니 사용자가 쓴
    이름이 곧 원본이다. `confidence` 는 NULL 이다: AI 가 인식한 게 아니라 신뢰도라는
    개념 자체가 없다(0 이나 1 을 넣으면 인식 성능 통계가 오염된다).

    양은 `confirmed_amount_g` 로 들어간다. 사용자가 직접 말한 값이라 확인이 끝난
    것으로 본다 — `estimated_amount_g` 는 AI 추정값 자리다.
    """
    item = MealItem(
        meal_id=meal_id,
        food_ref_id=food_ref_id,
        original_food_name=display_name,
        display_name=display_name,
        confirmed_amount_g=amount_g,
        source=MealItemSource.USER,
        raw_ai_result=raw_input,
    )
    db.add(item)
    db.flush()
    return item


def mark_recalculating(db: Session, meal: Meal) -> None:
    """식사를 재분석 대기 상태로 되돌린다.

    `db` 를 받지만 쓰지 않는다 — 이미 세션에 붙어 있는 객체라 대입만으로 UPDATE 가
    나간다. 시그니처를 맞춰 두는 건 "상태를 바꾸는 일은 crud 를 거친다" 는 규칙을
    호출부에서 눈에 보이게 하려는 것이다.
    """
    meal.status = MealStatus.ANALYZING
    meal.is_recalculation = True
