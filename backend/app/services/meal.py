"""식사(meal) 도메인 로직.

DB 세션은 직접 다루지 않고 `crud/` 를 통해서만 접근한다. 상태는 `crud/` 가 들고,
조합은 `worker/` · `api/` 가 한다.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.models.enums import MealStatus
from app.schemas.meal import (
    CalendarDay,
    CalendarSummary,
    MealCalendarResponse,
    MealDeleteResponse,
    MealItemCreateRequest,
    MealItemCreateResponse,
    MealListItem,
    MealListResponse,
    MealScores,
)
from app.schemas.nutrition import NutritionInfo

_KST_ZONE = ZoneInfo("Asia/Seoul")


class MealNotFoundError(Exception):
    """존재하지 않거나, 남의 것이거나, 이미 삭제된 식사를 가리킬 때."""


class MealNotEditableError(Exception):
    """지금 상태로는 음식을 고칠 수 없는 식사를 가리킬 때."""


class AddItemOutcome(NamedTuple):
    """항목 추가의 결과. 응답과 "커밋 뒤에 보내야 할 작업" 을 함께 돌려준다.

    큐 적재를 서비스 안에서 하지 않는 건, 커밋 실패 시 Worker 가 DB 에 없는 식사를
    처리하게 되기 때문이다. 순서를 지킬 책임은 호출부(api 레이어)에 있고, 그러려면
    무엇을 보낼지를 여기서 넘겨줘야 한다.
    """

    response: MealItemCreateResponse
    analyze_task: dict[str, Any]

# 그대로 g 으로 볼 수 있는 단위.
# ml 은 물 기준 1ml ≈ 1g 로 근사한다. 국·음료가 대부분이라 오차를 감수할 만하다.
_GRAM_EQUIVALENT_UNITS = {"g", "G", "그램", "ml", "mL", "ML", "밀리리터"}

_CURSOR_SEPARATOR = "|"

# 사용자가 음식을 고칠 수 있는 상태.
# ANALYZING 은 Worker 가 `source=MODEL` 항목을 지우고 다시 넣는 중이라 제외한다
# (`jobs/analyze_meal.py` 6단계) — 그 와중에 끼어들면 무엇이 남을지 알 수 없다.
# FAILED 는 인식된 음식이 하나도 없는 상태라 "고친다" 는 말이 성립하지 않는다.
_EDITABLE_STATUSES = frozenset({MealStatus.REVIEW_REQUIRED, MealStatus.EVALUATED})


def to_grams(amount: float | Decimal | None, unit: str | None) -> Decimal | None:
    """AI 가 준 `amount` + `unit` 을 g 으로 옮긴다. 옮길 수 없으면 None.

    **환산표가 없다.** `food_refs.serving_size` 는 "영양성분함량기준량"(성분값이
    어느 양 기준인지, 보통 100g)이지 "1개 = 50g" 이 아니다. 즉 "계란 2개" 를 g 으로
    바꿀 근거가 DB 에 없다.

    없는 근거를 지어내지 않는다. 지어낸 g 으로 Q/Q/S 를 채점하면 점수가 조용히
    틀리고, 사용자는 어디서 틀렸는지 알 수 없다. 대신 None 을 돌려주고
    원본 단위는 `raw_ai_result` 에 남긴다 — 사용자가 `REVIEW_REQUIRED` 단계에서
    실제 양을 확인해 `confirmed_amount_g` 를 채우는 것이 이 상태의 존재 이유다.
    """
    if amount is None or unit is None:
        return None
    if unit.strip() not in _GRAM_EQUIVALENT_UNITS:
        return None

    value = Decimal(str(amount))
    if value < 0:
        return None
    # 컬럼이 Numeric(8, 2) 다
    return value.quantize(Decimal("0.01"))


def _build_scores(
    quantity: Decimal | None,
    quality: Decimal | None,
    satiety: Decimal | None,
) -> MealScores | None:
    """세 점수가 전부 없으면(아직 미평가) None, 하나라도 있으면 객체로 감싼다."""
    if quantity is None and quality is None and satiety is None:
        return None
    return MealScores(
        quantity=round(quantity) if quantity is not None else None,
        quality=round(quality) if quality is not None else None,
        satiety=round(satiety) if satiety is not None else None,
    )


def _build_thumbnail_url(image_key: str | None) -> str | None:
    """image_key 를 실제 접근 가능한 URL 로 바꾼다.

    TODO: 6번(POST /meals)에서 infra/ FileStorage 붙이면 presigned URL 로 교체.
    """
    if image_key is None:
        return None
    return f"/media/{image_key}"


def encode_cursor(eaten_at: datetime, meal_id: uuid.UUID) -> str:
    """(eaten_at, id) 를 클라이언트가 그대로 들고 있다가 돌려줄 불투명한 문자열로 감싼다."""
    raw = f"{eaten_at.isoformat()}{_CURSOR_SEPARATOR}{meal_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """encode_cursor 로 만든 문자열을 (eaten_at, id) 로 되돌린다.

    클라이언트가 조작했거나 잘못된 cursor 를 보내면 ValueError.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        eaten_at_str, meal_id_str = raw.split(_CURSOR_SEPARATOR)
        return datetime.fromisoformat(eaten_at_str), uuid.UUID(meal_id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("잘못된 cursor 입니다.") from exc


def list_meals(
    db: Session,
    *,
    user_id: uuid.UUID,
    cursor: str | None,
    limit: int,
) -> MealListResponse:
    """cursor 이후의 식사를 최신순으로 limit 개 조회해 응답 스키마로 조립한다."""
    before_eaten_at, before_id = (None, None)
    if cursor is not None:
        before_eaten_at, before_id = decode_cursor(cursor)

    rows = meal_crud.list_meals(
        db,
        user_id=user_id,
        limit=limit + 1,  # 1개 더 가져와서 "다음 페이지 있음"을 판단한다
        before_eaten_at=before_eaten_at,
        before_id=before_id,
    )

    has_more = len(rows) > limit
    rows = rows[:limit]  # 판단용으로 더 가져온 1개는 응답에서 잘라낸다

    meal_ids = [meal.id for meal, *_ in rows]
    display_names = meal_crud.get_display_names(db, meal_ids)

    items = [
        MealListItem(
            meal_id=meal.id,
            meal_type=meal.meal_type,
            eaten_at=meal.eaten_at,
            stage=stage,
            display_name=display_names.get(meal.id, ""),
            thumbnail_url=_build_thumbnail_url(meal.image_key),
            scores=_build_scores(quantity, quality, satiety),
        )
        for meal, stage, quantity, quality, satiety in rows
    ]

    next_cursor = None
    if has_more and rows:
        last_meal = rows[-1][0]
        next_cursor = encode_cursor(last_meal.eaten_at, last_meal.id)

    return MealListResponse(items=items, next_cursor=next_cursor, has_more=has_more)


def delete_meal(db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID) -> MealDeleteResponse:
    """식사를 soft delete 하고 응답을 조립한다."""
    meal = meal_crud.soft_delete_meal(db, user_id=user_id, meal_id=meal_id)
    if meal is None:
        raise MealNotFoundError(f"meal {meal_id} 를 찾을 수 없습니다.")

    # 응답을 만들어 돌려주기 전에 커밋 — 클라이언트가 200을 받는 시점엔
    # 이미 DB에 반영된 상태여야 한다 (get_db 는 더 이상 commit 하지 않는다).
    db.commit()

    return MealDeleteResponse(
        meal_id=meal.id,
        deleted_at=meal.deleted_at,
        # TODO: 7·8번(insights/long-term) 구현 후 실제 stale 판정 로직으로 교체.
        affected_insights=[],
    )


def _parse_month_range(month: str) -> tuple[datetime, datetime]:
    """"YYYY-MM" 을 KST 기준 그 달의 [시작, 다음 달 시작) 구간으로 바꾼다."""
    try:
        year_str, month_str = month.split("-")
        year, mon = int(year_str), int(month_str)
        if not (1 <= mon <= 12):
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"잘못된 month 형식입니다: {month!r} (YYYY-MM 이어야 함)") from exc

    start = datetime(year, mon, 1, tzinfo=_KST_ZONE)
    end = datetime(year + 1, 1, 1, tzinfo=_KST_ZONE) if mon == 12 else datetime(year, mon + 1, 1, tzinfo=_KST_ZONE)
    return start, end


def get_calendar(db: Session, *, user_id: uuid.UUID, month: str) -> MealCalendarResponse:
    """월별 날짜별 집계 + 그 달 전체 요약을 조립한다."""
    month_start, month_end = _parse_month_range(month)

    day_rows = meal_crud.get_calendar_days(
        db, user_id=user_id, month_start=month_start, month_end=month_end
    )
    stage_by_day = {
        row.day.date(): row.stage
        for row in meal_crud.get_calendar_day_stages(
            db, user_id=user_id, month_start=month_start, month_end=month_end
        )
    }

    days = [
        CalendarDay(
            date=row.day.date(),
            count=row.count,
            recorded_meal_types=row.meal_types,
            stage=stage_by_day[row.day.date()],
        )
        for row in day_rows
    ]

    summary_row = meal_crud.get_calendar_summary(
        db, user_id=user_id, month_start=month_start, month_end=month_end
    )
    summary = CalendarSummary(
        total_meals=summary_row.total_meals,
        avg_scores=MealScores(
            quantity=round(summary_row.avg_quantity) if summary_row.avg_quantity is not None else None,
            quality=round(summary_row.avg_quality) if summary_row.avg_quality is not None else None,
            satiety=round(summary_row.avg_satiety) if summary_row.avg_satiety is not None else None,
        ),
    )

    return MealCalendarResponse(month=month, days=days, summary=summary)


def add_item(
    db: Session,
    *,
    user_id: uuid.UUID,
    meal_id: uuid.UUID,
    request: MealItemCreateRequest,
    amount_g: Decimal | None,
    food_ref_id: str | None,
    nutrition: NutritionInfo | None,
) -> AddItemOutcome:
    """사용자가 직접 입력한 음식을 식사에 더하고 재분석 대기로 되돌린다.

    `amount_g` · `food_ref_id` · `nutrition` 은 이미 결정된 값으로 들어온다.
    공공 DB 매칭은 `services/nutrition.py` 의 일이고 그 결과를 여기로 옮기는 건
    호출부(api 레이어)다 — services 끼리는 서로 참조하지 않는다(README 절대 규칙 5).
    """
    meal = meal_crud.get_owned_meal(db, user_id=user_id, meal_id=meal_id)
    if meal is None:
        raise MealNotFoundError(f"meal {meal_id} 를 찾을 수 없습니다.")

    if meal.status not in _EDITABLE_STATUSES:
        raise MealNotEditableError(
            f"{meal.status.value} 상태의 식사는 음식을 고칠 수 없습니다."
        )

    stage = medication_crud.get_snapshot_stage(db, meal.medication_snapshot_id)

    item = meal_crud.add_item(
        db,
        meal_id=meal.id,
        display_name=request.display_name,
        amount_g=amount_g,
        food_ref_id=food_ref_id,
        # 환산이 안 된 단위("2개")는 여기에만 남는다 — 사용자가 실제로 무엇을
        # 입력했는지가 유일하게 보존되는 자리다 (`to_grams` 독스트링 참고).
        raw_input={"amount": str(request.amount), "unit": request.unit},
    )
    meal_crud.mark_recalculating(db, meal)

    # 응답을 돌려주기 전에 커밋한다. 큐 적재는 이 커밋 뒤에 호출부가 한다 —
    # 순서가 뒤집히면 Worker 가 DB 에 없는 항목을 분석하려다 DLQ 로 간다.
    db.commit()

    return AddItemOutcome(
        response=MealItemCreateResponse(
            item_id=item.id,
            # 계약서(API.md 필드표)가 `matched` 를 "영양정보 유무"로 정의한다 —
            # "공공 DB 에서 음식을 찾았는가" 가 아니다. 음식은 찾았지만 g 환산이
            # 안 돼 성분을 못 만든 경우도 false 여야 FE 가 직접 입력으로 유도한다.
            # DB 의 food_ref_id 링크는 그대로 남는다(둘은 별개다).
            matched=nutrition is not None,
            nutrition=nutrition,
            status=meal.status,
            is_recalculation=meal.is_recalculation,
        ),
        analyze_task={
            "type": "meal.analyze",
            "mealId": str(meal.id),
            "mealType": meal.meal_type.value,
            "eatenAt": meal.eaten_at.isoformat(),
            "stage": stage.value,
            # TODO: 6번(POST /meals)에서 FileStorage 가 붙으면 presigned URL 을 싣는다.
            # AI 는 S3 권한이 없어 키만으로는 사진을 못 읽는다 (`_build_thumbnail_url` 과 같은 TODO).
            "imageUrl": None,
            "rawText": meal.raw_text,
        },
    )
