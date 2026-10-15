"""챗봇(AI 코치) 대화 · 메시지 · 추천 음식 카드 (`contracts/ChatbotAPI.md`).

사용자당 **열린 대화는 하나**다 — "＋ 새 대화" 가 이전 대화를 닫고 새로 연다
(`closed_at`). 닫힌 대화는 지우지 않는다.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ChatMessageRole, ChatMessageStatus, SafetyStatus, pg_enum
from app.models.mixins import created_at, uuid_pk


class ChatConversation(Base):
    """대화 하나. 기준 끼니(`meal_id`)를 붙들고 있다 — A-2 컨텍스트 주입이 이걸 본다."""

    __tablename__ = "chat_conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meals.id", ondelete="SET NULL"), nullable=True
    )
    """기준 끼니. 평가한 끼니가 하나도 없으면 NULL.

    `SET NULL` 인 이유: 끼니는 soft delete 라 보통 행이 남지만, 사용자 탈퇴 등으로
    hard delete 되더라도 대화 기록까지 지울 이유는 없다.

    SET NULL 은 hard delete 에서만 일어난다. 끼니는 soft delete 라 **삭제된 끼니를 가리킬
    수 있다** — 읽는 쪽(A-2 컨텍스트 주입 등)은 raw id 로 읽지 말고
    `crud/meal.py::get_owned_meal` 로 불러야 한다."""

    created_at: Mapped[datetime] = created_at()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """NULL 이면 열린 대화. 새 대화를 열 때 `crud/chat.py::close_open_conversation` 이 채운다."""

    __mapper_args__ = {"eager_defaults": True}

    __table_args__ = (
        # "진행 중인 대화" 가 둘이면 A-4 가 무엇을 이어 볼지 모호해진다.
        # 서비스가 닫고 여는 순서를 지키지만, 그게 깨져도 DB 가 막는다.
        Index(
            "uq_chat_conversations_open",
            "user_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
    )


class ChatMessage(Base):
    """대화의 말풍선 하나."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[ChatMessageRole] = mapped_column(
        pg_enum(ChatMessageRole, "chat_message_role"), nullable=False
    )
    status: Mapped[ChatMessageStatus] = mapped_column(
        pg_enum(ChatMessageStatus, "chat_message_status"), nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    """GENERATING 이거나 가드레일이 막았으면(BLOCKED) NULL."""
    safety_status: Mapped[SafetyStatus | None] = mapped_column(
        pg_enum(SafetyStatus, "safety_status"), nullable=True
    )
    """ASSISTANT 메시지의 가드레일 판정. USER 메시지는 NULL."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    """**`now()` 가 아니라 `clock_timestamp()` 다.** A-2 는 USER 메시지와 ASSISTANT
    자리표시를 한 트랜잭션에 넣는데, `now()` 는 트랜잭션 시작 시각이라 둘이 같은 값이
    되어 "오래된 순" 정렬이 정해지지 않는다."""

    __mapper_args__ = {"eager_defaults": True}

    __table_args__ = (
        Index("ix_chat_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )


class ChatFoodCard(Base):
    """ASSISTANT 메시지에 딸린 추천 음식 카드. A-5 "이걸로 바꾸기" 가 이 id 로 찾는다.

    **성분(`effects`)은 저장하지 않는다.** `meal_feedbacks.suggestions` 와 같은 원칙이다 —
    응답을 만들 때 `food_ref_id` 로 `food_refs` 를 읽는다. 저장하면 그 표가 갱신될 때
    낡고, 같은 끼니의 카드가 피드백 화면과 숫자가 갈린다.
    """

    __tablename__ = "chat_food_cards"

    id: Mapped[uuid.UUID] = uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    """메시지 안에서의 카드 순서. 0 부터. 한 메시지 안에서 겹칠 수 없다."""
    food_name: Mapped[str] = mapped_column(String(255), nullable=False)
    advice: Mapped[str] = mapped_column(Text, nullable=False)
    """응답에는 안 나간다. A-5 가 이 카드를 `meal_feedbacks.suggestions` 로 되돌릴 때
    `{foodName, advice, candidateFoodRefId}` 를 다시 만들어야 해서 둔다."""
    food_ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """AI 가 준 `candidateFoodRefId`. **FK 가 없다** — `food_refs` 에 없는 id 일 수 있고,
    FK 를 걸면 카드 INSERT 가 깨진다. 못 찾으면 `effects: []` 로 나간다."""

    __table_args__ = (
        # UNIQUE 의 인덱스가 message_id 로 시작하므로 message_id 단독 인덱스는 따로 두지 않는다
        # (`satiety_checkins` 와 같은 판단).
        UniqueConstraint("message_id", "position", name="uq_chat_food_cards_message_id_position"),
    )
