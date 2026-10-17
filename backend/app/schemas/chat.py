"""챗봇 API 스키마 (`contracts/ChatbotAPI.md`).

**nullable 필드에 기본값을 주지 않는다.** `= None` 을 달면 openapi 의 `required` 에서
빠져 생성 클라이언트가 `text?: string` 으로 받는다 (`schemas/feedback.py` 와 같은 판단).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import ConfigDict, StringConstraints

from app.models.enums import (
    ChatMessageRole,
    ChatMessageStatus,
    NutrientCode,
    NutrientUnit,
    SafetyStatus,
)
from app.schemas.base import CamelModel, KstDatetime

class FoodCardEffect(CamelModel):
    """영양 효과 칩 하나. `amount` 는 `food_refs` 의 **100g 당** 성분량이다
    (`crud/feedback.py::nutrients_by_food_ref` — 끼니 피드백 화면과 같은 숫자)."""

    code: NutrientCode
    amount: float
    unit: NutrientUnit


class FoodCardResponse(CamelModel):
    food_card_id: uuid.UUID
    food_name: str
    effects: list[FoodCardEffect]


class ChatMessageResponse(CamelModel):
    """명세 「공통 Message Data Structure」."""

    message_id: uuid.UUID
    role: ChatMessageRole
    status: ChatMessageStatus
    text: str | None
    food_cards: list[FoodCardResponse]
    safety_status: SafetyStatus | None
    created_at: KstDatetime


class ConversationResponse(CamelModel):
    """A-1 · A-3 · A-4 공통 응답."""

    conversation_id: uuid.UUID
    meal_id: uuid.UUID | None
    messages: list[ChatMessageResponse]


class CreateConversationRequest(CamelModel):
    """A-1 요청. body 를 아예 안 보내도 된다(엔드포인트가 `None` 으로 받는다).

    extra="forbid": `mealID` 같은 오타가 조용히 무시되면 "기준 끼니 없음" 으로 처리되어
    엉뚱한 끼니의 추천이 뜬다.
    """

    model_config = ConfigDict(extra="forbid")

    meal_id: uuid.UUID | None = None


CHAT_REPLY_POLL_INTERVAL_MS = 1000
CHAT_REPLY_TIMEOUT_MS = 30000
"""FE 가 답변을 기다리는 상한. 서버의 만료 기준(`crud/chat.py::CHAT_REPLY_STALE_SEC`)은
이보다 넉넉하다 — FE 가 포기한 뒤에도 워커가 답을 쓸 수는 있다."""


class SendMessageRequest(CamelModel):
    """A-2 요청. 공백만 보낸 메시지는 빈 메시지다 — AI 를 부를 이유가 없다.

    extra="forbid": `message` 같은 오타 필드가 조용히 무시되면 `text` 누락 422 와 섞여
    원인을 찾기 어렵다.
    """

    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class SendMessageResponse(CamelModel):
    """A-2 응답 (202). FE 는 `assistantMessageId` 가 READY · FAILED 가 될 때까지 폴링한다."""

    user_message: ChatMessageResponse
    assistant_message_id: uuid.UUID
    poll_interval_ms: int
    timeout_ms: int
