"""챗봇 대화 · 메시지 · 음식 카드 접근.

`chat_conversations` 는 부분 UNIQUE `(user_id) WHERE closed_at IS NULL` 이라 새 대화를
열기 전에 **먼저 닫아야** 한다 (`close_open_conversation`). add/flush 까지만 하고
커밋하지 않는다 (절대 규칙 5).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Final

from sqlalchemy import ColumnElement, Date, cast, func, select, update
from sqlalchemy.orm import Session

from app.models.chat import ChatConversation, ChatFoodCard, ChatMessage
from app.models.enums import ChatMessageRole, ChatMessageStatus, MealStatus, SafetyStatus
from app.models.meal import Meal
from app.models.user import User


def lock_user(db: Session, user_id: uuid.UUID) -> bool:
    """같은 사용자의 "새 대화 시작" 을 줄 세운다. 트랜잭션이 끝날 때 풀린다.
    사용자 행이 있으면 True, 없으면 False (존재 확인이 잠금 쿼리에 얹힌다).

    잠그지 않으면 더블탭 두 요청이 둘 다 "열린 대화 없음" 을 보고 INSERT 해 둘째가
    부분 UNIQUE 에 걸려 500 이 난다. 잠그면 둘째가 첫째의 커밋을 기다렸다가 그 대화를
    닫고 연다 — 둘 다 201, 마지막 것만 열린다.

    **`users` 를 읽지만 여기 둔다.** 잠금의 목적이 챗봇 불변식이라서다
    (`crud/feedback.py::nutrients_by_food_ref` 가 `food_refs` 를 읽는 것과 같은 판단).

    **`FOR UPDATE` 가 아니라 `FOR NO KEY UPDATE`(`key_share=True`) 다.** `feedback.meal` 워커는
    `meals` 를 잠근 뒤 `meal_feedbacks` 를 upsert 하는데, 그 `user_id` FK 검사가 `users` 에
    `FOR KEY SHARE` 를 건다. 여기서 `users` 를 `FOR UPDATE` 로 쥔 채 `chat_conversations`
    (`meal_id` FK → `meals` KEY SHARE)를 넣으면 두 쪽의 잠금 순서가 맞물려 교착한다.
    `NO KEY UPDATE` 는 KEY SHARE 와 충돌하지 않으면서 자기끼리는 충돌하므로, 같은 사용자의
    A-1 끼리만 줄 세우고 그 사용자의 다른 쓰기(FK 검사)는 막지 않는다.
    """
    row = db.execute(
        select(User.id).where(User.id == user_id).with_for_update(key_share=True)
    ).scalar_one_or_none()
    return row is not None


def latest_evaluated_meal_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    """`mealId` 없이 새 대화를 열 때의 기준 끼니 — 가장 최근에 먹은 **확정된** 살아있는 끼니.

    확정 전 끼니는 추천이 없거나 낡았고, 삭제된 끼니는 사용자에게 없는 끼니다.
    `ix_meals_user_id_eaten_at`(부분 인덱스, `deleted_at IS NULL`)를 탄다.
    """
    stmt = (
        select(Meal.id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.status == MealStatus.EVALUATED,
        )
        .order_by(Meal.eaten_at.desc(), Meal.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def close_open_conversation(db: Session, user_id: uuid.UUID) -> None:
    """열린 대화를 닫는다. 없으면 0 행 UPDATE 다."""
    db.execute(
        update(ChatConversation)
        .where(ChatConversation.user_id == user_id, ChatConversation.closed_at.is_(None))
        .values(closed_at=func.now())
    )


def create_conversation(
    db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID | None
) -> ChatConversation:
    conversation = ChatConversation(user_id=user_id, meal_id=meal_id)
    db.add(conversation)
    db.flush()
    return conversation


def add_message(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    role: ChatMessageRole,
    status: ChatMessageStatus,
    text: str | None,
    safety_status: SafetyStatus | None,
) -> ChatMessage:
    """메시지 하나를 넣는다. 메시지마다 flush 한다 — `created_at` 이 INSERT 시각
    (`clock_timestamp()`)이라 넣은 순서가 곧 정렬 순서가 되게 하려면 한 번에 몰아 넣지 않는다."""
    message = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        status=status,
        text=text,
        safety_status=safety_status,
    )
    db.add(message)
    db.flush()
    return message


def add_food_card(
    db: Session,
    *,
    message_id: uuid.UUID,
    position: int,
    food_name: str,
    advice: str,
    food_ref_id: str | None,
) -> ChatFoodCard:
    card = ChatFoodCard(
        message_id=message_id,
        position=position,
        food_name=food_name,
        advice=advice,
        food_ref_id=food_ref_id,
    )
    db.add(card)
    db.flush()
    return card


def get_owned_conversation(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> ChatConversation | None:
    """user_id 소유의 대화. 없음 · 남의 것은 None. **닫힌 대화도 돌려준다** — 조회는
    읽기뿐이고, 워커가 닫힌 대화의 마지막 질문에도 답을 쓰므로 폴링 중이던 FE 가 받아야 한다."""
    stmt = select(ChatConversation).where(
        ChatConversation.id == conversation_id, ChatConversation.user_id == user_id
    )
    return db.execute(stmt).scalar_one_or_none()


CHAT_REPLY_STALE_SEC: Final = 120
"""GENERATING 답변을 **만료** 로 보는 나이(초). lease(`QUEUE_LEASE_SEC`)와 같은 값이다.

워커는 AI 실패를 재시도하지 않고 바로 FAILED 로 두지만(`worker/jobs/chat_reply.py`),
워커 프로세스가 죽으면 아무도 상태를 바꾸지 않는다. 그대로 두면 409 때문에 그 대화에
다시는 못 보낸다. FE 는 30초만 기다리므로 120초 지난 답은 아무도 기다리지 않는다."""


def _stale() -> ColumnElement[bool]:
    # `now()` 가 아니라 `clock_timestamp()` 다 — `created_at` 이 `clock_timestamp()` 로 찍히고,
    # `now()` 는 트랜잭션 시작 시각이라 같은 트랜잭션에서 넣은 행과 비교가 어긋난다.
    return ChatMessage.created_at < func.clock_timestamp() - func.make_interval(
        0, 0, 0, 0, 0, 0, CHAT_REPLY_STALE_SEC
    )


def lock_open_conversation(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> ChatConversation | None:
    """user_id 소유의 **열린** 대화를 잠그고 가져온다. 없음 · 남의 것 · 닫힘은 None.

    같은 대화로의 연속 전송(더블탭)을 줄 세운다. 잠그지 않으면 두 요청이 둘 다
    "GENERATING 없음" 을 보고 답변 자리표시를 둘 만든다.

    **`FOR NO KEY UPDATE`(`key_share=True`) 다.** 메시지 INSERT 의 FK 검사는 이 행에
    KEY SHARE 를 거는데 그것과 충돌하지 않는다. A-1 의 닫기(`closed_at` UPDATE)와는
    충돌하므로 두 요청이 줄을 서고, 닫힌 뒤 깨어나면 Postgres 가 WHERE 를 다시 평가해
    None 이 된다 — 닫힌 대화에 메시지가 붙지 않는다.
    """
    stmt = (
        select(ChatConversation)
        .where(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == user_id,
            ChatConversation.closed_at.is_(None),
        )
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return db.execute(stmt).scalar_one_or_none()


def find_generating_reply(
    db: Session, conversation_id: uuid.UUID
) -> tuple[ChatMessage, bool] | None:
    """대화에서 아직 생성 중인 답변과 그 만료 여부. 없으면 None. 찾은 답변은 **잠근다.**

    잠그지 않으면 A-2 의 만료 처리(`send_message` 가 FAILED 로 UPDATE)가 그 사이 워커가 커밋한
    READY 를 덮어쓴다 — 읽은 GENERATING 은 낡은 값이다. 잠그면 워커의 커밋을 기다렸다가
    Postgres 가 `status = GENERATING` 을 다시 평가해, 방금 READY 가 된 답은 빠지고(None)
    새 메시지가 정상으로 붙는다.

    **교착하지 않는다.** A-2 는 대화 잠금(`lock_open_conversation`) → 메시지 잠금 순이고, 워커는
    메시지 잠금(`lock_message_for_worker`)만 잡는다. 순서가 뒤집히는 쪽이 없다.
    `FOR NO KEY UPDATE` 라 카드 INSERT 의 FK 검사(KEY SHARE)와도 충돌하지 않는다.
    """
    row = db.execute(
        select(ChatMessage, _stale())
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.role == ChatMessageRole.ASSISTANT,
            ChatMessage.status == ChatMessageStatus.GENERATING,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).first()
    return None if row is None else (row[0], bool(row[1]))


def finish_message(
    db: Session,
    message: ChatMessage,
    *,
    status: ChatMessageStatus,
    text: str | None = None,
    safety_status: SafetyStatus | None = None,
) -> None:
    """답변의 최종 상태를 쓴다. FAILED 면 `text` · `safety_status` 를 비운다(기본값)."""
    message.status = status
    message.text = text
    message.safety_status = safety_status
    db.flush()


def get_message_for_worker(
    db: Session, message_id: uuid.UUID
) -> tuple[ChatMessage, bool] | None:
    """워커 전용 — 메시지와 그 만료 여부(`CHAT_REPLY_STALE_SEC`). 없으면 None.

    워커에는 "요청한 사용자" 가 없어 소유권을 보지 않는다. API 경로에서는 쓰지 말 것.
    """
    row = db.execute(select(ChatMessage, _stale()).where(ChatMessage.id == message_id)).first()
    return None if row is None else (row[0], bool(row[1]))


def lock_message_for_worker(db: Session, message_id: uuid.UUID) -> ChatMessage | None:
    """워커 전용 — 답변을 잠그고 다시 읽는다. AI 를 기다리는 사이 A-2 가 만료 처리로
    FAILED 를 썼을 수 있어, 쓰기 직전에 잠근 값으로 다시 판단한다.

    `FOR NO KEY UPDATE` 다 — 카드 INSERT 의 FK 검사(KEY SHARE)와 충돌하지 않는다.
    """
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.id == message_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_conversation_for_worker(
    db: Session, conversation_id: uuid.UUID
) -> ChatConversation | None:
    """워커 전용 — 소유자도 닫힘 여부도 보지 않는다. 닫힌 대화의 마지막 질문에도 답은 쓴다."""
    return db.get(ChatConversation, conversation_id)


def list_messages(db: Session, conversation_id: uuid.UUID) -> list[ChatMessage]:
    """대화의 메시지 전부, 오래된 순. `id` 는 `created_at` 이 같을 때의 결정적 순서다."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    )
    return list(db.execute(stmt).scalars())


def list_evaluated_meals_on(db: Session, user_id: uuid.UUID, day: date) -> list[Meal]:
    """그 날(KST) 먹은 확정된 살아있는 끼니, 먹은 순.

    확정 전 끼니는 점수가 없거나 낡았다. KST 로 자르는 이유는 `crud/meal.py::kst_day` 와 같다
    — UTC 로 자르면 밤 11시 야식이 다음날이 된다.
    """
    stmt = (
        select(Meal)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.status == MealStatus.EVALUATED,
            cast(func.timezone("Asia/Seoul", Meal.eaten_at), Date) == day,
        )
        .order_by(Meal.eaten_at, Meal.created_at)
    )
    return list(db.execute(stmt).scalars())


def list_food_cards(db: Session, message_ids: list[uuid.UUID]) -> list[ChatFoodCard]:
    """여러 메시지의 카드를 한 번에, 메시지별 `position` 순. 메시지마다 읽으면 N+1 이다.
    `uq_chat_food_cards_message_id_position` 의 인덱스를 탄다."""
    if not message_ids:
        return []
    stmt = (
        select(ChatFoodCard)
        .where(ChatFoodCard.message_id.in_(message_ids))
        .order_by(ChatFoodCard.message_id, ChatFoodCard.position)
    )
    return list(db.execute(stmt).scalars())


def get_open_conversation(db: Session, user_id: uuid.UUID) -> ChatConversation | None:
    """user_id 의 열린 대화. 없으면 None. 부분 UNIQUE `uq_chat_conversations_open` 이라
    많아야 하나이고, 그 인덱스를 탄다."""
    stmt = select(ChatConversation).where(
        ChatConversation.user_id == user_id, ChatConversation.closed_at.is_(None)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_card_for_meal(
    db: Session, *, card_id: uuid.UUID, user_id: uuid.UUID, meal_id: uuid.UUID
) -> tuple[ChatFoodCard, datetime] | None:
    """A-5 "이걸로 바꾸기" 가 쓸 카드와, 그 카드가 만들어진 시각(메시지 `created_at`).

    카드 → 메시지 → 대화를 한 번에 JOIN 해 **대화의 주인과 기준 끼니가 둘 다 맞을 때만**
    돌려준다. 없는 카드 · 남의 카드 · 다른 끼니 대화의 카드를 구분하지 않고 모두 `None` 이다
    — `crud/meal.py::get_owned_meal` 처럼 "남의 카드 id 는 존재한다" 는 정보를 흘리지 않는다.

    카드가 달린 메시지가 `READY` · `SAFE` 가 아니면(생성 중 · 실패 · 가드레일 차단 · 검수 전)
    없는 카드로 본다.

    닫힌 대화(`closed_at`)의 카드도 돌려준다. 열린 대화도 사용자가 닫기 전까지 계속 열려
    있어 "닫힘" 은 카드가 낡았는지를 말해 주지 않는다 — 낡음은 호출부가 메시지 시각과
    끼니의 마지막 확정 시각으로 판정한다.
    """
    stmt = (
        select(ChatFoodCard, ChatMessage.created_at)
        .join(ChatMessage, ChatMessage.id == ChatFoodCard.message_id)
        .join(ChatConversation, ChatConversation.id == ChatMessage.conversation_id)
        .where(
            ChatFoodCard.id == card_id,
            ChatConversation.user_id == user_id,
            ChatConversation.meal_id == meal_id,
            # 완성됐고 가드레일을 통과한 메시지의 카드만 — 아니면 검수 안 된 문구가 SAFE
            # 피드백의 추천으로 옮겨진다 (절대 규칙 1).
            ChatMessage.status == ChatMessageStatus.READY,
            ChatMessage.safety_status == SafetyStatus.SAFE,
        )
    )
    row = db.execute(stmt).one_or_none()
    return None if row is None else (row[0], row[1])
