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
    """포즈 단계. 여기까지가 분류의 전부이고, 용량 판단은 하지 않는다 (규칙 1)."""

    PRE_DOSE = "PRE_DOSE"
    INITIAL = "INITIAL"
    TITRATION = "TITRATION"
    MAINTENANCE = "MAINTENANCE"


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
    "지금 처리 중"은 곧 "PENDING 인데 행 잠금이 걸린 상태"이고 pg_locks 로 본다.

    FAILED 는 DLQ 자리다. QUEUE_MAX_ATTEMPTS 만큼 실패하면 여기로 옮기고 더 집지 않는다.
    """

    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


def pg_enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """native ENUM 컬럼 타입. 멤버 이름이 아니라 값을 저장한다."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
    )
