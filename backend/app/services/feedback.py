"""끼니 피드백 조회 · 사후 포만감 체크인.

`crud/` 를 조합하고 트랜잭션 경계를 정한다 (절대 규칙 5). 다른 `services/` 를
참조하지 않는다.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.crud import feedback as feedback_crud
from app.crud import meal as meal_crud
from app.crud import satiety as satiety_crud
from app.models.meal import Meal
from app.schemas.feedback import (
    FeedbackSuggestion,
    MealFeedbackResponse,
    SatietyCheckinRequest,
    SatietyCheckinResponse,
    SuggestionNutrient,
)


class MealNotFoundError(LookupError):
    """없는 식사 · 남의 식사 · 삭제된 식사. 셋 다 404 로 같게 응답한다."""


def _owned_meal(db: Session, user_id: uuid.UUID, meal_id: uuid.UUID) -> Meal:
    meal = meal_crud.get_owned_meal(db, user_id=user_id, meal_id=meal_id)
    if meal is None:
        raise MealNotFoundError(f"meal {meal_id} 를 찾을 수 없습니다.")
    return meal


def _build_suggestions(db: Session, stored: list[dict] | None) -> list[FeedbackSuggestion]:
    """저장된 제안에 성분을 채운다.

    저장돼 있는 건 AI 가 준 `{foodName, advice, candidateFoodRefId}` 뿐이다
    (`models/feedback.py::MealFeedback.suggestions`). `nutrients` 는 여기서
    `food_refs` 를 한 번 조회해 채운다 — 저장하면 그 표가 갱신될 때 낡는다.

    **참조를 못 찾아도 항목을 버리지 않는다.** `nutrients` 만 비운다 — 제안 문구
    ("두부 반 모를 더해요")는 성분 숫자가 없어도 그대로 쓸모가 있다.
    """
    if not stored:
        return []

    ref_ids = [
        ref_id
        for item in stored
        if (ref_id := item.get("candidateFoodRefId")) is not None
    ]
    by_ref = feedback_crud.nutrients_by_food_ref(db, ref_ids)

    suggestions: list[FeedbackSuggestion] = []
    for item in stored:
        resolved = by_ref.get(item.get("candidateFoodRefId"))
        suggestions.append(
            FeedbackSuggestion(
                food_name=item.get("foodName", ""),
                advice=item.get("advice", ""),
                nutrients=[
                    SuggestionNutrient(code=code, amount_g=float(amount))
                    for code, amount in (resolved.amounts if resolved else {}).items()
                ],
            )
        )
    return suggestions


def get_feedback(
    db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID
) -> MealFeedbackResponse:
    """끼니 피드백을 읽는다. 없으면 "아직" 이지 에러가 아니다.

    **`GENERATING` · `FAILED` 는 내지 않는다.** 그걸 알려면 `task_queue` 를 봐야 하는데
    유도 규칙이 명세에 없다. 지금은 행이 있으면 `READY`, 없으면 `PENDING` 이다 —
    워커(`worker/jobs/feedback_meal.py`)가 붙을 때 그 티켓에서 정한다.

    **여기서 생성을 시작하지 않는다.** `worker/dispatch.py` 가 "사용자가 '다음 끼니
    제안 보기' 를 눌렀을 때 그 API 가 큐에 넣는다" 고 적어 두었지만, 명세에 그 API 가
    없다. 남은 후보가 이 GET 뿐인데 조회가 부수효과로 작업을 만드는 모양이라
    정하고 가야 한다 — 장기 피드백은 `POST /insights/long-term/refresh` 가 따로 있다.
    """
    meal = _owned_meal(db, user_id, meal_id)

    row = feedback_crud.get_by_meal(db, meal.id)
    if row is None:
        return MealFeedbackResponse.pending()

    # 마스킹은 스키마 팩토리가 한다 — `GET /meals/{mealId}` 도 같은 행을 읽으므로
    # 조립부마다 쓰면 코드가 두 벌이 되고 한쪽만 고쳐진다.
    return MealFeedbackResponse.from_row(
        row, suggestions=_build_suggestions(db, row.suggestions)
    )


def add_checkin(
    db: Session,
    *,
    user_id: uuid.UUID,
    meal_id: uuid.UUID,
    request: SatietyCheckinRequest,
) -> SatietyCheckinResponse:
    """사후 포만감을 기록한다. 커밋까지 한다.

    **상태를 보지 않는다.** 포만감은 확정 여부와 무관하게 사용자가 실제로 겪는 일이고,
    명세에도 상태 조건이 없다.

    한 요청이 **두 테이블**에 쓴다. 체크인은 시점마다 하나(`satiety_checkins`),
    "다시 배고파진 시각" 과 한마디는 식사당 하나(`satiety_logs`)라 자리가 다르다 —
    명세의 `GET /meals/{mealId}` 가 그 둘을 `checkins[]` 바깥에 두는 것과 같다.
    """
    meal = _owned_meal(db, user_id, meal_id)

    checkin = satiety_crud.upsert_checkin(
        db,
        meal_id=meal.id,
        offset_hours=request.checkin_offset_hours,
        pct=request.satiety_pct,
    )
    log = satiety_crud.set_hunger_return(
        db,
        meal_id=meal.id,
        minutes=request.hunger_return_minutes,
        comment=request.comment,
    )
    db.commit()

    return SatietyCheckinResponse(
        checkin_id=checkin.id,
        meal_id=meal.id,
        checkin_offset_hours=checkin.checkin_offset_hours,
        satiety_pct=checkin.satiety_pct,
        # 방금 보낸 값이 아니라 **저장된 값**을 돌려준다. 이번에 안 보냈으면 앞서
        # 적어 둔 값이 그대로 나간다 — 사용자가 화면에서 보는 것과 맞다.
        hunger_return_minutes=log.hunger_return_minutes,
    )
