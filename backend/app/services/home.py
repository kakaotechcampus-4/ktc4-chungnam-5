"""홈(GET /home) 조립 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 다른 services/ 모듈을 import 하지 않는다 —
투약 계산(services/medication.get_current_view)은 엔드포인트가 호출해 결과만 넘긴다.
읽기 전용이라 커밋하지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import ApiError, ErrorCode
from app.core.time import KST, kst_day_range
from app.crud import feedback as feedback_crud
from app.crud import meal as meal_crud
from app.crud import user as user_crud
from app.models.enums import MealType, SafetyStatus
from app.models.feedback import MealFeedback
from app.schemas.home import HomeMeal, HomeMedication, HomeResponse, HomeStomach, HomeToday
from app.schemas.meal import MealScores
from app.schemas.medication import CurrentMedicationResponse

# missingMealTypes 에 들어갈 수 있는 끼니와 그 순서. SNACK 은 "빠진 끼니"가 아니라 넣지 않는다.
# 아직 시간이 안 된 끼니도 거르지 않는다 — 명세 §7 예시(18시 전 기록 2끼 → ["DINNER"])와 같다.
_MAIN_MEAL_TYPES = (MealType.BREAKFAST, MealType.LUNCH, MealType.DINNER)


def _ensure_user_exists(db: Session, user_id: uuid.UUID) -> None:
    # services/user_state.py 의 _ensure_user_exists 와 같은 모양 — services 끼리 import 하지 않는다.
    if user_crud.get(db, user_id) is None:
        raise ApiError(ErrorCode.USER_NOT_FOUND, "사용자를 찾을 수 없습니다.", 404)


def _build_scores(
    quantity: Decimal | None,
    quality: Decimal | None,
    satiety: Decimal | None,
) -> MealScores | None:
    """세 점수가 전부 없으면(아직 미평가) None, 하나라도 있으면 객체로 감싼다."""
    # services/meal.py 의 _build_scores 와 같은 규칙 — services 끼리 import 하지 않는다.
    # 어긋나면 홈과 GET /meals 의 점수가 갈린다 (test_home_api.py #19 가 비교한다).
    if quantity is None and quality is None and satiety is None:
        return None
    return MealScores(
        quantity=round(quantity) if quantity is not None else None,
        quality=round(quality) if quality is not None else None,
        satiety=round(satiety) if satiety is not None else None,
    )


def _build_thumbnail_url(image_key: str | None) -> str | None:
    # services/meal.py 의 _build_thumbnail_url 과 같은 규칙 — services 끼리 import 하지 않는다.
    # 그쪽이 presigned URL 로 바뀌면 여기도 같이 바꾼다 (test_home_api.py #21 이 GET /meals 와 비교한다).
    if image_key is None:
        return None
    return f"/media/{image_key}"


def _safe_feedback_body(feedback: MealFeedback | None) -> str | None:
    # services/meal.py 의 _build_feedback 과 같은 노출 규칙 — services 끼리 import 하지 않는다.
    # SAFE 만 노출한다 — REVIEW_REQUIRED(가드레일 전)도 BLOCKED 와 똑같이 숨긴다.
    if feedback is None or feedback.safety_status is not SafetyStatus.SAFE:
        return None
    return feedback.body


def _build_medication(view: CurrentMedicationResponse | None) -> HomeMedication | None:
    if view is None:
        return None
    return HomeMedication(
        drug_name=view.drug_name,
        dose_mg=view.dose_mg,
        dose_count=view.dose_count,
        stage=view.stage,
        next_dose_date=view.next_dose_date,
        days_until_next_dose=view.days_until_next_dose,
        # 미래 날짜 투약 구간을 만드는 공개 경로가 없어 "예정된 용량 변경"이 생길 수 없다 —
        # services/medication.py 에서 시작일은 미래 불가, 용량 변경일은 늘 오늘, PATCH 도 오늘
        # 뒤로 옮기지 못한다. 그래서 지금은 늘 false 다. 판단을 지어내지 않는다 (규칙 1).
        dose_change_scheduled=False,
    )


def _build_today(db: Session, *, user_id: uuid.UUID, day: date) -> HomeToday:
    range_start, range_end = kst_day_range(day)
    rows = meal_crud.list_meals_in_range(
        db, user_id=user_id, range_start=range_start, range_end=range_end
    )
    display_names = meal_crud.get_display_names(db, [meal.id for meal, *_ in rows])

    meals = [
        HomeMeal(
            meal_id=meal.id,
            meal_type=meal.meal_type,
            eaten_at=meal.eaten_at,
            display_name=display_names.get(meal.id, ""),
            thumbnail_url=_build_thumbnail_url(meal.image_key),
            scores=_build_scores(quantity, quality, satiety),
        )
        for meal, quantity, quality, satiety in rows
    ]

    recorded_types = {meal.meal_type for meal in meals}
    return HomeToday(
        recorded_count=len(meals),
        meals=meals,
        missing_meal_types=[t for t in _MAIN_MEAL_TYPES if t not in recorded_types],
    )


def _build_stomach(db: Session, *, user_id: uuid.UUID, now: datetime) -> HomeStomach | None:
    # 기준은 날짜와 무관하게 satiety_after 가 기록된 가장 최근 식사다. 없으면 값을 지어내지 않고 None.
    row = meal_crud.get_latest_meal_with_satiety_after(db, user_id=user_id)
    if row is None:
        return None
    meal, satiety_after = row

    # 분 단위 내림. eaten_at 이 now 보다 미래면(시계 차이·미래 시각 입력) 음수 대신 0 이다.
    minutes_since_meal = max(0, int((now - meal.eaten_at).total_seconds() // 60))

    return HomeStomach(
        satiety_pct=satiety_after,
        source_meal_id=meal.id,
        source_meal_at=meal.eaten_at,
        minutes_since_meal=minutes_since_meal,
        feedback_summary=_safe_feedback_body(feedback_crud.get_by_meal(db, meal.id)),
    )


def get_home(
    db: Session,
    *,
    user_id: uuid.UUID,
    medication: CurrentMedicationResponse | None,
    now: datetime,
) -> HomeResponse:
    """홈 화면 한 번에 필요한 데이터를 조립한다.

    medication 은 엔드포인트가 구한 GET /medications/current 와 같은 계산 결과다 (미등록이면 None).
    now 는 KST aware 시각 — "오늘"과 minutesSinceMeal 이 같은 순간을 쓰게 한다.
    없는 사용자는 404 USER_NOT_FOUND.
    """
    _ensure_user_exists(db, user_id)

    today = now.astimezone(KST).date()
    return HomeResponse(
        date=today,
        medication=_build_medication(medication),
        stomach=_build_stomach(db, user_id=user_id, now=now),
        today=_build_today(db, user_id=user_id, day=today),
    )
