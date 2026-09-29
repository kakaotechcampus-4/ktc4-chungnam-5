"""도메인 ENUM.

PostgreSQL native ENUM 으로 만든다. 값 오타를 DB 가 막아준다.
값을 추가할 때는 `ALTER TYPE ... ADD VALUE` 마이그레이션이 필요하다.
"""

import enum

from sqlalchemy import Enum as SAEnum


class GoalStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class MedicationStage(str, enum.Enum):
    """포즈 단계. 여기까지가 분류의 전부이고, 용량 판단은 하지 않는다 (규칙 1).

    판정 규칙은 `docs/be-medication-stage-rule.md`. TITRATION 은 "증량기"가 아니라
    **조정기**다 — 중간 용량인데 아직 정착 전이라는 뜻이고, 방향을 함의하지 않는다.
    """

    PRE_DOSE = "PRE_DOSE"
    INITIAL = "INITIAL"
    TITRATION = "TITRATION"
    MAINTENANCE = "MAINTENANCE"
    REDUCED = "REDUCED"
    """직전보다 낮은 용량으로 내렸고 아직 정착 전.

    TODO(모델): DB native ENUM `medication_stage` 에는 아직 이 값이 없다.
    `ALTER TYPE medication_stage ADD VALUE 'REDUCED'` 마이그레이션이 필요하고,
    그 전까지 이 값으로 INSERT 하면 DataError 로 죽는다.
    단계 개수(4개 vs 5개)는 FE 협의 안건이라 회의 후 마지막에 진행한다.
    """


class DrugName(str, enum.Enum):
    """지원 약물.

    **DB native ENUM 이 아니다** — `medication_records.drug_name` 은 VARCHAR 이고
    여기 값이 문자열로 저장된다. API 경계에서 값 집합을 막으려고 두는 것이라,
    약물을 추가하려면 여기와 `services/medication.py` 의 DOSE_LADDERS 두 곳을 고친다.

    **값이 한글인 이유:** FE 가 `"위고비"` 로 보내고 받는다 (투약 화면의 약 선택 탭).
    API 명세에 DrugName 열거형이 없어 FE 표기를 따랐다. 멤버 이름은 영문이라
    내부 코드(`DOSE_LADDERS` 등)는 값이 바뀌어도 그대로다.
    """

    WEGOVY = "위고비"
    MOUNJARO = "마운자로"




class HandoffTriggerType(str, enum.Enum):
    """의료 판단이 필요해 차단한 사유 (규칙 1)."""

    DOSAGE_QUESTION = "DOSAGE_QUESTION"  # 증량·감량 문의
    DISCONTINUATION_QUESTION = "DISCONTINUATION_QUESTION"  # 단약 문의
    PRESCRIPTION_QUESTION = "PRESCRIPTION_QUESTION"  # 처방 문의
    SEVERE_GI_SYMPTOM = "SEVERE_GI_SYMPTOM"  # 중증 위장관 증상
    OTHER = "OTHER"


class HandoffStatus(str, enum.Enum):
    PENDING = "PENDING"
    REVIEWED = "REVIEWED"
    RESOLVED = "RESOLVED"


class FoodCategory(str, enum.Enum):
    """음식 분류. Quality 채점에서 가공식품 비중을 보는 데 쓴다."""

    PROCESSED = "PROCESSED"  # 가공식품
    GENERAL = "GENERAL"  # 일반음식


class MealType(str, enum.Enum):
    BREAKFAST = "BREAKFAST"
    LUNCH = "LUNCH"
    DINNER = "DINNER"
    SNACK = "SNACK"


class MealStatus(str, enum.Enum):
    """이 4개가 전부다. 중간 상태를 추가하지 않는다."""

    ANALYZING = "ANALYZING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    EVALUATED = "EVALUATED"
    FAILED = "FAILED"


class MealItemSource(str, enum.Enum):
    MODEL = "MODEL"
    USER = "USER"


class SafetyStatus(str, enum.Enum):
    """생성된 피드백을 그대로 노출해도 되는지."""

    SAFE = "SAFE"
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class FeedbackPeriodType(str, enum.Enum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class TaskStatus(str, enum.Enum):
    """`task_queue` 행의 상태.

    RUNNING 이 없다. 워커는 처리하는 동안 행 잠금을 쥐고 있을 뿐이고, 그 사실은
    커밋 전이라 다른 세션에 보이지 않는다 — 써 봐야 아무도 관측할 수 없는 값이 된다.
    "지금 처리 중"은 곧 "PENDING 인데 행 잠금이 걸린 상태"이고, `pg_locks` 는 잠금이
    튜플 단위라 실용적이지 않아 `scripts/queue_status.py` 는 `pg_stat_activity` 로 근사한다.

    FAILED 는 DLQ 자리다. QUEUE_MAX_ATTEMPTS 만큼 실패하면 여기로 옮기고 더 집지 않는다.
    """

    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


# ── 여기부터는 응답 전용 ENUM ────────────────────────────────
#
# **DB 컬럼이 아니다.** `pg_enum()` 에 넘기지 않으므로 `ALTER TYPE` 마이그레이션도
# 필요 없다 (파일 상단 설명은 위쪽의 DB ENUM 들에 대한 것이다). API 명세
# 「열거형」의 값 집합을 응답 스키마에서 강제하려고 둔다 — 값이 늘면 여기만 고친다.


class NutrientCode(str, enum.Enum):
    """명세 「열거형」의 NutrientCode. 응답 `nutrients[].code` 다."""

    CALORIE = "CALORIE"
    PROTEIN = "PROTEIN"
    FAT = "FAT"
    CARB = "CARB"
    FIBER = "FIBER"
    SODIUM = "SODIUM"


class NutrientState(str, enum.Enum):
    """목표 대비 현재 위치. 목표가 미정이면 None 이고 이 값은 안 나간다."""

    SHORT = "SHORT"
    OK = "OK"
    OVER = "OVER"


class FeedbackStatus(str, enum.Enum):
    """피드백 문장의 생성 상태. 점수(Q/Q/S)와 별개다 — AI 가 죽어도 점수는 남는다."""

    PENDING = "PENDING"
    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"


class NutritionSource(str, enum.Enum):
    """성분값의 출처. 명세 `evidence.nutritionSources` 다.

    `meal_items` 에 컬럼이 없어 지금은 응답 조립 시점에 판단한다 — food_refs 에서
    온 것은 PUBLIC_DB, 사용자가 직접 넣은 것은 USER_INPUT.
    """

    PUBLIC_DB = "PUBLIC_DB"
    USER_INPUT = "USER_INPUT"


class ScoreAxis(str, enum.Enum):
    """Q/Q/S 세 축. 응답 `stageEmphasis` 가 이 값들의 부분집합이다.

    명세 「열거형」에 이름이 없어서 여기서 정의한다 — `stageEmphasis` 예시
    (`["SATIETY", "QUALITY"]`)의 원소가 곧 점수 축이다.
    """

    QUANTITY = "QUANTITY"
    QUALITY = "QUALITY"
    SATIETY = "SATIETY"


def pg_enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """native ENUM 컬럼 타입. 멤버 이름이 아니라 값을 저장한다."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
    )
