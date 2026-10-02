"""`meal.analyze` — 사진·텍스트에서 음식을 인식해 `meal_items` 에 저장한다.

`ANALYZING → REVIEW_REQUIRED`. 사용자가 인식 결과를 확인하면 거기서 채점으로 이어진다.
음식을 하나도 못 알아봤거나 AI 가 끝내 실패하면 `FAILED` 다.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `AnalyzeMealRequest` · `AnalyzeMealResponse` 다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.crud import food as food_crud
from app.crud import meal as meal_crud
from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask, NonRetryableError, QueueSettings
from app.infra.storage import build_file_storage
from app.models.enums import MealStatus
from app.models.meal import Meal
from app.services.meal import to_grams

logger = logging.getLogger("worker.analyze_meal")

# `meal_items.estimated_amount` 가 Numeric(8, 2) 다. 넘는 값을 넣으면 flush 에서
# 터져 트랜잭션이 깨진다 — 그러면 FAILED 로 바꿀 기회도 같이 사라진다.
_MAX_AMOUNT = Decimal("999999.99")
# 같은 이유로 `meal_items` 의 문자열 컬럼 길이를 넘는 값도 미리 걸러야 한다.
_MAX_NAME_LEN = 255  # original_food_name · display_name
_MAX_UNIT_LEN = 32  # estimated_unit


@dataclass(frozen=True)
class _Recognized:
    """검증을 마친 인식 항목 1건. DB 에 쓰기 전에 응답 전체를 이 모양으로 옮겨 둔다."""

    name: str
    amount: Decimal
    unit: str
    confidence: Decimal
    food_ref_id: str | None
    raw: dict[str, Any]


def run(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None:
    """
    실패한 시도의 도메인 변경은 큐 트랜잭션과 함께 롤백되므로, 재시도할 때 이전
    시도의 흔적은 없다. 그래도 `meals.status` 가 `ANALYZING` 이 아니면 넘어간다 —
    같은 작업이 두 번 등록되는 경우(사용자 더블 탭 등)는 큐 구현과 무관하게 남는다.

    순서:
      1. `meals` 를 읽는다. 행이 없으면 raise — 재시도해도 생기지 않지만, 조용히
         DONE 으로 끝내면 원인을 추적할 수 없다
      2. 건너뛴다(None): 지운 식사(soft delete 라 행은 남아 있다) · `ANALYZING` 이
         아닌 식사 · `is_recalculation=True` 인 식사. 마지막은 사용자가 음식을 고친
         뒤 재계산 대기다 — 여기서 분석하면 사용자가 고친 항목을 AI 결과로 덮는다
      3. AI 호출 + 응답 검증. **DB 쓰기보다 먼저 전부 끝낸다** — 그래야 실패했을 때
         반쯤 쓴 흔적 없이 `FAILED` 로 바꿀 수 있다
      4. 실패하면 마지막 시도 전에는 raise(큐가 재시도), 마지막 시도거나 재시도해도
         같은 실패(AI 4xx)면 `FAILED` 로 두고 정상 반환한다. raise 로 끝내면 큐는
         작업을 격리하지만 식사는 영원히 `ANALYZING` 이고, FE 는 45초 뒤 타임아웃만 본다
      5. 항목이 없으면 `FAILED` — 확인할 음식이 없다(`services/meal.py::_is_editable`).
         `safetyStatus` 는 상태 판단에 쓰지 않는다. `raw_ai_result` 에만 남긴다
      6. `candidateFoodRefId` 는 `food_refs` 에 실재하는 것만 남긴다. 없는 FK 를
         넣으면 커밋이 통째로 깨진다
      7. 기존 `source=MODEL` 항목을 지우고 새로 넣는다. `source=USER` 는 남긴다.
         최초 분석 중에는 사용자가 음식을 고칠 수 없어(`_is_editable`) USER 항목과
         중복될 일이 없으므로 중복 판정은 하지 않는다
      8. 양은 AI 가 말한 그대로 `estimated_amount` · `estimated_unit` 에, g 환산은
         `to_grams` 로 `estimated_amount_g` 에. 환산이 안 되면("2개") g 만 None 이다
      9. `REVIEW_REQUIRED` 로 옮긴다. `is_recalculation` 은 건드리지 않는다 — 2 에서
         걸렀으므로 여기 오는 식사는 최초 분석(False)뿐이다

    **쓰기 직전에 식사를 잠그고 다시 읽는다** — AI 를 부르는 동안 사용자가 지웠을 수
    있다. 쓰기(6~8)는 SAVEPOINT 안에서 한다. 3 의 검증을 통과했는데도 DB 가 거부하는
    값이 있으면(모르는 제약) 트랜잭션이 aborted 가 돼 4 처럼 FAILED 로 바꿀 수 없게
    되는데, SAVEPOINT 만 되돌리면 같은 세션으로 FAILED 를 쓸 수 있다.

    🔗 TODO(범위 밖):
      - `clarifyQuestion` 은 저장할 컬럼이 없어 `raw_ai_result` 에만 남는다.
        `GET /meals/{mealId}` 는 아직 None 을 내보낸다(`services/meal.py`)
      - `safetyStatus=BLOCKED` 의 `medical_handoff_logs` 기록. 응답에 필수값인
        `trigger_type` 을 채울 사유가 없다
    """
    body = task.payload
    meal_id = body["mealId"]

    meal = meal_crud.get_meal_for_worker(db, uuid.UUID(meal_id))
    if meal is None:
        raise ValueError(f"식사를 찾을 수 없다: {meal_id}")
    if _should_skip(meal):
        return None

    try:
        response = ai.analyze_meal(_request(body))
        model_version = response["modelVersion"]
        safety_status = response["safetyStatus"]
        recognized = [
            _parse_item(raw, model_version=model_version, safety_status=safety_status)
            for raw in response["items"]
        ]
    except Exception as exc:
        return _fail_or_raise(db, meal, task, exc)

    meal = meal_crud.lock_meal_for_worker(db, meal.id)
    if meal is None or _should_skip(meal):
        return None

    if not recognized:
        return _finish(db, meal, MealStatus.FAILED, item_count=0)

    try:
        with db.begin_nested():
            _replace_model_items(db, meal, recognized)
    except SQLAlchemyError as exc:
        return _fail_or_raise(db, meal, task, exc)

    # 커밋하지 않는다. 이 세션은 큐가 작업을 잠근 트랜잭션이고, 큐가 DONE 과 함께
    # 한 번에 커밋한다. 여기서 터지면 도메인 변경까지 통째로 롤백된다.

    # 포즈 정보는 민감 건강정보다. 음식명·이미지 키를 로그에 남기지 않는다(규칙 6).
    logger.info(
        "분석 완료 mealId=%s items=%d safetyStatus=%s",
        meal_id,
        len(recognized),
        safety_status,
    )
    return _finish(db, meal, MealStatus.REVIEW_REQUIRED, item_count=len(recognized))


def _should_skip(meal: Meal) -> bool:
    """지운 식사 · 분석 중이 아닌 식사 · 사용자가 고친 뒤 재계산 대기인 식사."""
    return (
        meal.deleted_at is not None
        or meal.status is not MealStatus.ANALYZING
        or meal.is_recalculation
    )


def _fail_or_raise(db: Session, meal: Meal, task: ClaimedTask, exc: Exception) -> dict[str, Any]:
    """마지막 시도 전이면 다시 올려 큐의 재시도에 맡기고, 마지막이면 FAILED 로 끝낸다.

    재시도해도 같은 실패(`NonRetryableError`, 예: AI 4xx)는 마지막 시도로 본다 — 올리면
    큐가 바로 격리하는데 식사는 `ANALYZING` 에 남는다.
    """
    if not _is_last_attempt(task) and not isinstance(exc, NonRetryableError):
        raise exc
    # 예외 메시지에 AI 응답 일부가 섞일 수 있어 타입만 남긴다(규칙 6).
    logger.warning(
        "분석 최종 실패 mealId=%s attempts=%d error=%s",
        meal.id,
        task.attempts + 1,
        type(exc).__name__,
    )
    return _finish(db, meal, MealStatus.FAILED, item_count=0)


def _replace_model_items(db: Session, meal: Meal, recognized: list[_Recognized]) -> None:
    known_ids = food_crud.existing_ids(
        db, {item.food_ref_id for item in recognized if item.food_ref_id is not None}
    )
    meal_crud.delete_model_items(db, meal.id)
    for item in recognized:
        meal_crud.add_model_item(
            db,
            meal_id=meal.id,
            original_food_name=item.name,
            amount=item.amount,
            unit=item.unit,
            amount_g=to_grams(item.amount, item.unit),
            confidence=item.confidence,
            food_ref_id=item.food_ref_id if item.food_ref_id in known_ids else None,
            raw_ai_result=item.raw,
        )


def _request(body: dict[str, Any]) -> dict[str, Any]:
    """`AnalyzeMealRequest`. presigned URL 은 여기서(호출 직전에) 새로 발급한다.

    payload 엔 imageKey 만 있다(PR #42 리뷰 반영) — 큐에 넣는 시점에 미리 만든 URL 은
    이 작업이 실제로 집힐 때(적체·재시도 backoff)면 이미 만료됐을 수 있다.
    """
    image_key = body.get("imageKey")
    return {
        "mealId": body["mealId"],
        "mealType": body["mealType"],
        "eatenAt": body["eatenAt"],
        "stage": body["stage"],
        "imageUrl": build_file_storage().url(image_key) if image_key else None,
        "rawText": body.get("rawText"),
    }


def _parse_item(raw: dict[str, Any], *, model_version: str, safety_status: str) -> _Recognized:
    """`RecognizedItem` 한 건을 검증해 옮긴다. 계약과 다르면 raise — AI 실패로 다룬다."""
    name = _text(raw["originalFoodName"], field="originalFoodName", max_len=_MAX_NAME_LEN)
    unit = _text(raw["unit"], field="unit", max_len=_MAX_UNIT_LEN)

    food_ref_id = raw.get("candidateFoodRefId")
    if food_ref_id is not None and not isinstance(food_ref_id, str):
        # 숫자로 오면 `varchar = integer` 비교로 쿼리가 깨진다.
        raise ValueError("candidateFoodRefId 가 문자열이 아니다")

    amount = Decimal(str(raw["estimatedAmount"])).quantize(Decimal("0.01"))
    if not Decimal(0) <= amount <= _MAX_AMOUNT:
        raise ValueError("estimatedAmount 가 범위를 벗어났다")

    # `meal_items.confidence` 는 Numeric(4, 3) + CHECK 0~1 이다.
    confidence = Decimal(str(raw["confidence"])).quantize(Decimal("0.001"))
    if not Decimal(0) <= confidence <= Decimal(1):
        raise ValueError("confidence 가 0~1 을 벗어났다")

    return _Recognized(
        name=name,
        amount=amount,
        unit=unit,
        confidence=confidence,
        food_ref_id=food_ref_id,
        # AI 가 원래 뭐라고 했는지의 보관 자리다. 여기서 양을 다시 읽지 않는다.
        raw={"modelVersion": model_version, "safetyStatus": safety_status, "item": raw},
    )


def _text(value: Any, *, field: str, max_len: int) -> str:
    """앞뒤 공백을 떼고 컬럼에 들어갈 수 있는지 본다. NUL 은 Postgres 가 거부한다."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 가 비었다")
    value = value.strip()
    if len(value) > max_len or "\x00" in value:
        raise ValueError(f"{field} 를 저장할 수 없다")
    return value


def _is_last_attempt(task: ClaimedTask) -> bool:
    """이번에 실패하면 큐가 격리하는가. `infra/queue.py::_record_failure` 와 같은 식이다.

    `task.attempts` 는 **이전까지** 실패한 횟수다 — 실패를 기록할 때 1 을 더한다.
    """
    return task.attempts + 1 >= QueueSettings().QUEUE_MAX_ATTEMPTS


def _finish(db: Session, meal: Meal, status: MealStatus, *, item_count: int) -> dict[str, Any]:
    meal_crud.set_status(db, meal, status)
    # task_queue.result 에는 식별자·상태·개수만 — 음식명은 남기지 않는다(규칙 6).
    return {"mealId": str(meal.id), "status": status.value, "itemCount": item_count}
