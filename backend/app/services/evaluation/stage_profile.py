"""단계별 강조축 — 화면이 어느 게이지를 앞세울지.

**채점 기준선은 여기 없다.** API 명세가 응답 모양만 정하고 Q/Q/S 계산식은 정하지
않았다 (`nutrients[].target` 이 예시에서 전부 `null` 이고 "미확정" 이라고 적혀
있다). 기준선을 서버가 지어내면 사용자가 받는 점수가 근거 없는 값이 된다.

팀이 기준선을 정하면 여기에 단계별 목표 범위를 더하고
`rule_engine` 에 채점 함수를 되살린다 — 나머지(엔드포인트·스키마·저장)는 그대로다.

**절대 규칙 3(D9)은 그때도 유효하다:** `total_score` 도 축별 가중치도 만들지
않는다. 단계별 차이는 곱셈이 아니라 기준선의 엄격함으로 낸다.
"""

from __future__ import annotations

from typing import Final

from app.models.enums import MedicationStage, ScoreAxis

STAGE_EMPHASIS: Final[dict[MedicationStage, tuple[ScoreAxis, ...]]] = {
    # 투약 전 — 약효가 없으니 식사량 습관이 먼저다.
    MedicationStage.PRE_DOSE: (ScoreAxis.QUANTITY,),
    # 적응기 — 부작용이 잦아 총량보다 영양 밀도가 중요하다.
    MedicationStage.INITIAL: (ScoreAxis.QUALITY,),
    # 조정기 — 용량을 올리며 섭취가 줄어드는 구간.
    MedicationStage.TITRATION: (ScoreAxis.QUANTITY, ScoreAxis.QUALITY),
    # 유지기 — 명세 예시가 이 단계다: "stageEmphasis": ["SATIETY", "QUALITY"].
    MedicationStage.MAINTENANCE: (ScoreAxis.SATIETY, ScoreAxis.QUALITY),
    # 감량기 — 내린 용량에 다시 적응하는 중이라 포만감이 흔들린다.
    MedicationStage.REDUCED: (ScoreAxis.SATIETY,),
}
"""단계 → 강조할 점수 축.

🚧 **명세가 준 건 MAINTENANCE 하나뿐이다** (`["SATIETY", "QUALITY"]`). 나머지 넷은
각 단계의 뜻에서 유추한 잠정값이라 팀 확인이 필요하다. 다만 이건 "어느 게이지를
크게 보여줄까" 라 틀려도 점수처럼 잘못된 판단을 만들지는 않는다.

**점수가 있는 축만 걸러 내보내지 않는다.** 이 값은 "이 단계에서 무엇이 중요한가"
이고, 오늘 그 축을 매길 수 있는지와는 별개다 — Quantity·Quality 기준선이 미정인
지금도 단계의 의미는 변하지 않는다. 걸러 내면 명세 예시(`["SATIETY", "QUALITY"]`)와
어긋나고, TITRATION 처럼 두 축이 다 미정인 단계는 빈 배열이 되어 FE 가 기준을
잃는다. 점수가 null 인 축을 어떻게 그릴지는 FE 가 정한다.
"""


def emphasis_for(stage: MedicationStage) -> tuple[ScoreAxis, ...]:
    """그 단계에서 강조할 축. 모르는 단계는 없다 — ENUM 전체를 덮는다."""
    return STAGE_EMPHASIS[stage]
