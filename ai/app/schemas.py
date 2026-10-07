"""BE ↔ AI 요청·응답 계약.

`ai-stub/schemas.py` 의 구조·필드명을 그대로 따른다. BE 가 이 모양으로 이미 구현돼 있다.

JSON 은 camelCase, 파이썬은 snake_case 다. `_Camel` 이 둘을 이어 준다.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ─────────────────────────── Common ───────────────────────────


class MealType(str, Enum):
    BREAKFAST = "BREAKFAST"
    LUNCH = "LUNCH"
    DINNER = "DINNER"
    SNACK = "SNACK"


class Stage(str, Enum):
    PRE_DOSE = "PRE_DOSE"
    INITIAL = "INITIAL"
    TITRATION = "TITRATION"
    MAINTENANCE = "MAINTENANCE"


class SafetyStatus(str, Enum):
    SAFE = "SAFE"
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


# ─────────────────────────── /analyze-meal ───────────────────────────


class AnalyzeMealRequest(_Camel):
    meal_id: str
    meal_type: MealType
    eaten_at: dt.datetime
    stage: Stage
    image_url: str | None = None  # presigned URL. AI 는 S3 권한이 없다
    raw_text: str | None = None  # 이미지와 함께 올 수도 있다

    @model_validator(mode="after")
    def _need_image_or_text(self) -> AnalyzeMealRequest:
        if not self.image_url and not self.raw_text:
            raise ValueError("imageUrl 과 rawText 중 최소 하나는 있어야 한다")
        return self


class RecognizedItem(_Camel):
    """meal_items 한 행에 대응한다.

    성분(kcal·단백질) 필드는 일부러 없다. AI 는 음식을 지목만 하고
    성분은 BE 가 food_refs 에서 채운다. 양도 g 가 아니라 자연 단위로 준다.
    길이·범위 제한은 BE 가 저장 전에 거르는 기준(meal_items 컬럼)과 같다.
    """

    # 가장 일반적인 표준 음식명. 수식어·브랜드명은 뺀다
    original_food_name: str = Field(min_length=1, max_length=255)
    estimated_amount: float = Field(ge=0, le=999999.99)
    unit: str = Field(min_length=1, max_length=32)  # g / 개 / ml … g 환산은 BE 가 한다
    confidence: float = Field(ge=0, le=1)
    # BE /internal/v1 search_foods 로 찾아 채운다. 그 API 가 생기기 전까지는 None
    candidate_food_ref_id: str | None = None
    clarify_question: str | None = None


class AnalyzeMealResponse(_Camel):
    meal_id: str
    model_version: str
    safety_status: SafetyStatus
    items: list[RecognizedItem] = Field(default_factory=list)


# ─────────────────────────── /short-feedback ───────────────────────────
# TODO: 계약 미확정 (A-1-2). 확정되면 ai-stub/schemas.py 구조를 기준으로 작성한다.


# ─────────────────────────── /long-feedback ───────────────────────────
# TODO: 계약 미확정. 확정되면 ai-stub/schemas.py 구조를 기준으로 작성한다.
