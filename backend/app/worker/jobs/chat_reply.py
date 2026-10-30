"""`chat.reply` — 챗봇 답변 하나를 만든다 (A-2).

`/chat` → `chat_messages`(+ `chat_food_cards`). 이 작업은 A-2(`services/chat.py::send_message`)가
USER 메시지 · GENERATING 자리표시와 **같은 트랜잭션에서** 넣는다.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `ChatRequest` · `ChatResponse` 다.

**재시도하지 않는다.** FE 는 30초만 폴링한다 — 재시도(최대 3회 × 45초)는 FE 가 포기한 뒤
늦은 답만 붙인다. AI 가 실패하면 `on_ai_error` 가 바로 FAILED 로 두고 FE 가 "다시 보내기" 를
띄운다. 워커가 죽어 GENERATING 이 남으면 `crud/chat.py::CHAT_REPLY_STALE_SEC` 뒤 만료로 본다.
`load` · `apply` 가 DB 순단 등으로 터져 큐가 다시 집은 작업(`attempts >= 1`)도 AI 를 부르지 않고
FAILED 로 닫는다 — 큐의 재시도가 AI 재시도로 새지 않게.

순서 (load 1~3 · call_ai 4 · apply 5~6):
  1. 답변 자리표시를 읽는다. 없거나 GENERATING 이 아니면 건너뛴다
  2. 만료됐거나 재시도로 다시 집힌 작업이면 AI 를 부르지 않고 FAILED 로 끝낸다 — FE 가 포기한
     질문에 LLM 비용을 쓰지 않는다
  3. 질문 · 지난 대화 · 컨텍스트를 모은다
  4. AI 를 부른다
  5. 답변을 잠그고 다시 본다 — 그사이 만료 처리로 FAILED 가 됐으면 쓰지 않는다
  6. READY(SAFE · BLOCKED) 또는 FAILED 로 쓴다
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.orm import Session

from app.core.time import KST
from app.crud import chat as chat_crud
from app.crud import evaluation as evaluation_crud
from app.crud import feedback as feedback_crud
from app.crud import food as food_crud
from app.crud import insight as insight_crud
from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask
from app.models.chat import ChatConversation, ChatFoodCard, ChatMessage
from app.models.enums import (
    ChatMessageRole,
    ChatMessageStatus,
    MealStatus,
    MedicationStage,
    SafetyStatus,
)
from app.models.evaluation import QQSEvaluation
from app.services.chat import showable_suggestions
from app.worker.job import Job, Skip

logger = logging.getLogger("worker.chat_reply")

HISTORY_LIMIT: Final = 20
"""AI 에 싣는 지난 대화 상한. 오래된 것부터 버린다 — 토큰 비용이 대화 길이에 비례해 늘지 않게."""

FOOD_NAME_MAX: Final[int] = ChatFoodCard.food_name.type.length
"""카드 음식명 상한 — 컬럼(`String(255)`)에서 가져와 둘이 어긋나지 않게 한다. 넘는 이름을
INSERT 하면 DataError 로 `apply` 가 터지고 큐 재시도가 AI 를 또 부른다."""


@dataclass(frozen=True)
class _Ctx:
    """`load` 가 넘기는 값. ORM 객체를 싣지 않는다 — `load` 의 세션은 곧 닫힌다."""

    message_id: uuid.UUID
    fail_reason: str | None
    """있으면 AI 를 부르지 않고 FAILED 로 끝낸다 (`EXPIRED` · `RETRIED` · `NO_QUESTION`)."""
    request: dict[str, Any] | None


def _number(value: Decimal | int | None) -> float | None:
    # AI payload 는 JSON 으로 나간다 — Decimal 을 싣지 않는다. None 은 "모른다" 라 그대로 둔다.
    return None if value is None else float(value)


def _qqs(evaluation: QQSEvaluation | None) -> dict[str, float | None] | None:
    if evaluation is None:
        return None
    # 채점 못 한 축은 null 이다 — 0 으로 채우면 "못 쟀다" 가 "바닥이다" 가 된다.
    return {
        "quantity": _number(evaluation.quantity_score),
        "quality": _number(evaluation.quality_score),
        "satiety": _number(evaluation.satiety_score),
    }


def _history(prior: list[ChatMessage]) -> list[dict[str, str]]:
    """질문 이전의 메시지 → AI 에 줄 지난 대화.

    - ASSISTANT 는 READY + SAFE + 본문 있음만. BLOCKED · FAILED · GENERATING 은 뺀다.
    - USER 는 **바로 다음 답이 BLOCKED 면 뺀다.** 가드레일이 막은 질문을 다시 실으면
      AI 가 그 질문에 뒤늦게 답할 수 있다 (규칙 1). 답이 FAILED 인 질문은 남긴다 —
      사용자가 실제로 한 말이고, 보통 같은 질문을 다시 보낸다.
    """
    turns: list[dict[str, str]] = []
    for index, message in enumerate(prior):
        if message.role is ChatMessageRole.USER:
            following = prior[index + 1] if index + 1 < len(prior) else None
            if following is not None and following.safety_status is SafetyStatus.BLOCKED:
                continue
            if message.text:
                turns.append({"role": "USER", "text": message.text})
        elif (
            message.status is ChatMessageStatus.READY
            and message.safety_status is SafetyStatus.SAFE
            and message.text
        ):
            turns.append({"role": "ASSISTANT", "text": message.text})
    return turns[-HISTORY_LIMIT:]


def _base_meal(db: Session, conversation: ChatConversation) -> dict[str, Any] | None:
    """기준 끼니. 지워졌으면 None — `meal_id` 는 soft delete 를 모르므로 raw id 로 읽지 않는다
    (`models/chat.py::ChatConversation.meal_id`)."""
    if conversation.meal_id is None:
        return None
    meal = meal_crud.get_owned_meal(db, user_id=conversation.user_id, meal_id=conversation.meal_id)
    if meal is None:
        return None
    # 확정 전 끼니의 추천은 고치기 전 끼니를 보고 쓴 것이다 — A-1 과 같은 판단.
    suggestions = (
        showable_suggestions(db, meal.id) if meal.status is MealStatus.EVALUATED else []
    )
    return {
        "mealId": str(meal.id),
        "mealType": meal.meal_type.value,
        "qqs": _qqs(evaluation_crud.get_by_meal(db, meal.id)),
        "suggestions": [{"foodName": s.food_name, "advice": s.advice} for s in suggestions],
    }


def _context(db: Session, conversation: ChatConversation) -> dict[str, Any]:
    """명세 「컨텍스트 선주입」. AI 의 tool 호출 상한(3회)을 아끼려고 BE 가 미리 싣는다."""
    user_id = conversation.user_id
    medication = medication_crud.get_current(db, user_id)
    today = datetime.now(KST).date()
    long_term = insight_crud.get_latest_safe(db, user_id)
    return {
        "stage": (medication.stage if medication else MedicationStage.PRE_DOSE).value,
        "medication": None
        if medication is None
        else {"drugName": medication.drug_name, "doseMg": float(medication.dose_mg)},
        "today": {
            "date": today.isoformat(),
            "meals": [
                {
                    "mealType": meal.meal_type.value,
                    "foods": [item.display_name for item in meal.items],
                    "qqs": _qqs(evaluation_crud.get_by_meal(db, meal.id)),
                }
                for meal in chat_crud.list_evaluated_meals_on(db, user_id, today)
            ],
        },
        "baseMeal": _base_meal(db, conversation),
        "longTerm": None
        if long_term is None
        else {
            "periodType": long_term.period_type.value,
            "trendSummary": long_term.trend_summary,
            "recommendation": long_term.recommendation,
        },
    }


def load(db: Session, task: ClaimedTask) -> _Ctx | Skip:
    """답변 자리표시를 읽고 AI 요청을 만든다."""
    message_id = uuid.UUID(task.payload["messageId"])

    found = chat_crud.get_message_for_worker(db, message_id)
    if found is None or found[0].status is not ChatMessageStatus.GENERATING:
        logger.info("챗봇 답변 건너뜀 messageId=%s reason=NOT_GENERATING", message_id)
        return Skip({"skipped": "NOT_GENERATING"})
    reply, stale = found
    if stale:
        return _Ctx(message_id=reply.id, fail_reason="EXPIRED", request=None)
    # `attempts` 는 이번 시도 이전까지 집힌 횟수다(첫 시도 0, `queue.py::claim`). 1 이상이면 앞선
    # 시도가 AI 를 부른 뒤 `apply` 에서 터졌거나 워커가 죽은 것이다 — 다시 부르면 FE 가 포기한
    # 뒤 LLM 비용만 더 쓴다. 위의 GENERATING 확인 뒤라 이미 끝난 답은 그대로 건너뛴다.
    if task.attempts > 0:
        return _Ctx(message_id=reply.id, fail_reason="RETRIED", request=None)

    conversation = chat_crud.get_conversation_for_worker(db, reply.conversation_id)
    messages = chat_crud.list_messages(db, reply.conversation_id)
    position = next(i for i, m in enumerate(messages) if m.id == reply.id)
    question_at = next(
        (i for i in range(position - 1, -1, -1) if messages[i].role is ChatMessageRole.USER),
        None,
    )
    # A-2 는 질문을 자리표시보다 먼저 넣는다. 없으면 데이터가 깨진 것이다 — 재시도해도 같다.
    if conversation is None or question_at is None or not messages[question_at].text:
        return _Ctx(message_id=reply.id, fail_reason="NO_QUESTION", request=None)

    return _Ctx(
        message_id=reply.id,
        fail_reason=None,
        request={
            "userId": str(conversation.user_id),
            "conversationId": str(conversation.id),
            "text": messages[question_at].text,
            "history": _history(messages[:question_at]),
            "context": _context(db, conversation),
        },
    )


def call_ai(ctx: _Ctx, ai: AiClient) -> dict[str, Any] | None:
    if ctx.fail_reason is not None:
        return None
    return ai.chat(ctx.request)


def _safety(raw: object) -> SafetyStatus | None:
    try:
        return SafetyStatus(raw)
    except ValueError:
        return None


def _fail(db: Session, reply: ChatMessage, reason: str) -> dict[str, Any]:
    chat_crud.finish_message(db, reply, status=ChatMessageStatus.FAILED)
    logger.warning("챗봇 답변 실패 messageId=%s reason=%s", reply.id, reason)
    return {"messageId": str(reply.id), "status": "FAILED", "reason": reason}


def _lock_generating(db: Session, message_id: uuid.UUID) -> ChatMessage | None:
    """답변을 잠그고 다시 본다. 아직 GENERATING 일 때만 돌려준다.

    워커 루프에서는 새 세션이라 identity map 이 비어 있지만, `JOB.run_inline` 은 `load` 와
    같은 세션을 쓴다. 그래서 다시 읽기 전에 비운다.
    """
    db.expire_all()
    reply = chat_crud.lock_message_for_worker(db, message_id)
    if reply is None or reply.status is not ChatMessageStatus.GENERATING:
        logger.info("챗봇 답변 건너뜀 messageId=%s reason=STALE", message_id)
        return None
    return reply


def apply(
    db: Session, task: ClaimedTask, ctx: _Ctx, result: dict[str, Any] | None
) -> dict[str, Any]:
    """답변을 잠그고 다시 본 뒤 READY 또는 FAILED 로 쓴다."""
    # AI 를 기다리는 동안(최대 45초) 잠그지 않았다 — 그사이 사용자가 다시 보내 A-2 가
    # 만료 처리로 FAILED 를 썼을 수 있다. 그 답을 늦게 온 응답으로 되살리지 않는다.
    reply = _lock_generating(db, ctx.message_id)
    if reply is None:
        return {"skipped": "STALE"}
    if ctx.fail_reason is not None:
        return _fail(db, reply, ctx.fail_reason)

    safety = _safety(result.get("safetyStatus")) if isinstance(result, dict) else None
    if safety is None:
        return _fail(db, reply, "BAD_RESPONSE")

    if safety is not SafetyStatus.SAFE:
        # 챗봇에는 사람 검수 흐름이 없다 — REVIEW_REQUIRED 도 노출하지 않는다 (규칙 1).
        # 문장 · 카드는 버린다. 명세: BLOCKED 면 text null, foodCards [].
        chat_crud.finish_message(
            db, reply, status=ChatMessageStatus.READY, safety_status=SafetyStatus.BLOCKED
        )
        logger.info("챗봇 답변 완료 messageId=%s safetyStatus=BLOCKED", reply.id)
        return {
            "messageId": str(reply.id), "status": "READY", "safetyStatus": "BLOCKED", "cardCount": 0,
        }

    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        return _fail(db, reply, "EMPTY_TEXT")

    # 카드는 끼니 제안과 모양이 같다({foodName, advice, candidateFoodRefId}) — 같은 규칙으로
    # 거른다. 이름 · 조언이 빈 카드와 깨진 원소는 버린다. 거기에 챗봇이 더 거른다:
    # 공백뿐인 이름 · 조언(`parse_suggestion` 은 `" "` 를 통과시킨다)과 컬럼보다 긴 이름.
    # 긴 이름은 자르지 않고 버린다 — 잘린 이름은 다른 음식을 가리킬 수 있다.
    cards = [
        c
        for c in feedback_crud.parse_suggestions(result.get("foodCards"))
        if c.food_name.strip() and c.advice.strip() and len(c.food_name) <= FOOD_NAME_MAX
    ]
    # 공공 DB 에 없는 id 는 "이 음식" 이라는 거짓 링크라 버린다 — `feedback_meal._stored_suggestions` 와 같다.
    known = food_crud.existing_ids(db, {c.ref_id for c in cards if c.ref_id is not None})

    chat_crud.finish_message(
        db, reply, status=ChatMessageStatus.READY, text=text, safety_status=SafetyStatus.SAFE
    )
    for position, card in enumerate(cards):
        chat_crud.add_food_card(
            db,
            message_id=reply.id,
            position=position,
            food_name=card.food_name,
            advice=card.advice,
            food_ref_id=card.ref_id if card.ref_id in known else None,
        )

    # 커밋하지 않는다. 큐가 DONE 과 함께 한 번에 커밋한다(`queue.complete`).
    # 로그 · 반환값에는 식별자와 개수만 — 질문 · 답 · 음식명은 남기지 않는다 (규칙 6).
    logger.info("챗봇 답변 완료 messageId=%s safetyStatus=SAFE cards=%d", reply.id, len(cards))
    return {
        "messageId": str(reply.id), "status": "READY", "safetyStatus": "SAFE", "cardCount": len(cards),
    }


def on_ai_error(
    db: Session, task: ClaimedTask, ctx: _Ctx, exc: Exception
) -> dict[str, Any]:
    """AI 가 실패했다. **재시도하지 않고** 바로 FAILED 로 둔다 — FE 는 30초만 기다린다.

    예외 메시지에 AI 응답 일부가 섞일 수 있어 타입만 남긴다 (규칙 6).
    """
    reply = _lock_generating(db, ctx.message_id)
    if reply is None:
        return {"skipped": "STALE"}
    return _fail(db, reply, f"AI_ERROR:{type(exc).__name__}")


JOB = Job(load=load, call_ai=call_ai, apply=apply, on_ai_error=on_ai_error)
