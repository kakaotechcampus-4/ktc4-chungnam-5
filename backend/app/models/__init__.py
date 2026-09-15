"""SQLAlchemy 모델.

Alembic autogenerate 가 테이블을 발견하려면 모든 모델이 여기서 import 되어야 한다.
"""

from app.db.base import Base
from app.models.evaluation import QQSEvaluation
from app.models.feedback import (
    DailyFeedback,
    DailyFeedbackSource,
    LongTermFeedback,
    LongTermFeedbackSource,
    MealFeedback,
)
from app.models.food import FoodRef
from app.models.handoff import MedicalHandoffLog
from app.models.meal import Meal, MealItem, SatietyLog, UserCorrection
from app.models.medication import MedicationRecord, MedicationSnapshot
from app.models.user import User, UserGoal, UserState

__all__ = [
    "Base",
    "DailyFeedback",
    "DailyFeedbackSource",
    "FoodRef",
    "LongTermFeedback",
    "LongTermFeedbackSource",
    "Meal",
    "MealFeedback",
    "MealItem",
    "MedicalHandoffLog",
    "MedicationRecord",
    "MedicationSnapshot",
    "QQSEvaluation",
    "SatietyLog",
    "User",
    "UserCorrection",
    "UserGoal",
    "UserState",
]
