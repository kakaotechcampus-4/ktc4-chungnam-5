"""`chat.reply` 워커 — 챗봇 답변 하나를 만든다.

틀리기 쉬운 것:
  - 막힌(BLOCKED) 답과 그 질문을 history 로 다시 실어 AI 에 되먹이는 것
  - 컨텍스트에 검수 안 된 문장(BLOCKED 추천 · BLOCKED 장기 피드백)을 싣는 것 (규칙 1)
  - 삭제된 기준 끼니를 raw id 로 읽어 컨텍스트에 싣는 것
  - FE 가 이미 포기한 만료 답변에 AI(=LLM 비용)를 부르는 것
  - AI 실패를 재시도해 FE timeout 뒤에 늦은 답을 붙이는 것
  - 사용자가 그새 다시 보내 FAILED 가 된 답을 늦게 온 AI 응답으로 덮는 것
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.time import KST
from app.crud import chat as chat_crud
from app.crud import medication as medication_crud
from app.infra.queue import ClaimedTask
from app.models.chat import ChatFoodCard, ChatMessage
from app.models.enums import (
    ChatMessageRole,
    ChatMessageStatus,
    FeedbackPeriodType,
    MealStatus,
    MedicationStage,
    SafetyStatus,
)
from app.models.feedback import LongTermFeedback
from app.services import chat as chat_service
from app.tests.factories import (
    make_food_ref,
    make_meal,
    make_meal_feedback,
    make_meal_item,
    make_qqs_evaluation,
    make_user,
)
from app.worker.dispatch import get_job
from app.worker.job import Skip
from app.worker.jobs import chat_reply

_TOFU = {"foodName": "두부 반 모", "advice": "단백질을 채워요", "candidateFoodRefId": None}


# ─────────────────────────── helper ───────────────────────────


def _task(message_id: uuid.UUID) -> ClaimedTask:
    return ClaimedTask(
        id=uuid.uuid4(), type="chat.reply", payload={"messageId": str(message_id)}, attempts=0
    )


def _open(db: Session, user_id: uuid.UUID, meal_id: uuid.UUID | None = None) -> uuid.UUID:
    return chat_service.start_conversation(db, user_id=user_id, meal_id=meal_id).conversation_id


def _send(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID, text: str) -> uuid.UUID:
    """메시지를 보내고 답변 자리표시 id 를 돌려준다."""
    return chat_service.send_message(
        db, user_id=user_id, conversation_id=conversation_id, text=text
    ).assistant_message_id


def _finish(db: Session, message_id: uuid.UUID, status: ChatMessageStatus, text=None, safety=None):
    chat_crud.finish_message(
        db, db.get(ChatMessage, message_id), status=status, text=text, safety_status=safety
    )


def _age(db: Session, message_id: uuid.UUID, seconds: int) -> None:
    db.execute(
        update(ChatMessage)
        .where(ChatMessage.id == message_id)
        .values(created_at=func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, seconds))
    )
    db.expire_all()


def _request(db: Session, message_id: uuid.UUID) -> dict[str, Any]:
    ctx = chat_reply.load(db, _task(message_id))
    assert not isinstance(ctx, Skip)
    assert ctx.fail_reason is None
    return ctx.request


def _long_term(db: Session, user_id, *, safety, summary, updated_at) -> LongTermFeedback:
    row = LongTermFeedback(
        user_id=user_id,
        period_type=FeedbackPeriodType.WEEKLY,
        period_start=updated_at.date() - timedelta(days=6),
        period_end=updated_at.date(),
        trend_summary=summary,
        recommendation="저녁에 단백질을 챙겨요",
        safety_status=safety,
        updated_at=updated_at,
    )
    db.add(row)
    db.flush()
    return row


# ─────────────────────────── load: 질문 · history ───────────────────────────


def test_load_builds_request_for_first_message_without_context(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    reply_id = _send(db, user.id, conversation_id, "다른 음식 추천해줘")

    request = _request(db, reply_id)

    assert request["userId"] == str(user.id)
    assert request["conversationId"] == str(conversation_id)
    assert request["text"] == "다른 음식 추천해줘"
    assert request["history"] == []
    assert request["context"] == {
        "stage": "PRE_DOSE",
        "medication": None,
        "today": {"date": datetime.now(KST).date().isoformat(), "meals": []},
        "baseMeal": None,
        "longTerm": None,
    }


def test_history_has_intro_and_previous_turns_in_order(db: Session):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    make_meal_feedback(db, user_id=user.id, meal_id=meal.id, suggestions=[_TOFU])
    conversation_id = _open(db, user.id, meal.id)
    first = _send(db, user.id, conversation_id, "첫 질문")
    _finish(db, first, ChatMessageStatus.READY, "첫 답", SafetyStatus.SAFE)
    second = _send(db, user.id, conversation_id, "둘째 질문")

    request = _request(db, second)

    assert request["text"] == "둘째 질문"
    assert request["history"] == [
        {"role": "ASSISTANT", "text": chat_service.INTRO_TEXT},
        {"role": "USER", "text": "첫 질문"},
        {"role": "ASSISTANT", "text": "첫 답"},
    ]


def test_history_drops_blocked_turns_and_failed_replies(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    blocked = _send(db, user.id, conversation_id, "약 용량 올려도 돼?")
    _finish(db, blocked, ChatMessageStatus.READY, None, SafetyStatus.BLOCKED)
    failed = _send(db, user.id, conversation_id, "저녁 뭐 먹지")
    _finish(db, failed, ChatMessageStatus.FAILED)
    third = _send(db, user.id, conversation_id, "저녁 뭐 먹지")

    request = _request(db, third)

    # 막힌 질문은 질문째 뺀다. 실패한 답만 빠지고 그 질문은 남는다(사용자가 실제로 한 말이다).
    assert request["history"] == [{"role": "USER", "text": "저녁 뭐 먹지"}]


def test_history_keeps_only_the_latest_turns(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    for i in range(15):
        reply = _send(db, user.id, conversation_id, f"질문 {i}")
        _finish(db, reply, ChatMessageStatus.READY, f"답 {i}", SafetyStatus.SAFE)
    last = _send(db, user.id, conversation_id, "마지막")

    history = _request(db, last)["history"]

    assert len(history) == chat_reply.HISTORY_LIMIT == 20
    assert history[0] == {"role": "USER", "text": "질문 5"}
    assert history[-1] == {"role": "ASSISTANT", "text": "답 14"}


# ─────────────────────────── load: 컨텍스트 ───────────────────────────


def test_context_has_medication_and_todays_evaluated_meals(db: Session):
    user = make_user(db)
    medication_crud.create(
        db,
        user_id=user.id,
        drug_name="위고비",
        dose_mg=Decimal("1.000"),
        injection_count=4,
        stage=MedicationStage.MAINTENANCE,
        effective_from=date(2026, 1, 1),
    )
    now = datetime.now(UTC)
    today_meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED, eaten_at=now)
    make_meal_item(db, meal_id=today_meal.id, display_name="참치김밥")
    make_qqs_evaluation(db, meal_id=today_meal.id, quantity_score=80, quality_score=None, satiety_score=70)
    make_meal(db, user_id=user.id, status=MealStatus.EVALUATED, eaten_at=now - timedelta(days=2))
    make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED, eaten_at=now)
    conversation_id = _open(db, user.id, today_meal.id)
    reply_id = _send(db, user.id, conversation_id, "오늘 어땠어?")

    context = _request(db, reply_id)["context"]

    assert context["stage"] == "MAINTENANCE"
    assert context["medication"] == {"drugName": "위고비", "doseMg": 1.0}
    assert context["today"]["meals"] == [
        {
            "mealType": "LUNCH",
            "foods": ["참치김밥"],
            "qqs": {"quantity": 80.0, "quality": None, "satiety": 70.0},
        }
    ]


def test_context_base_meal_carries_safe_suggestions(db: Session):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    make_meal_feedback(db, user_id=user.id, meal_id=meal.id, suggestions=[_TOFU])
    conversation_id = _open(db, user.id, meal.id)
    reply_id = _send(db, user.id, conversation_id, "두부 말고")

    base = _request(db, reply_id)["context"]["baseMeal"]

    assert base == {
        "mealId": str(meal.id),
        "mealType": "LUNCH",
        "qqs": None,
        "suggestions": [{"foodName": "두부 반 모", "advice": "단백질을 채워요"}],
    }


def test_context_base_meal_hides_blocked_suggestions(db: Session):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    make_meal_feedback(
        db, user_id=user.id, meal_id=meal.id, suggestions=[_TOFU], safety_status=SafetyStatus.BLOCKED
    )
    conversation_id = _open(db, user.id, meal.id)
    reply_id = _send(db, user.id, conversation_id, "추천해줘")

    assert _request(db, reply_id)["context"]["baseMeal"]["suggestions"] == []


def test_context_base_meal_is_null_when_deleted(db: Session):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    conversation_id = _open(db, user.id, meal.id)
    meal.deleted_at = datetime.now(UTC)
    db.flush()
    reply_id = _send(db, user.id, conversation_id, "추천해줘")

    assert _request(db, reply_id)["context"]["baseMeal"] is None


def test_context_long_term_is_latest_safe_one(db: Session):
    user = make_user(db)
    now = datetime.now(UTC)
    _long_term(db, user.id, safety=SafetyStatus.SAFE, summary="오래된 SAFE", updated_at=now - timedelta(days=7))
    _long_term(db, user.id, safety=SafetyStatus.BLOCKED, summary="최근 BLOCKED", updated_at=now)
    conversation_id = _open(db, user.id)
    reply_id = _send(db, user.id, conversation_id, "장기 피드백 보고싶어")

    assert _request(db, reply_id)["context"]["longTerm"] == {
        "periodType": "WEEKLY",
        "trendSummary": "오래된 SAFE",
        "recommendation": "저녁에 단백질을 챙겨요",
    }


# ─────────────────────────── load: 건너뛰기 · 만료 ───────────────────────────


def test_load_skips_reply_that_is_no_longer_generating(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    reply_id = _send(db, user.id, conversation_id, "질문")
    _finish(db, reply_id, ChatMessageStatus.FAILED)

    assert isinstance(chat_reply.load(db, _task(reply_id)), Skip)


def test_load_skips_unknown_message(db: Session):
    assert isinstance(chat_reply.load(db, _task(uuid.uuid4())), Skip)


def test_load_marks_expired_reply_and_call_ai_does_not_call(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    reply_id = _send(db, user.id, conversation_id, "질문")
    _age(db, reply_id, chat_crud.CHAT_REPLY_STALE_SEC + 1)

    ctx = chat_reply.load(db, _task(reply_id))

    assert ctx.fail_reason == "EXPIRED"
    assert ctx.request is None
    assert chat_reply.call_ai(ctx, ai=None) is None  # ai 를 건드리면 AttributeError


# ─────────────────────────── apply · on_ai_error ───────────────────────────


class FakeAi:
    """`/chat` 호출을 기록하고 정해진 응답을 돌려주는 가짜 AI (`ai-stub/schemas.py` ChatResponse 모양).

    `on_call` 은 AI 를 기다리는 사이에 일어난 일(만료 처리 등)을 흉내 낸다.
    """

    def __init__(self, *, response: dict[str, Any] | None = None, error: Exception | None = None, on_call=None):
        self.response = response if response is not None else {
            "text": "시금치 말고 이런 음식은 어떤가요?",
            "modelVersion": "stub-chat-0",
            "safetyStatus": "SAFE",
            "foodCards": [],
        }
        self.error = error
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return self.response


run = chat_reply.JOB.run_inline


def _pending_reply(db: Session) -> uuid.UUID:
    user = make_user(db)
    conversation_id = _open(db, user.id)
    return _send(db, user.id, conversation_id, "다른 음식 추천해줘")


def _cards(db: Session, message_id: uuid.UUID) -> list[ChatFoodCard]:
    return list(
        db.scalars(
            select(ChatFoodCard).where(ChatFoodCard.message_id == message_id).order_by(ChatFoodCard.position)
        )
    )


def test_safe_answer_is_saved_with_cards(db: Session):
    make_food_ref(db, food_ref_id="KFD_LETTUCE", name="상추")
    reply_id = _pending_reply(db)
    ai = FakeAi(response={
        "text": "시금치 말고 이런 음식은 어떤가요?",
        "modelVersion": "stub-chat-0",
        "safetyStatus": "SAFE",
        "foodCards": [
            {"foodName": "쌈채소 한 접시", "advice": "식이섬유를 채워요", "candidateFoodRefId": "KFD_LETTUCE"},
            {"foodName": "두부 반 모", "advice": "단백질을 채워요", "candidateFoodRefId": "NOT_IN_DB"},
            {"foodName": "", "advice": "이름 없는 카드"},
            "깨진 원소",
        ],
    })

    result = run(db, _task(reply_id), ai)

    reply = db.get(ChatMessage, reply_id)
    assert (reply.status, reply.safety_status, reply.text) == (
        ChatMessageStatus.READY, SafetyStatus.SAFE, "시금치 말고 이런 음식은 어떤가요?",
    )
    cards = _cards(db, reply_id)
    assert [(c.position, c.food_name, c.advice, c.food_ref_id) for c in cards] == [
        (0, "쌈채소 한 접시", "식이섬유를 채워요", "KFD_LETTUCE"),
        (1, "두부 반 모", "단백질을 채워요", None),  # 공공 DB 에 없는 id 는 거짓 링크라 버린다
    ]
    assert result == {
        "messageId": str(reply_id), "status": "READY", "safetyStatus": "SAFE", "cardCount": 2,
    }
    [call] = ai.calls
    assert call["text"] == "다른 음식 추천해줘"


@pytest.mark.parametrize("safety", ["BLOCKED", "REVIEW_REQUIRED"])
def test_unsafe_answer_is_saved_blocked_without_text_or_cards(db: Session, safety):
    reply_id = _pending_reply(db)
    ai = FakeAi(response={
        "text": "약물 관련 판단은 담당 의료진과 상의해 주세요.",
        "modelVersion": "stub-chat-0",
        "safetyStatus": safety,
        "foodCards": [{"foodName": "두부", "advice": "x", "candidateFoodRefId": None}],
    })

    run(db, _task(reply_id), ai)

    reply = db.get(ChatMessage, reply_id)
    assert (reply.status, reply.safety_status, reply.text) == (
        ChatMessageStatus.READY, SafetyStatus.BLOCKED, None,
    )
    assert _cards(db, reply_id) == []


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"text": "   ", "modelVersion": "m", "safetyStatus": "SAFE", "foodCards": []}, "EMPTY_TEXT"),
        ({"text": None, "modelVersion": "m", "safetyStatus": "SAFE"}, "EMPTY_TEXT"),
        ({"text": "답", "modelVersion": "m", "safetyStatus": "WHAT"}, "BAD_RESPONSE"),
    ],
    ids=["blank-text", "null-text", "unknown-safety"],
)
def test_unusable_answer_is_failed(db: Session, response, reason):
    reply_id = _pending_reply(db)

    result = run(db, _task(reply_id), FakeAi(response=response))

    reply = db.get(ChatMessage, reply_id)
    assert (reply.status, reply.text, reply.safety_status) == (ChatMessageStatus.FAILED, None, None)
    assert result == {"messageId": str(reply_id), "status": "FAILED", "reason": reason}


def _safe(cards: list[Any]) -> dict[str, Any]:
    return {"text": "답", "modelVersion": "m", "safetyStatus": "SAFE", "foodCards": cards}


def test_card_with_too_long_name_is_dropped_not_truncated(db: Session):
    """`food_name` 은 String(255) 다. 넘는 카드를 넣으면 DataError 로 apply 가 터지고, 큐가
    재시도하면 AI 를 또 부르며 답은 GENERATING 에 남는다. 자르면 다른 음식이 되므로 버린다."""
    reply_id = _pending_reply(db)
    limit = chat_reply.FOOD_NAME_MAX
    ai = FakeAi(response=_safe([
        {"foodName": "가" * limit, "advice": "경계값은 남긴다"},
        {"foodName": "나" * (limit + 1), "advice": "넘으면 버린다"},
        {"foodName": "두부 반 모", "advice": "단백질을 채워요"},
    ]))

    result = run(db, _task(reply_id), ai)

    assert db.get(ChatMessage, reply_id).status is ChatMessageStatus.READY
    assert [(c.position, c.food_name) for c in _cards(db, reply_id)] == [
        (0, "가" * limit), (1, "두부 반 모"),
    ]
    assert result["cardCount"] == 2
    assert limit == 255


def test_cards_with_blank_name_or_advice_are_dropped(db: Session):
    reply_id = _pending_reply(db)
    ai = FakeAi(response=_safe([
        {"foodName": "   ", "advice": "이름이 공백"},
        {"foodName": "조언 공백", "advice": " \t\n"},
        {"foodName": "두부 반 모", "advice": "단백질을 채워요"},
    ]))

    run(db, _task(reply_id), ai)

    assert db.get(ChatMessage, reply_id).status is ChatMessageStatus.READY
    assert [(c.position, c.food_name) for c in _cards(db, reply_id)] == [(0, "두부 반 모")]


def test_ai_error_fails_immediately_without_retry(db: Session):
    reply_id = _pending_reply(db)

    result = run(db, _task(reply_id), FakeAi(error=RuntimeError("AI 500")))  # raise 하지 않는다

    assert db.get(ChatMessage, reply_id).status is ChatMessageStatus.FAILED
    assert result["status"] == "FAILED"


def test_expired_reply_is_failed_without_calling_ai(db: Session):
    reply_id = _pending_reply(db)
    _age(db, reply_id, chat_crud.CHAT_REPLY_STALE_SEC + 1)
    ai = FakeAi()

    result = run(db, _task(reply_id), ai)

    assert ai.calls == []
    assert db.get(ChatMessage, reply_id).status is ChatMessageStatus.FAILED
    assert result == {"messageId": str(reply_id), "status": "FAILED", "reason": "EXPIRED"}


def test_retried_task_fails_without_calling_ai(db: Session):
    """load/apply 가 DB 순단 등으로 터져 큐가 다시 집은 작업이다. FE 는 30초 만에 포기했으므로
    AI(=LLM 비용)를 또 부르지 않고 FAILED 로 닫는다."""
    reply_id = _pending_reply(db)
    retry = ClaimedTask(
        id=uuid.uuid4(), type="chat.reply", payload={"messageId": str(reply_id)}, attempts=1
    )
    ai = FakeAi()

    result = run(db, retry, ai)

    assert ai.calls == []
    assert db.get(ChatMessage, reply_id).status is ChatMessageStatus.FAILED
    assert result == {"messageId": str(reply_id), "status": "FAILED", "reason": "RETRIED"}


def test_retried_task_still_skips_reply_that_is_not_generating(db: Session):
    reply_id = _pending_reply(db)
    _finish(db, reply_id, ChatMessageStatus.READY, "이미 답함", SafetyStatus.SAFE)
    retry = ClaimedTask(
        id=uuid.uuid4(), type="chat.reply", payload={"messageId": str(reply_id)}, attempts=1
    )

    assert isinstance(chat_reply.load(db, retry), Skip)


def test_late_answer_does_not_overwrite_reply_failed_meanwhile(db: Session):
    """AI 를 기다리는 사이 사용자가 다시 보내 A-2 가 만료 처리로 FAILED 를 썼다."""
    reply_id = _pending_reply(db)
    ai = FakeAi(on_call=lambda: _finish(db, reply_id, ChatMessageStatus.FAILED))

    result = run(db, _task(reply_id), ai)

    assert db.get(ChatMessage, reply_id).status is ChatMessageStatus.FAILED
    assert _cards(db, reply_id) == []
    assert result == {"skipped": "STALE"}


def test_ai_error_does_not_touch_reply_failed_meanwhile(db: Session):
    reply_id = _pending_reply(db)
    ai = FakeAi(
        error=RuntimeError("AI 500"),
        on_call=lambda: _finish(db, reply_id, ChatMessageStatus.READY, "다른 답", SafetyStatus.SAFE),
    )

    result = run(db, _task(reply_id), ai)

    assert db.get(ChatMessage, reply_id).text == "다른 답"
    assert result == {"skipped": "STALE"}


def test_ai_error_through_worker_loop_ends_done_with_failed_reply_and_no_retry(sessions):
    """`process_one` 까지 이어서 본다 — AI 예외가 `queue.fail`(재시도)로 새지 않고 DONE 으로
    끝나야 한다. 진짜 큐 · 커밋하는 세션이라 사용자는 테스트가 직접 지운다."""
    from app.infra.queue import DbTaskQueue
    from app.models.enums import TaskStatus
    from app.models.task import Task
    from app.models.user import User
    from app.worker.loop import process_one

    with sessions() as setup:
        user = make_user(setup)
        setup.commit()
        user_id = user.id
        conversation_id = _open(setup, user_id)
        reply_id = _send(setup, user_id, conversation_id, "다른 음식 추천해줘")
    try:
        queue = DbTaskQueue(sessions)
        lease = queue.claim()
        assert lease is not None and lease.task.payload == {"messageId": str(reply_id)}
        ai = FakeAi(error=RuntimeError("AI 500"))

        process_one(queue, ai, lease)  # raise 하면 워커 루프가 queue.fail 로 넘긴다

        assert len(ai.calls) == 1
        with sessions() as check:
            task = check.get(Task, lease.task.id)
            assert (task.status, task.attempts, task.last_error) == (TaskStatus.DONE, 1, None)
            assert task.result["reason"] == "AI_ERROR:RuntimeError"
            assert check.get(ChatMessage, reply_id).status is ChatMessageStatus.FAILED
    finally:
        with sessions() as cleanup:
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()


def test_dispatch_knows_chat_reply():
    assert get_job("chat.reply") is chat_reply.JOB
