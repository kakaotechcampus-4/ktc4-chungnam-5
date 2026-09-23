"""식사(meal) 도메인 로직.

DB 세션은 직접 다루지 않고 `crud/` 를 통해서만 접근한다. 상태는 `crud/` 가 들고,
조합은 `worker/` · `api/` 가 한다.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.crud import meal as meal_crud
from app.models.enums import MealItemSource, MealStatus
from app.models.meal import Meal, MealItem
from app.schemas.meal import (
    RECALCULATION_STEPS,
    CalendarDay,
    CalendarSummary,
    MealCalendarResponse,
    MealDeleteResponse,
    MealItemCreateRequest,
    MealItemCreateResponse,
    MealItemDeleteResponse,
    MealItemsUpdateResponse,
    MealItemUpdate,
    MealListItem,
    MealListResponse,
    MealScores,
)
from app.schemas.nutrition import (
    ManualNutrition,
    NutritionInfo,
    NutritionUpdateResponse,
)

_KST_ZONE = ZoneInfo("Asia/Seoul")


class MealNotFoundError(Exception):
    """존재하지 않거나, 남의 것이거나, 이미 삭제된 식사를 가리킬 때."""


class MealNotEditableError(Exception):
    """지금 상태로는 음식을 고칠 수 없는 식사를 가리킬 때."""


class MealItemNotFoundError(Exception):
    """이 식사에 없는 항목을 가리킬 때. 남의 식사 항목도 여기로 온다."""


@dataclass(frozen=True)
class ResolvedItemUpdate:
    """수정 요청 한 건 + api 레이어가 미리 풀어 둔 값.

    `amount_g` 는 `to_grams`, `food_ref_id` 는 공공 DB 매칭 결과다. 둘 다
    `services/` 끼리 부를 수 없어서(README 절대 규칙 5) 호출부가 채워 넣는다.
    """

    request: MealItemUpdate
    amount_g: Decimal | None
    food_ref_id: str | None


# 그대로 g 으로 볼 수 있는 단위. 표기만 다른 같은 단위를 한 묶음으로 둔다.
# ml 은 물 기준 1ml ≈ 1g 로 근사한다. 국·음료가 대부분이라 오차를 감수할 만하다.
_GRAM_UNITS = {"g", "G", "그램"}
_MILLILITRE_UNITS = {"ml", "mL", "ML", "밀리리터"}
_GRAM_EQUIVALENT_UNITS = _GRAM_UNITS | _MILLILITRE_UNITS

_CURSOR_SEPARATOR = "|"

# 사용자가 음식을 고칠 수 있는 상태.
# FAILED 는 인식된 음식이 하나도 없는 상태라 "고친다" 는 말이 성립하지 않는다.
_EDITABLE_STATUSES = frozenset({MealStatus.REVIEW_REQUIRED, MealStatus.EVALUATED})


def _is_editable(meal: Meal) -> bool:
    """지금 이 식사의 음식을 고칠 수 있는가.

    **`ANALYZING` 은 뜻이 두 개다.** `is_recalculation` 이 가른다:

    - `False` — 최초 분석 중. Worker 가 `source=MODEL` 항목을 지우고 다시 넣는
      중이라(`jobs/analyze_meal.py` 6단계) 끼어들면 무엇이 남을지 알 수 없다.
      애초에 사용자에게는 "분석 중" 화면이라 고칠 수단도 없다 → **금지**
    - `True` — 사용자가 확인 화면에서 음식을 고쳐 재분석을 기다리는 중.
      사용자는 여전히 그 확인 화면에 있고 음식을 더 고치는 게 정상 흐름이다
      → **허용**

    둘을 구분하지 않고 `ANALYZING` 을 통째로 막으면 **음식을 하나밖에 못 넣는다** —
    첫 추가가 상태를 `ANALYZING` 으로 바꾸고, 그 상태가 두 번째 추가를 409 로
    막는다. 스스로 문을 잠그는 셈이다.
    """
    if meal.status in _EDITABLE_STATUSES:
        return True
    return meal.status is MealStatus.ANALYZING and meal.is_recalculation


def _editable_meal(db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID) -> Meal:
    """지금 음식을 고칠 수 있는 식사를 가져오거나 예외를 던진다.

    음식을 더하고·고치고·빼는 세 경로가 똑같은 관문을 지난다 — 어느 하나만
    느슨해지면 그 경로로만 남의 식사가 새거나, 워커가 `meal_items` 를 갈아엎는
    중간에 끼어든다. 그래서 복사본을 두지 않고 여기서만 판단한다.

    없는 식사·남의 식사·삭제된 식사는 전부 `MealNotFoundError` 로 같다
    (`crud.meal.get_owned_meal` 의 단일 WHERE — 소유권 누출 방지).
    고칠 수 없는 상태라면 `MealNotEditableError` 다(`_is_editable` 참고).
    """
    meal = meal_crud.get_owned_meal(db, user_id=user_id, meal_id=meal_id)
    if meal is None:
        raise MealNotFoundError(f"meal {meal_id} 를 찾을 수 없습니다.")

    if not _is_editable(meal):
        raise MealNotEditableError(
            f"{meal.status.value} 상태의 식사는 음식을 고칠 수 없습니다."
        )
    return meal


def to_grams(amount: float | Decimal | None, unit: str | None) -> Decimal | None:
    """`amount` + `unit` 을 g 으로 옮긴다. 옮길 수 없으면 None.

    AI 가 추정한 값(`jobs/analyze_meal.py` 7단계)과 사용자가 직접 입력한 값
    (`POST /meals/{mealId}/items`)이 **같은 규칙을 탄다** — 어느 쪽에서 왔든
    "2개" 는 g 이 아니다.

    **환산표가 없다.** `food_refs.serving_size` 는 "영양성분함량기준량"(성분값이
    어느 양 기준인지, 보통 100g)이지 "1개 = 50g" 이 아니다. 즉 "계란 2개" 를 g 으로
    바꿀 근거가 DB 에 없다.

    없는 근거를 지어내지 않는다. 지어낸 g 으로 Q/Q/S 를 채점하면 점수가 조용히
    틀리고, 사용자는 어디서 틀렸는지 알 수 없다. 대신 None 을 돌려준다 — 사용자가
    말한 양 자체는 `meal_items.confirmed_amount` · `confirmed_unit` 에 그대로 남고,
    `REVIEW_REQUIRED` 단계에서 g 으로 환산 가능한 양을 확인받아
    `confirmed_amount_g` 를 채우는 것이 이 상태의 존재 이유다.
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
) -> MealItemCreateResponse:
    """사용자가 직접 입력한 음식을 식사에 더하고 재계산 대기로 표시한다.

    `amount_g` · `food_ref_id` · `nutrition` 은 이미 결정된 값으로 들어온다.
    공공 DB 매칭은 `services/nutrition.py` 의 일이고 그 결과를 여기로 옮기는 건
    호출부(api 레이어)다 — services 끼리는 서로 참조하지 않는다(README 절대 규칙 5).

    ## 여기서 비동기 작업을 만들지 않는다

    사용자가 음식명과 양을 직접 알려줬으므로 AI 에게 물을 것이 없다. 다시 계산할
    Q/Q/S 는 순수 함수라 0.01 초면 끝나고(README 절대 규칙 2), 애초에 확인 화면은
    **[확인] 을 누르기 전까지 점수를 보여 주지 않는다** — 편집 중에는 계산할
    필요조차 없다.

    `ANALYZING` 은 "워커가 도는 중" 이 아니라 **"점수가 아직 유효하지 않다"** 는
    표시다. 해소는 사용자가 [확인] 을 누를 때 `POST /meals/{mealId}/confirm` 이
    한다 — `add_meal_item` 독스트링의 의존성 항목 참고.
    """
    meal = _editable_meal(db, user_id=user_id, meal_id=meal_id)

    item = meal_crud.add_item(
        db,
        meal_id=meal.id,
        display_name=request.display_name,
        amount_g=amount_g,
        food_ref_id=food_ref_id,
        amount=request.amount,
        unit=request.unit,
    )
    meal_crud.mark_recalculating(db, meal)

    # 트랜잭션 경계는 services 가 정한다(README 절대 규칙 5).
    db.commit()

    return MealItemCreateResponse(
        item_id=item.id,
        # API 명세서(필드표)가 `matched` 를 "영양정보 유무" 로 정의한다 — "공공 DB
        # 에서 음식을 찾았는가" 가 아니다. 음식은 찾았지만 g 환산이 안 돼 성분을
        # 못 만든 경우도 false 여야 FE 가 직접 입력으로 유도한다. DB 의
        # food_ref_id 링크는 그대로 남는다(둘은 별개다).
        matched=nutrition is not None,
        nutrition=nutrition,
        status=meal.status,
        is_recalculation=meal.is_recalculation,
    )


def resolved_amount(item: MealItem) -> tuple[Decimal | None, Decimal | None, str | None]:
    """고치기 전의 양 — `(g 환산값, 숫자, 단위)`.

    **확인 여부의 센티넬은 `confirmed_amount` 다.** `confirmed_amount_g` 가 아니다 —
    그쪽은 g 으로 환산된 값만 담아서 "2개" 로 확인한 항목도 NULL 이라, 그 NULL 은
    "확인 전" 과 "환산 불가" 를 구분하지 못한다. `estimated_amount_g` 로 폴백하면 이미
    확인된 "2개" 가 AI 추정값(100g)으로 되돌아가, 같은 "2개" 를 다시 보내도 `100g →
    2개` 로 보여 `user_corrections` 에 거짓 행이 매번 쌓인다. 확인 화면이 고치지 않은
    항목까지 보내는 게 정상 경로라(엔드포인트 독스트링) 이건 예외가 아니다.

    모델의 읽기 규칙 그대로다 — 확인됐으면 `confirmed_*`, 아니면 `estimated_*`.
    두 쌍이 같은 모양이라 폴백이 한 줄로 끝난다.
    """
    if item.confirmed_amount is not None:
        return (item.confirmed_amount_g, item.confirmed_amount, item.confirmed_unit)
    return (item.estimated_amount_g, item.estimated_amount, item.estimated_unit)


def _amount_key(
    amount_g: Decimal | None, amount: Decimal | None, unit: str | None
) -> tuple[str | None, ...]:
    """양을 비교 가능한 하나의 값으로 만든다.

    g 으로 환산된 값이 있으면 그것이 곧 양이다. 없으면("2개") 사용자가 입력한
    숫자·단위 쌍이 그 양의 유일한 표현이다 — 둘을 섞어 비교하면 "2개 → 3개" 가 둘 다
    g 이 NULL 이라 '안 고쳤다' 로 보인다.

    숫자는 **자릿수를 지우고** 비교한다. 저장된 값은 `Numeric(8, 2)` 를 거쳐 와서
    `Decimal("2.00")` 이지만 요청의 `2` 는 `Decimal("2")` 라, 날것으로 비교하면 같은
    "2개" 가 매번 '고쳤다' 로 잡힌다.
    """
    if amount_g is not None:
        return ("g", _compare_text(amount_g), _canonical_unit(unit))
    return ("raw", _compare_text(amount), _canonical_unit(unit))


def _canonical_unit(unit: str | None) -> str | None:
    """비교용 단위. 같은 단위의 표기 차이와 앞뒤 공백을 지운다.

    `g` · `G` · `그램` 은 같은 단위이고 `ml` · `mL` · `밀리리터` 도 그렇다 — 표기가
    다르다고 '고쳤다' 로 잡으면 거짓 이력이 쌓인다.

    **`ml → g` 은 반대로 고친 것이다.** `to_grams` 가 1ml ≈ 1g 로 근사하는 탓에 환산값이
    양쪽 `250.00` 으로 같아지는데, 단위 오인식은 AI 인식 성능 평가의 신호라 이력에
    남아야 한다. 그래서 g 분기에서도 단위를 키에 넣는다.

    저장된 단위는 워커가 쓴 그대로라 공백이 붙어 올 수 있다(요청 쪽은 pydantic 이 이미
    strip 한다) — 이름을 `.strip()` 후 비교하는 것과 같은 이유다.
    """
    if unit is None:
        return None
    stripped = unit.strip()
    if stripped in _GRAM_UNITS:
        return "g"
    if stripped in _MILLILITRE_UNITS:
        return "ml"
    return stripped


def _compare_text(value: Decimal | None) -> str | None:
    """자릿수를 지운 비교용 문자열. `2.00` 과 `2` 가 같은 값이 된다.

    `normalize()` 만 쓰면 `250.00` 이 `2.5E+2` 가 되어 `250` 과 또 어긋난다 —
    지수 표기를 `f` 포맷으로 되돌린다.
    """
    if value is None:
        return None
    return format(value.normalize(), "f")


def _as_text(value: Decimal | None) -> str | None:
    """Decimal 을 JSONB 에 담을 문자열로.

    JSONB 직렬화가 Decimal 을 모르고, float 로 바꾸면 `250.00` 이 `250.0` 이 되어
    자릿수가 사라진다 — 인식 오차를 나중에 계산할 값이라 그대로 보존한다.
    """
    return str(value) if value is not None else None


def update_items(
    db: Session,
    *,
    user_id: uuid.UUID,
    meal_id: uuid.UUID,
    resolved: list[ResolvedItemUpdate],
) -> MealItemsUpdateResponse:
    """사용자가 고친 음식들을 반영하고 재계산 대기로 표시한다.

    `add_item` 과 같은 규칙을 따른다 — g 환산과 공공 DB 매칭은 이미 끝난 값으로
    들어오고(README 절대 규칙 5), 여기서는 비동기 작업을 만들지 않는다.

    ## 전부 되거나 전부 안 되거나

    요청한 항목 중 하나라도 이 식사의 것이 아니면 `MealItemNotFoundError` 를 내고
    **아무것도 반영하지 않는다.** 확인 화면은 여러 항목을 한 번에 보내므로, 절반만
    반영되면 사용자는 화면과 서버 중 어느 쪽이 맞는지 알 수 없다.
    """
    meal = _editable_meal(db, user_id=user_id, meal_id=meal_id)

    items = meal_crud.get_items_by_ids(
        db, meal_id=meal.id, item_ids=[update.request.item_id for update in resolved]
    )
    by_id = {item.id: item for item in items}

    # 고치기 전에 전부 확인한다. 루프 안에서 확인하면 앞쪽 항목은 이미 바뀐 채로
    # 예외가 나가고, "아무것도 반영되지 않았다" 는 세션 롤백이 대신 지켜 주는
    # 우연한 성질이 된다 — 규칙이면 코드로 드러나 있어야 한다.
    missing = [
        update.request.item_id
        for update in resolved
        if update.request.item_id not in by_id
    ]
    if missing:
        # 하나만 알리면 FE 가 낡은 확인 화면을 한 번에 고치지 못한다.
        ids = ", ".join(str(item_id) for item_id in missing)
        raise MealItemNotFoundError(f"item {ids} 는 이 식사의 항목이 아닙니다.")

    for update in resolved:
        item = by_id[update.request.item_id]
        # 저장된 이름은 워커가 쓴 그대로고 요청의 이름은 pydantic 이 strip 한 값이다.
        # 날것으로 비교하면 공백 하나 차이가 rename 으로 보이고, 이름 매칭이 못
        # 좁히는 순간 멀쩡한 `food_ref_id` 가 끊긴다.
        renamed = item.display_name.strip() != update.request.display_name

        # 반영 전 값은 여기서 전부 잡아 둔다 — `update_item` 뒤에 읽으면 방금 쓴
        # 값이라 "안 고쳤다" 가 된다.
        stored = resolved_amount(item)
        stored_amount_g, stored_amount, stored_unit = stored
        before: dict[str, str | None] = {
            "displayName": item.display_name,
            "amountG": _as_text(stored_amount_g),
            "confidence": _as_text(item.confidence),
            "amount": _as_text(stored_amount),
            "unit": stored_unit,
        }
        # 고친 값은 요청에서 만든다. 항목을 다시 읽으면 안 된다 — 환산이 안 된
        # 단위("2개")는 `confirmed_amount_g` 가 NULL 이라 고치기 전 값(AI 추정값)으로
        # 되돌아가 '안 고쳤다' 로 보인다.
        after: dict[str, str | None] = {
            "displayName": update.request.display_name,
            "amountG": _as_text(update.amount_g),
            "amount": _as_text(update.request.amount),
            "unit": update.request.unit,
        }

        meal_crud.update_item(
            db,
            item=item,
            display_name=update.request.display_name,
            amount=update.request.amount,
            unit=update.request.unit,
            amount_g=update.amount_g,
            # 이름이 그대로면 기존 링크를 지킨다. 다시 찾으면 AI 가 정확히 연결해 둔
            # 항목이 끊길 수 있다 — 이름 매칭은 흔한 음식에 None 을 주기 때문이다
            # (`endpoints/meal_items.py` 의 update_meal_items 독스트링 참고).
            # 지킬 링크가 없을 때(`food_ref_id IS NULL`)는 얘기가 다르다. AI 가
            # `candidateFoodRefId` 를 못 줬다는 뜻이고 이름 매칭은 워커가 쓰지 않는
            # 별개의 신호라, 방금 찾아 온 결과를 버리면 "미역국 2개 → 200g" 처럼
            # 이제 환산이 되는 수정도 영양정보를 영영 못 얻는다. 끊을 링크가 없으니
            # 보수적으로 굴 이유도 없다 — POST 가 이름 매칭만으로 링크를 거는 것과
            # 같은 신호다.
            food_ref_id=(
                update.food_ref_id if renamed or item.food_ref_id is None else item.food_ref_id
            ),
            # 이름을 바꿨으면 그 신뢰도는 더는 이 항목의 것이 아니다. 남겨 두면
            # 사용자가 직접 써 넣은 이름이 FE 에서 "AI 가 자신 없어함"(`< 0.8`)으로
            # 강조된다. 값은 바로 위 `before` 에 담겨 user_corrections 로 간다.
            confidence=None if renamed else item.confidence,
        )

        # 이름은 `renamed` 를 그대로 쓴다 — 공백만 다른 건 사용자가 고친 게 아니라
        # FE 가 화면의 값을 돌려보낸 것이고, 이력에 쌓이면 인식 오차 통계에 섞인다.
        changed = renamed or _amount_key(*stored) != _amount_key(
            update.amount_g, update.request.amount, update.request.unit
        )

        # 직접 입력한 영양성분(`PUT .../nutrition` 의 `manual`)은 **섭취량 기준
        # 총량**이라 양이 바뀌면 거짓이 되고, 이름이 바뀌면 다른 음식의 값이 된다.
        # 남겨 두면 그 값이 Q/Q/S 채점까지 조용히 흘러간다 — 비어 있는 편이 낫다는
        # 기존 원칙 그대로 지우고 `matched: false` 폴백으로 되돌린다
        # (`endpoints/meal_items.py` 의 `add_meal_item` 독스트링).
        #
        # **`changed` 여야 한다.** 확인 화면은 고치지 않은 항목까지 보내므로
        # (이 함수의 독스트링) 무조건 지우면 사용자는 아무것도 고치지 않았는데
        # 직접 입력한 값을 잃는다.
        if changed:
            meal_crud.clear_item_manual_nutrition(db, item=item)

        if item.source is MealItemSource.MODEL and changed:
            meal_crud.add_correction(
                db,
                meal_item_id=item.id,
                original_value=before,
                corrected_value=after,
            )

    meal_crud.mark_recalculating(db, meal)

    # 트랜잭션 경계는 services 가 정한다(README 절대 규칙 5).
    db.commit()

    return MealItemsUpdateResponse(
        status=meal.status,
        is_recalculation=meal.is_recalculation,
        steps=list(RECALCULATION_STEPS),
    )


@dataclass(frozen=True)
class NutritionTarget:
    """영양정보를 붙일 항목 + 그 항목의 지금 확정된 양.

    `amount_g` 를 함께 내보내는 건 호출부(api 레이어)가 공공 DB 환산을 돌려야
    하는데(README 절대 규칙 5 — services 끼리는 서로 부르지 않는다) 그 환산에
    먹은 양이 필요하기 때문이다. 읽기 규칙(`resolved_amount`)을 호출부가 다시
    구현하면 두 곳이 조용히 어긋난다.
    """

    meal: Meal
    item: MealItem
    amount_g: Decimal | None


def get_nutrition_target(
    db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID, item_id: uuid.UUID
) -> NutritionTarget:
    """영양정보를 고칠 항목을 소유권·상태째 확인해서 가져온다.

    형제 엔드포인트 셋과 **똑같은 관문**을 지난다(`_editable_meal`) — 없는 식사·
    남의 식사·삭제된 식사는 전부 `MealNotFoundError`, 고칠 수 없는 상태는
    `MealNotEditableError` 다.

    반영은 `set_item_nutrition` 이 한다. 둘로 나뉜 건 그 사이에서 api 레이어가
    공공 DB 환산을 돌려야 하기 때문이다(`NutritionTarget` 참고).
    """
    meal = _editable_meal(db, user_id=user_id, meal_id=meal_id)

    item = meal_crud.get_item(db, meal_id=meal.id, item_id=item_id)
    if item is None:
        raise MealItemNotFoundError(f"item {item_id} 는 이 식사의 항목이 아닙니다.")

    return NutritionTarget(meal=meal, item=item, amount_g=resolved_amount(item)[0])


def _manual_nutrition(item: MealItem) -> NutritionInfo | None:
    """항목에 직접 입력된 영양성분. 하나도 없으면 None.

    **환산하지 않는다** — `manual_*` 는 섭취량 기준 총량이다(`MealItem` 컬럼 주석).
    """
    values = (
        item.manual_kcal,
        item.manual_protein_g,
        item.manual_fat_g,
        item.manual_carb_g,
        item.manual_fiber_g,
        item.manual_sodium_mg,
    )
    if all(value is None for value in values):
        return None

    kcal, protein_g, fat_g, carb_g, fiber_g, sodium_mg = values
    return NutritionInfo(
        kcal=kcal,
        protein_g=protein_g,
        fat_g=fat_g,
        carb_g=carb_g,
        fiber_g=fiber_g,
        sodium_mg=sodium_mg,
    )


def item_nutrition(
    item: MealItem, public_db_nutrition: NutritionInfo | None
) -> tuple[NutritionInfo | None, str | None]:
    """항목의 영양정보와 그 **출처**를 유도한다 — `(nutrition, nutritionSource)`.

    `meal_items` 에 `nutrition_source` 컬럼은 없다. 그 설계는 **유도하는 코드가 한
    곳일 때만** 성립하므로, 이 필드를 내보내는 응답은 전부 여기를 거쳐야 한다
    (`PUT .../nutrition` · `GET /meals/{mealId}` · `POST .../confirm`). 두 곳이
    각자 계산하면 같은 항목이 화면마다 다른 출처로 보인다.

    규칙은 한 줄이다:

        직접 입력이 있으면 USER_INPUT → 없고 공공 DB 환산이 되면 PUBLIC_DB → 둘 다 없으면 null

    **사용자가 고른 후보도 `PUBLIC_DB` 다.** 이 필드가 답하는 질문은 "숫자가 어디서
    왔는가" 이지 "누가 골랐는가" 가 아니다 — 명세서의 `evidence.dbSource` 와 같은
    자리다.

    `public_db_nutrition` 을 인자로 받는 건 그 환산이 `services/nutrition.py` 의
    일이고 services 끼리는 서로 부르지 않기 때문이다(README 절대 규칙 5).
    """
    manual = _manual_nutrition(item)
    if manual is not None:
        return manual, "USER_INPUT"
    if public_db_nutrition is not None:
        return public_db_nutrition, "PUBLIC_DB"
    return None, None


def set_item_nutrition(
    db: Session,
    *,
    target: NutritionTarget,
    food_ref_id: str | None,
    manual: ManualNutrition | None,
    public_db_nutrition: NutritionInfo | None,
) -> NutritionUpdateResponse:
    """사용자가 고른 영양정보를 항목에 붙이고 재계산 대기로 표시한다.

    `add_item` · `update_items` · `delete_item` 과 같은 순서다 — 바꾸고, 식사를
    재계산 대기로 옮기고, 커밋한다. 여기서도 큐에 아무것도 넣지 않는다
    (`api/v1/endpoints/meal_items.py` 의 `add_meal_item` 독스트링 참고).

    출처는 저장하지 않고 `item_nutrition` 이 유도한다.

    ## 직접 입력은 기존 링크를 끊지 않는다

    "어느 음식으로 봤는가" 는 그 자체로 남길 값이고, 직접 입력이 지워지면 돌아갈
    자리이기도 하다(`test_changing_the_amount_falls_back_to_the_linked_public_db_values`).
    반대로 후보를 고르면 링크가 그 후보로 **바뀐다.**

    ## 이전 직접 입력은 새 값이 그 자리를 채울 때만 지운다

    후보를 골랐고 환산까지 됐으면 그 값이 유효한 최신 선택이므로 이전 직접 입력을
    남기면 안 된다 — 읽기 규칙상 직접 입력이 계속 이겨 **후보 선택이 아무 일도 하지
    않은 것처럼 보인다.**

    하지만 고른 후보로 아무 값도 못 만들었다면("계란 2개") 얘기가 다르다. 거기서도
    지우면 사용자는 요청 한 번으로 갖고 있던 유일한 영양정보를 잃고 폴백 시작점으로
    되돌아간다 — 얻은 것 없이 잃기만 한다. 그때는 그대로 둔다.
    """
    # 후보를 고른 요청은 링크를 바꾸고, 직접 입력(`food_ref_id` 없음)은 지킨다.
    link = food_ref_id if food_ref_id is not None else target.item.food_ref_id
    meal_crud.set_item_food_ref(db, item=target.item, food_ref_id=link)

    if manual is not None:
        meal_crud.set_item_manual_nutrition(
            db,
            item=target.item,
            kcal=manual.kcal,
            protein_g=manual.protein_g,
            fat_g=manual.fat_g,
            carb_g=manual.carb_g,
            fiber_g=manual.fiber_g,
            sodium_mg=manual.sodium_mg,
        )
    elif public_db_nutrition is not None:
        meal_crud.clear_item_manual_nutrition(db, item=target.item)

    meal_crud.mark_recalculating(db, target.meal)

    # 트랜잭션 경계는 services 가 정한다(README 절대 규칙 5).
    db.commit()

    nutrition, source = item_nutrition(target.item, public_db_nutrition)

    return NutritionUpdateResponse(
        item_id=target.item.id,
        # 형제 엔드포인트와 같은 정의 — "영양정보가 나가는가" 지 "공공 DB 에서
        # 찾았는가" 가 아니다(`add_item` 참고).
        matched=nutrition is not None,
        nutrition_source=source,
        nutrition=nutrition,
        status=target.meal.status,
        is_recalculation=target.meal.is_recalculation,
    )


def delete_item(
    db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID, item_id: uuid.UUID
) -> MealItemDeleteResponse:
    """사용자가 확인 화면에서 뺀 음식을 지우고 재계산 대기로 표시한다.

    `add_item` · `update_items` 와 같은 순서를 따른다 — 식사를 소유권째 확인하고,
    지금 고칠 수 있는 상태인지 보고, 바꾼 뒤 커밋한다(README 절대 규칙 5).

    ## 마지막 항목도 지울 수 있다

    항목이 0 개인 식사가 남는다. 막으면 "AI 가 잘못 인식한 유일한 항목을 지우고
    올바른 걸 넣기" 가 POST 를 먼저 해야 하는 순서 제약이 되고, 사용자는 왜 그
    순서여야 하는지 알 길이 없다. 식사를 통째로 지우는 경로는
    `DELETE /meals/{mealId}` 로 이미 따로 있다.

    ## 여기서 비동기 작업을 만들지 않는다

    사용자가 뺀 음식이라 AI 에게 물을 것이 없고, 다시 계산할 Q/Q/S 는 순수 함수라
    0.01 초면 끝난다(README 절대 규칙 2). `ANALYZING` 은 "워커가 도는 중" 이 아니라
    **"점수가 아직 유효하지 않다"** 는 표시이며, 해소는 사용자가 [확인] 을 누를 때
    불리는 `POST /meals/{mealId}/confirm` 이 한다 — `add_item` 과 같다.
    """
    meal = _editable_meal(db, user_id=user_id, meal_id=meal_id)

    item = meal_crud.get_item(db, meal_id=meal.id, item_id=item_id)
    if item is None:
        raise MealItemNotFoundError(f"item {item_id} 는 이 식사의 항목이 아닙니다.")

    meal_crud.delete_item(db, item=item)
    meal_crud.mark_recalculating(db, meal)

    # 트랜잭션 경계는 services 가 정한다(README 절대 규칙 5).
    db.commit()

    return MealItemDeleteResponse(
        status=meal.status,
        is_recalculation=meal.is_recalculation,
    )
