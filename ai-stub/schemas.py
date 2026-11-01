"""BE ↔ AI 요청·응답 계약.

AI 레이어가 아직 없어서, BE 가 개발하는 동안 이 모양에 맞춰 코드를 쓴다.
AI 팀이 실제 서비스를 만들 때 이 파일이 기준이 된다.

JSON 은 camelCase, 파이썬은 snake_case 다. `_Camel` 이 둘을 이어 준다.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


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
    REDUCED = "REDUCED"  # 직전보다 낮은 용량으로 내렸고 아직 정착 전. BE MedicationStage 와 같다


class SafetyStatus(str, Enum):
    SAFE = "SAFE"
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Scope(str, Enum):
    MEAL = "MEAL"
    DAILY = "DAILY"


class PeriodType(str, Enum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    ALL = "ALL"  # 공개 API 의 ?period=all. DB ENUM 에는 아직 없다


class QQS(_Camel):
    """BE Rule Engine 의 점수. **null 은 "못 쟀다" 다** — 0(바닥)과 다르다.

    근거가 없는 축은 BE 가 채점하지 않고 null 로 둔다 — 성분을 못 구했거나 체중 기록이
    없는 경우 등(`backend/app/services/evaluation/rule_engine.py`). AI 는 null 인 축을
    평가하는 문장을 쓰지 않는다.
    """

    quantity: float | None
    quality: float | None
    satiety: float | None


# ─────────────────────────── /analyze-meal ───────────────────────────


class AnalyzeMealRequest(_Camel):
    meal_id: str
    meal_type: MealType
    eaten_at: dt.datetime
    stage: Stage
    image_url: str | None = None  # presigned URL. AI 는 S3 권한이 없다
    raw_text: str | None = None

    @model_validator(mode="after")
    def _need_image_or_text(self) -> AnalyzeMealRequest:
        if not self.image_url and not self.raw_text:
            raise ValueError("imageUrl 과 rawText 중 최소 하나는 있어야 한다")
        return self


class RecognizedItem(_Camel):
    """meal_items 한 행에 대응한다.

    성분(kcal·단백질) 필드는 일부러 없다. AI 는 음식을 지목만 하고
    성분은 BE 가 food_refs 에서 채운다. 양도 g 가 아니라 자연 단위로 준다.
    """

    original_food_name: str
    estimated_amount: float
    unit: str  # g / 개 / ml … g 환산은 BE 가 한다
    confidence: float = Field(ge=0, le=1)
    candidate_food_ref_id: str | None = None
    clarify_question: str | None = None


class AnalyzeMealResponse(_Camel):
    meal_id: str
    model_version: str
    safety_status: SafetyStatus
    items: list[RecognizedItem] = Field(default_factory=list)


# ─────────────────────────── /short-feedback ───────────────────────────


class Nutrition(_Camel):
    """BE → AI 방향. AI 가 생성하는 값이 아니다."""

    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carb_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None


class FeedbackItem(_Camel):
    display_name: str
    amount: float | None  # null 이면 먹은 양을 모른다. 음식을 빼지 않고 보낸다
    unit: str | None
    nutrition: Nutrition | None = None


class SatietyCheckin(_Camel):
    checkin_offset_hours: float
    satiety_pct: int = Field(ge=0, le=100)


class SatietyContext(_Camel):
    before_pct: int | None = Field(default=None, ge=0, le=100)
    after_pct: int | None = Field(default=None, ge=0, le=100)
    checkins: list[SatietyCheckin] = Field(default_factory=list)
    hunger_return_minutes: int | None = None
    user_comment: str | None = None


class DailyMealSummary(_Camel):
    meal_type: MealType
    summary: str
    qqs: QQS


class PreviousMealItem(_Camel):
    display_name: str
    amount: float | None
    unit: str | None


class PreviousMeal(_Camel):
    """그날 이 끼니보다 먼저 먹은 확정 끼니. 문장이 아니라 음식 · 점수로 준다.

    앞 끼니 문장은 아직 만들어지는 중일 수 있어서다 — 음식 · 점수는 확정 때 이미 있다.
    """

    meal_type: MealType
    skipped: bool = False  # true 면 "안 먹은 끼니" — items 가 비고 qqs 가 모두 null
    qqs: QQS
    items: list[PreviousMealItem] = Field(default_factory=list)
    nutrition_totals: Nutrition | None = None  # 끼니 영양 합계. 없으면 null


class ShortFeedbackRequest(_Camel):
    scope: Scope
    user_id: str
    stage: Stage
    qqs: QQS  # 채점은 BE Rule Engine 이 이미 끝냈다. 식사 안 함이면 세 축 모두 null

    # scope=MEAL
    meal_id: str | None = None
    # 저녁(DINNER)이면 다음 끼니 추천(suggestions)을 비운다 — 아침 · 점심은 다음 끼니 추천
    meal_type: MealType | None = None
    # true 면 "식사 안 함" 끼니다. items · satiety 가 비고 qqs 가 모두 null 이다 —
    # 영양 판정 없이, 앞선 끼니(previousMeals)와 다음 끼니 챙기기로 문장을 쓴다
    skipped: bool = False
    items: list[FeedbackItem] = Field(default_factory=list)
    satiety: SatietyContext | None = None
    # 그날(KST) 이 끼니보다 먼저 먹은 확정 끼니. 먹은 순서. 문장이 이들을 반영해 누적된다 —
    # 단, Q/Q/S 와 영양소 평가는 이 끼니 단독이다
    previous_meals: list[PreviousMeal] = Field(default_factory=list)
    # 그날(KST) 끼니 영양 합 — daily_feedbacks.total_* 와 같은 값. 확정 끼니가 남긴 합계를
    # 더한 것이라 식사 안 함 · 확인 전 끼니는 빠진다. 더할 합계가 없으면 null
    daily_nutrition_totals: Nutrition | None = None
    # 사용자 체중(kg). 먹을 때의 체중, 그 이전 기록이 없으면 가장 최근 체중이다 — BE 가
    # Quality 채점에 쓴 값과 같다. 기록이 하나도 없으면 null
    weight_kg: float | None = None

    # scope=DAILY
    date: dt.date | None = None
    meals: list[DailyMealSummary] = Field(default_factory=list)

    @model_validator(mode="after")
    def _scope_fields(self) -> ShortFeedbackRequest:
        if self.scope is Scope.MEAL and not self.meal_id:
            raise ValueError("scope=MEAL 에는 mealId 가 필요하다")
        if self.scope is Scope.DAILY and self.date is None:
            raise ValueError("scope=DAILY 에는 date 가 필요하다")
        return self


class Suggestion(_Camel):
    """nutrients 는 여기 없다 — BE 가 food_refs 에서 채운다."""

    food_name: str
    advice: str
    candidate_food_ref_id: str | None = None


class ShortFeedbackResponse(_Camel):
    body: str  # 공개 API 에서는 summary 로 나간다
    model_version: str
    safety_status: SafetyStatus
    reasoning: str | None = None
    # scope=DAILY 면 null. mealType=DINNER 면 빈 배열 — BE 도 저녁 추천은 저장하지 않는다
    suggestions: list[Suggestion] | None = None


# ─────────────────────────── /long-feedback ───────────────────────────


class SeriesPoint(_Camel):
    date: dt.date
    quantity: float
    quality: float
    satiety: float


class LongFeedbackRequest(_Camel):
    user_id: str
    period_type: PeriodType
    period_start: dt.date
    period_end: dt.date
    stage: Stage
    series: list[SeriesPoint] = Field(default_factory=list)
    daily_summaries: list[str] = Field(default_factory=list)


class LongFeedbackResponse(_Camel):
    """chartData 는 없다 — BE 가 qqs_evaluations 를 집계해 채운다."""

    trend_summary: str
    recommendation: str
    model_version: str
    safety_status: SafetyStatus


# ─────────────────────────── /chat ───────────────────────────


class ChatRole(str, Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class ChatTurn(_Camel):
    """지난 대화 한 줄. BE 가 BLOCKED · FAILED 답과 그 질문은 빼고 보낸다."""

    role: ChatRole
    text: str


class Medication(_Camel):
    drug_name: str
    dose_mg: float


class TodayMeal(_Camel):
    """오늘(KST) 확정된 끼니 하나. 점수는 BE Rule Engine 이 매겼다."""

    meal_type: MealType
    foods: list[str] = Field(default_factory=list)
    qqs: QQS | None = None  # 평가 행이 없으면 null


class TodayContext(_Camel):
    date: dt.date
    meals: list[TodayMeal] = Field(default_factory=list)


class BaseMealSuggestion(_Camel):
    food_name: str
    advice: str


class BaseMeal(_Camel):
    """대화의 기준 끼니. `suggestions` 는 사용자가 지금 보고 있는 "현재 추천" 이다."""

    meal_id: str
    meal_type: MealType
    qqs: QQS | None = None
    suggestions: list[BaseMealSuggestion] = Field(default_factory=list)


class LongTermContext(_Camel):
    period_type: PeriodType
    trend_summary: str
    recommendation: str | None = None


class ChatContext(_Camel):
    """BE 가 매 메시지마다 선주입하는 사용자 정보. AI 가 tool 로 다시 묻지 않게 한다."""

    stage: Stage
    medication: Medication | None = None
    today: TodayContext
    base_meal: BaseMeal | None = None
    long_term: LongTermContext | None = None


class ChatRequest(_Camel):
    user_id: str
    conversation_id: str
    text: str = Field(min_length=1)
    history: list[ChatTurn] = Field(default_factory=list)
    context: ChatContext


class ChatFoodCard(_Camel):
    """nutrients 는 여기 없다 — BE 가 food_refs 에서 채운다."""

    food_name: str
    advice: str
    candidate_food_ref_id: str | None = None


class ChatResponse(_Camel):
    """BLOCKED 면 `text` 는 null, `foodCards` 는 [] 다 (입력 · 출력 가드레일 모두)."""

    text: str | None
    model_version: str
    safety_status: SafetyStatus
    food_cards: list[ChatFoodCard] = Field(default_factory=list)
