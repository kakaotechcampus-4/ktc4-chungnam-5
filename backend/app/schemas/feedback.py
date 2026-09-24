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
    """`expectedSatietyPct`. `current` 는 지금 포만감, `after` 는 **제안을 따랐을 때의
    예상치**다 — 명세 예시가 `{current: 62, after: 79}` 를 제안 바로 아래 둔다.

    ⚠️ **지금은 `after` 에 `current` 를 그대로 넣는다.** 예측을 만들 소스가 없다 —
    `ai-stub` 계약에 해당 필드가 없고, "두부를 먹으면 17%p 오른다" 를 BE 가 계산하면
    절대 규칙 1(의료 판단 금지)에 걸린다. 그렇다고 객체째 `null` 로 두면 FE 가 게이지를
    아예 못 그려서, 우선 같은 값으로 채워 "변화 없음" 으로 보이게 한다. 예측 소스가
    생기면 `after` 만 갈아 끼우면 된다."""

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
        cls,
        row: MealFeedback,
        *,
        suggestions: list[FeedbackSuggestion],
        expected: ExpectedSatiety | None,
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
            # `REVIEW_REQUIRED` 는 **아직**이다 — `PENDING` 을 낸다.
            #
            # 셋을 갈라서 본다. `READY` + `summary: null` 은 FE 에 빈 카드를 그리게
            # 하는데, 그게 `body is None` 가드가 막으려던 바로 그 모양이다.
            #   SAFE            내용을 싣는다                      → READY
            #   BLOCKED         생성은 끝났고 영영 못 보여 준다.
            #                   FE 는 상담 안내로 바꾼다
            #                   (`MEDICAL_QUESTION_DETECTED` 동반) → READY
            #   REVIEW_REQUIRED 검수를 통과하면 보일 수도 있다     → PENDING
            #
            # `safety_status` 의 `server_default` 가 `REVIEW_REQUIRED` 라 워커가
            # 막 쓴 행이 전부 여기 걸린다 — 기본값이 "아직" 쪽이어야 맞다.
            feedback_status=(
                FeedbackStatus.READY
                if safe or row.safety_status is SafetyStatus.BLOCKED
                else FeedbackStatus.PENDING
            ),
            summary=row.body if safe else None,
            reasoning=row.reasoning if safe else None,
            suggestions=suggestions if safe else [],
            expected_satiety_pct=expected if safe else None,
            safety_status=row.safety_status,
        )
