"""`GET /meals/{mealId}/feedback` · `POST /meals/{mealId}/satiety-checkins` 스키마."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING


from app.models.enums import FeedbackStatus, NutrientCode, SafetyStatus
from app.schemas.base import CamelModel

if TYPE_CHECKING:  # 런타임 import 를 피한다 — 스키마가 모델에 의존하지 않게.
    from app.models.feedback import MealFeedback


class SuggestionNutrient(CamelModel):
    """제안 음식이 채워 주는 성분 한 줄. 명세 `suggestions[].nutrients[]`."""

    code: NutrientCode
    amount_g: float


class FeedbackSuggestion(CamelModel):
    """다음 끼니 제안 한 건.

    `nutrients` 는 **저장돼 있지 않다.** AI 는 `candidateFoodRefId` 만 주고
    (`ai-stub/schemas.py::Suggestion`: "nutrients 는 여기 없다 — BE 가 food_refs 에서
    채운다"), 응답을 만들 때 그 id 로 조회해 채운다. 저장하면 `food_refs` 가 갱신될 때
    낡는다.

    참조를 못 찾으면 `nutrients` 는 `[]` 다 — 제안 문구는 그대로 쓸모가 있어서
    항목을 통째로 버리지 않는다.
    """

    food_name: str
    nutrients: list[SuggestionNutrient]
    advice: str


class ExpectedSatiety(CamelModel):
    """제안대로 먹었을 때의 예상 포만감. 명세 `expectedSatietyPct`."""

    current: int
    after: int


class MealFeedbackResponse(CamelModel):
    """`GET /meals/{mealId}/feedback`. 명세 6필드.

    **nullable 필드에 기본값을 주지 않는다.** `= None` 을 달면 openapi 의 `required`
    에서 빠져 생성 클라이언트가 `summary?: string` 으로 받는다. 값이 `null` 로 올
    뿐 키는 항상 있다 (`schemas/evaluation.py` 와 같은 판단).
    """

    feedback_status: FeedbackStatus
    summary: str | None
    reasoning: str | None
    suggestions: list[FeedbackSuggestion]
    expected_satiety_pct: ExpectedSatiety | None
    safety_status: SafetyStatus | None

    @classmethod
    def pending(cls) -> MealFeedbackResponse:
        """아직 피드백이 없다. 행이 안 생겼을 때."""
        return cls(
            feedback_status=FeedbackStatus.PENDING,
            summary=None,
            reasoning=None,
            suggestions=[],
            expected_satiety_pct=None,
            safety_status=None,
        )

    @classmethod
    def from_row(
        cls, row: MealFeedback, *, suggestions: list[FeedbackSuggestion]
    ) -> MealFeedbackResponse:
        """행 하나를 응답으로. **안전하지 않으면 내용을 싣지 않는다.**

        명세: `safetyStatus: BLOCKED` → `summary` · `suggestions` 미표시, 상담 안내로
        대체. 여기서 거르는 이유는 **노출 경로가 둘**이기 때문이다 — `GET /meals/{mealId}`
        도 같은 행을 읽는다. 응답 조립부마다 쓰면 코드가 두 벌이 되고 한쪽만 고쳐진다.

        `SAFE` 가 아닌 것을 전부 막는다. `safety_status` 의 `server_default` 가
        `REVIEW_REQUIRED` 라(모델 독스트링: "가드레일을 통과해야만 SAFE 가 된다"),
        예외적인 `BLOCKED` 만이 아니라 **검수 전 행도** 여기 걸린다.

        `reasoning` 도 막는다. 명세가 이름을 대지 않았지만 "왜 그렇게 판단했는지" 라
        본문과 같은 위험을 지닌다 — 막을 것만 나열하면 새 필드가 늘 때 빠뜨린다.
        """
        safe = row.safety_status is SafetyStatus.SAFE
        return cls(
            # 생성은 끝났다. 내용을 보여줄 수 있는지는 safetyStatus 가 말한다.
            feedback_status=FeedbackStatus.READY,
            summary=row.body if safe else None,
            reasoning=row.reasoning if safe else None,
            suggestions=suggestions if safe else [],
            # 담을 컬럼도 AI 응답 필드도 없다. 명세 필드라 키는 두되 값이 없다.
            expected_satiety_pct=None,
            safety_status=row.safety_status,
        )
