"""`POST /meals/{mealId}/confirm` · `GET /meals/{mealId}/evaluation` 스키마.

두 응답은 **`feedbackStatus` 하나만 다르다** (명세: "confirm 응답과 동일
(`feedbackStatus` 제외)"). 그래서 공통부를 두고 confirm 만 한 필드를 더한다 —
따로 쓰면 한쪽만 고쳐져 조용히 어긋난다.
"""

from __future__ import annotations

import uuid

from pydantic import ConfigDict, Field

from app.models.enums import (
    FeedbackStatus,
    MealStatus,
    MedicationStage,
    NutrientCode,
    NutrientState,
    NutritionSource,
    ScoreAxis,
)
from app.schemas.base import CamelModel


class MealConfirmRequest(CamelModel):
    """확정 요청. 식후 포만감만 받는다.

    extra="forbid": 오타난 필드를 조용히 무시하지 않는다 (`schemas/user.py` 와
    같은 이유). 이 요청은 필드가 하나뿐이라, 오타가 나면 그 하나가 통째로
    빠진 것으로 처리돼 포만감 없이 확정된다.
    """

    model_config = ConfigDict(extra="forbid")

    satiety_after_pct: int = Field(ge=0, le=100)


class QqsScores(CamelModel):
    """세 축의 점수. 0~100 정수이고, 근거가 없으면 null 이다.

    합계도 가중치도 없다 (절대 규칙 3 · D9).
    """

    quantity: int | None
    quality: int | None
    satiety: int | None


class NutrientRow(CamelModel):
    """명세 `nutrients[]` 한 줄.

    `target` · `state` 는 null 허용이다 — 명세가 "미확정 시 게이지 미표시" 라고
    적어 두었고, 단계별 목표치가 아직 팀 확정 전이다
    (`services/evaluation/stage_profile.py`).

    **기본값을 두지 않는다.** `= None` 을 붙이면 OpenAPI 의 `required` 에서 빠져
    생성 클라이언트가 `current?: number | null` 로 받는다. 명세는 이 키들이 값이
    없어도 **항상 있고 명시적으로 null** 이라고 정한다.
    """

    code: NutrientCode
    label: str
    current: float | None
    target: float | None
    unit: str
    state: NutrientState | None


class EvaluationEvidence(CamelModel):
    """점수의 출처. 나중에 "왜 이 점수였나" 를 추적하려면 필요하다."""

    db_source: str
    stage_rule_version: str
    weight_profile_version: str
    nutrition_sources: list[NutritionSource]


class MealEvaluationResponse(CamelModel):
    """`GET /meals/{mealId}/evaluation` 응답."""

    meal_id: uuid.UUID
    status: MealStatus
    stage: MedicationStage
    scores: QqsScores
    stage_emphasis: list[ScoreAxis]
    nutrients: list[NutrientRow]
    evidence: EvaluationEvidence


class MealConfirmResponse(MealEvaluationResponse):
    """`POST /meals/{mealId}/confirm` 응답. 위에 `feedbackStatus` 만 더한다.

    확정 직후에는 피드백 문장이 아직 없다 — 점수(Rule Engine)와 문장(AI)은
    분리돼 있어서(README) 확정은 점수까지만 끝내고 문장은 뒤따라온다.
    """

    feedback_status: FeedbackStatus
