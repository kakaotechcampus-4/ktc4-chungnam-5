"""챗봇 API 스키마 (`contracts/ChatbotAPI.md`).

**nullable 필드에 기본값을 주지 않는다.** `= None` 을 달면 openapi 의 `required` 에서
빠져 생성 클라이언트가 `text?: string` 으로 받는다 (`schemas/feedback.py` 와 같은 판단).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import ConfigDict

from app.models.enums import ChatMessageRole, ChatMessageStatus, NutrientCode, SafetyStatus
from app.schemas.base import CamelModel, KstDatetime

EffectUnit = Literal["g", "mg", "kcal"]


class FoodCardEffect(CamelModel):
    """영양 효과 칩 하나. `amount` 는 `food_refs` 의 **100g 당** 성분량이다
    (`crud/feedback.py::nutrients_by_food_ref` — 끼니 피드백 화면과 같은 숫자)."""

    code: NutrientCode
    amount: float
    unit: EffectUnit


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
