"""끼니 피드백 조회 · 사후 포만감 체크인.

`crud/` 를 조합하고 트랜잭션 경계를 정한다 (절대 규칙 5). 다른 `services/` 를
참조하지 않는다.
"""

from __future__ import annotations

import uuid
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.crud import evaluation as evaluation_crud
from app.crud import feedback as feedback_crud
from app.crud import meal as meal_crud
from app.crud import satiety as satiety_crud
from app.models.enums import MealStatus
from app.models.meal import Meal
from app.schemas.feedback import (
    ExpectedSatiety,
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


class _StoredSuggestion(NamedTuple):
    food_name: str
    advice: str
    ref_id: str | None


def _coerce(item: object) -> _StoredSuggestion | None:
    """저장된 제안 한 건을 쓸 수 있는 모양으로 바꾼다. 못 쓰면 `None`.

    **이 값은 신뢰할 수 없는 입력이다.** 컬럼이 JSONB 라 DB 는 아무 JSON 이나 받고,
    쓰는 쪽은 AI 응답을 담을 워커다. 계약이 바뀌거나 부분 응답이 오면 모양이 어긋난다.
    검증 없이 `item.get(...)` 을 부르면 문자열 원소 하나에 `AttributeError` 가 나고,
    **그 끼니 피드백 전체(요약·근거까지)를 못 읽는다.** 한 건이 깨졌다고 나머지를
    버릴 이유는 없다.

    `or ""` 를 쓰는 건 `{"foodName": null}` 때문이다 — 키가 있으면
    `get(k, "")` 의 기본값이 안 먹어 `None` 이 그대로 나오고, `food_name: str` 에서
    터진다.

    문구가 하나라도 비면 버린다. `nutrients` 만 남은 카드는 FE 에 그릴 게 없다.
    """
    if not isinstance(item, dict):
        return None

    food_name = item.get("foodName") or ""
    advice = item.get("advice") or ""
    if not isinstance(food_name, str) or not isinstance(advice, str):
        return None
    # **하나라도 비면 버린다.** 명세의 카드는 음식 이름과 조언 문구가 짝이라
    # (`{foodName, nutrients, advice}`), 한쪽이 없으면 FE 가 반쪽 카드를 그린다.
    if not food_name or not advice:
        return None

    ref_id = item.get("candidateFoodRefId")
    # str 이 아닌 건 조회에 넘기지 않는다 — int 는 `varchar = integer` 로 DB 가
    # 거부하고, list 는 `set()` 에서 unhashable 로 터진다.
    return _StoredSuggestion(food_name, advice, ref_id if isinstance(ref_id, str) else None)


def _build_suggestions(db: Session, stored: object) -> list[FeedbackSuggestion]:
    """저장된 제안에 성분을 채운다.

    저장돼 있는 건 AI 가 준 `{foodName, advice, candidateFoodRefId}` 뿐이다
    (`models/feedback.py::MealFeedback.suggestions`). `nutrients` 는 여기서
    `food_refs` 를 한 번 조회해 채운다 — 저장하면 그 표가 갱신될 때 낡는다.

    **참조를 못 찾아도 항목을 버리지 않는다.** `nutrients` 만 비운다 — 제안 문구
    ("두부 반 모를 더해요")는 성분 숫자가 없어도 그대로 쓸모가 있다. 모양이 깨진
    항목만 `_coerce` 가 걸러낸다.
    """
    if not isinstance(stored, list):
        return []

    items = [parsed for item in stored if (parsed := _coerce(item)) is not None]
    if not items:
        return []

    by_ref = feedback_crud.nutrients_by_food_ref(
        db, [item.ref_id for item in items if item.ref_id is not None]
    )

    return [
        FeedbackSuggestion(
            food_name=item.food_name,
            advice=item.advice,
            nutrients=[
                SuggestionNutrient(code=code, amount_g=float(amount))
                for code, amount in (
                    by_ref[item.ref_id].amounts
                    if item.ref_id is not None and item.ref_id in by_ref
                    else {}
                ).items()
            ],
        )
        for item in items
    ]


def _expected_satiety(db: Session, meal_id: uuid.UUID) -> ExpectedSatiety | None:
    """`expectedSatietyPct` 를 만든다. 점수가 없으면 `None`.

    `current` 는 `qqs_evaluations.satiety_score` 다. 룰엔진이 요청의
    `satietyAfterPct` 를 그대로 점수로 쓰므로(`rule_engine.py::score_satiety`) 두 값은
    같고, 저장된 쪽을 읽으면 확정 시점의 값이 보장된다.

    ⚠️ **`after` 에 같은 값을 넣는다** — 팀 결정. 예측 소스가 없어서다(자세한 근거는
    `schemas/feedback.py::ExpectedSatiety`). 제안을 반영한 예측이 생기기 전까지
    "변화 없음" 으로 나간다.
    """
    row = evaluation_crud.get_by_meal(db, meal_id)
    if row is None or row.satiety_score is None:
        return None
    # `round` 다 — 컬럼이 `Numeric(5, 2)` 라 소수 점수가 들어올 수 있다.
    # `int()` 로 자르면 68.7 이 68 이 되어, 반올림하는 다른 화면과 같은 끼니가
    # 68 과 69 로 갈린다. 지금은 `score_satiety` 가 정수만 만들어 차이가 없다.
    current = round(row.satiety_score)
    return ExpectedSatiety(current=current, after=current)


def get_feedback(
    db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID
) -> MealFeedbackResponse:
    """끼니 피드백을 읽는다. 없으면 "아직" 이지 에러가 아니다.

    지금은 행이 있고 내용이 차 있으면 `READY`, 아니면 `PENDING` 이다. 명세가 주는
    것은 값 목록(`:46`)과 예시 둘(`POST /confirm` → `PENDING`, 여기 → `READY`)뿐이고
    **유도 규칙은 없다.** 그 둘은 이미 만족한다.

    🔗 TODO(`worker/` 담당 @leekh2002 · FE 와 합의 후): **큐 등록과
    `GENERATING`/`FAILED` 를 여기서 맡는다.** 방향은 정해졌고 구현만 남았다.

      1. **큐에 넣는 건 이 GET 이다.** `worker/dispatch.py` 가 "사용자가 '다음 끼니
         제안 보기' 를 눌렀을 때 그 API 가 `feedback.meal` 을 넣는다" 고 정해 두었고,
         명세에서 그 화면에 대응하는 엔드포인트가 이것뿐이다. FE 의
         `meal_evaluation_screen.dart` 에도 같은 자리에 TODO 가 있다. 확정됐는데
         내용이 없으면 `enqueue` + `db.commit()` 한다 — **조회가 쓰기를 하게 되므로
         이 서비스는 더 이상 읽기 전용이 아니다.**

      2. **중복은 DB 제약으로 막는다.** FE 가 1.5 초 간격으로 폴링하므로 조회 후
         삽입으로는 경쟁을 못 막는다. `task_queue` 에 부분 유니크를 건다:

             UNIQUE ((payload->>'mealId'))
             WHERE type = 'feedback.meal' AND status = 'PENDING'

         `type` 으로 좁혀 다른 작업에 영향이 없고, `PENDING` 조건이라 끝나면 풀려서
         재확정 후 다시 걸린다. 넣을 때 `ON CONFLICT DO NOTHING`.
         `task_queue` 는 공용이라 담당자 확인이 먼저다.

      3. **`FAILED` 는 재시도 진입점도 여기다.** 워커는 `QUEUE_MAX_ATTEMPTS` 까지
         자동 재시도하고 그 뒤 DLQ 로 격리한다(`worker/loop.py`). 명세 에러표의
         `FEEDBACK_GENERATION_FAILED`(200, "점수 유지, 피드백만 재시도")는 **FE 처리**
         열이므로, 사용자가 다시 시도할 때 새 작업을 넣는 쪽은 서버다. 부분 유니크가
         `PENDING` 만 걸어 `FAILED` 행은 방해하지 않는다.

      4. **⚠️ 서버는 폴링과 재시도 버튼을 구분하지 못한다.** GET 하나뿐이라
         `FAILED` 를 볼 때마다 새로 넣으면 폴링이 AI 를 무한히 부른다. **FE 가
         `FAILED` 에서 폴링을 멈추고 사용자가 누를 때만 다시 부르기로 한다** — 분석
         화면(`meal_analysis_screen.dart`)이 이미 같은 규칙으로 돈다. 서버 쪽
         안전장치(실패 횟수 상한)는 필요해지면 그때 넣는다.
    """
    meal = _owned_meal(db, user_id, meal_id)

    # **확정 상태가 아니면 저장된 문장을 내보내지 않는다.**
    # 음식을 고치면 `crud/meal.py::mark_recalculating` 이 상태를 `ANALYZING` 으로
    # 되돌리는데 `meal_feedbacks` 행은 남는다. 그 문장은 고치기 전 끼니를 보고 쓴
    # 것이라 이미 사실이 아니다 — "단백질이 부족했어요" 가 단백질을 더한 뒤에도
    # 그대로 나간다. `POST /meals/{mealId}/confirm` 은 이때 `PENDING` 을 내므로,
    # 여기서 `READY` 를 내면 같은 끼니에 두 답이 된다.
    #
    # 🔗 점수(`qqs_evaluations`)는 같은 증상을 **행 삭제**로 막는다
    # (`crud/meal.py::mark_recalculating`). 여기서 같은 방법을 쓰지 않는 이유는
    # `daily_feedback_sources.meal_feedback_id` 가 `ON DELETE CASCADE` 라서다 —
    # 행을 지우면 일일 피드백의 출처 링크가 조용히 사라지고 문장만 남는다.
    # 점수 테이블에는 그런 자식이 없어 지워도 잃을 게 없었다.
    #
    # 🔗 명세상 `GET /meals/{mealId}` 상세도 같은 행을 싣는다(담당 박준혁, 미구현).
    # 그쪽도 `status: EVALUATED` 일 때만 `feedback` 을 내보내야 한다.
    if meal.status is not MealStatus.EVALUATED:
        return MealFeedbackResponse.pending()

    # `body is None` 은 **행이 있지만 내용이 무효**라는 뜻이다 — 재확정이
    # `invalidate_by_meal` 로 비운 상태다. 행 존재만 보고 `READY` 를 내면
    # `summary: null` 인 READY 가 나가서, FE 가 빈 카드를 그린다.
    row = feedback_crud.get_by_meal(db, meal.id)
    if row is None or row.body is None:
        return MealFeedbackResponse.pending()

    # 마스킹은 스키마 팩토리가 한다 — `GET /meals/{mealId}` 도 같은 행을 읽으므로
    # 조립부마다 쓰면 코드가 두 벌이 되고 한쪽만 고쳐진다.
    return MealFeedbackResponse.from_row(
        row,
        suggestions=_build_suggestions(db, row.suggestions),
        expected=_expected_satiety(db, meal.id),
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
        hunger_return_minutes=log.hunger_return_minutes if log else None,
    )
