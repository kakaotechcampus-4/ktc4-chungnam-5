"""단계별 채점 기준선(`STAGE_PROFILES`)과 강조축(`STAGE_EMPHASIS`).

**둘은 역할이 다르다.**

- `STAGE_PROFILES` — **점수를 바꾸는** 값. 단계마다 어느 방향이 위험한지를
  보고 그 쪽 기준을 조였다. 수치와 근거는 `docs/be-qqs-scoring-rule.md` 「6. 단계별 기준」 표다.
- `STAGE_EMPHASIS` — **화면에서 어느 게이지를 앞세울지**만 정한다. 채점에 쓰지 않는다.

**절대 규칙 3(D9):** `total_score` 도 축별 가중치도 없다. 단계별 차이는 곱셈이 아니라
기준선의 엄격함으로 낸다. 이 표를 바꾸면 `services/evaluation` 의 `STAGE_RULE_VERSION` 을 올린다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from app.models.enums import MedicationStage, ScoreAxis
from app.services.evaluation.rule_engine import DailyTargets, QuantityBand, StageProfile

_SODIUM_MG_PER_DAY: Final = Decimal("2300")
"""KDRI 2025 만성질환위험감소섭취량. 단계 무관."""
_FIBER_G_PER_DAY: Final = Decimal("25")
"""KDRI 2025 충분섭취량의 근거값(Reynolds 2019). 성별·나이 컬럼이 없어 공통값을 쓴다."""
_PROTEIN_ON_DRUG: Final = Decimal("1.2")
"""공동 권고(Mozaffarian 2025) 감량 중 1.2–1.6 g/kg/일의 하한. 단계별 기준은 권고에 없다."""


def _band(lo: str, hi: str, under: str, over: str) -> QuantityBand:
    return QuantityBand(Decimal(lo), Decimal(hi), Decimal(under), Decimal(over))


STAGE_PROFILES: Final[dict[MedicationStage, StageProfile]] = {
    # 위험 없음 → 전부 기본값. 단백질은 KDRI 2025 권장섭취량 기준.
    MedicationStage.PRE_DOSE: StageProfile(
        _band("85", "105", "30", "30"),
        DailyTargets(Decimal("0.91"), _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY),
    ),
    # 부족 쪽 엄격 — 부작용으로 너무 적게 먹는다. 시작 며칠 고섬유 회피 → 식이섬유 2/3.
    MedicationStage.INITIAL: StageProfile(
        _band("70", "100", "15", "30"),
        DailyTargets(_PROTEIN_ON_DRUG, _FIBER_G_PER_DAY * 2 / 3, _SODIUM_MG_PER_DAY),
    ),
    # 부족 쪽 엄격 — 올릴 때마다 섭취가 줄어든다 (1.0mg 에서 −24%).
    MedicationStage.TITRATION: StageProfile(
        _band("60", "90", "15", "30"),
        DailyTargets(_PROTEIN_ON_DRUG, _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY),
    ),
    # 공동 권고의 섭취 감소 16–39% → 평소의 61–84%. Quantity 는 위험 방향 없음.
    MedicationStage.MAINTENANCE: StageProfile(
        _band("60", "85", "30", "30"),
        DailyTargets(_PROTEIN_ON_DRUG, _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY),
    ),
    # 초과 쪽 엄격 — 용량을 내리면 식욕이 돌아온다 (간접 근거, Seier 2025).
    MedicationStage.REDUCED: StageProfile(
        _band("65", "90", "30", "15"),
        DailyTargets(_PROTEIN_ON_DRUG, _FIBER_G_PER_DAY, _SODIUM_MG_PER_DAY),
    ),
}
"""단계 → 채점 기준선. ENUM 전체를 덮는다."""


def profile_for(stage: MedicationStage) -> StageProfile:
    """그 단계의 채점 기준선."""
    return STAGE_PROFILES[stage]


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
이고, 오늘 그 축을 매길 수 있는지와는 별개다 — 체중이 없어 Quality 가 null 인
식사에서도 단계의 의미는 변하지 않는다. 걸러 내면 명세 예시(`["SATIETY", "QUALITY"]`)와
어긋나고, 두 축이 다 null 인 식사는 빈 배열이 되어 FE 가 기준을 잃는다.
점수가 null 인 축을 어떻게 그릴지는 FE 가 정한다.
"""


def emphasis_for(stage: MedicationStage) -> tuple[ScoreAxis, ...]:
    """그 단계에서 강조할 축. 모르는 단계는 없다 — ENUM 전체를 덮는다."""
    return STAGE_EMPHASIS[stage]
