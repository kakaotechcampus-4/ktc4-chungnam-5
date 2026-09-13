"""`analyze_meal` 의 DB 저장 검증. 실제 Postgres 를 쓴다.

DB 가 없으면 통째로 건너뛴다:

    docker compose -f infra/docker-compose.yml up -d
    alembic upgrade head

모델이 JSONB · PG ENUM 을 쓰기 때문에 SQLite 로 대체할 수 없다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.enums import MealItemSource, MealStatus, MealType, MedicationStage
from app.models.meal import Meal
from app.models.medication import MedicationSnapshot
from app.models.user import User
from app.worker.jobs.analyze_meal import run as analyze_meal


def _db_is_up() -> bool:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_is_up(),
    reason="Postgres 가 떠 있지 않다. infra/docker-compose.yml 로 띄우고 alembic upgrade head.",
)


class FakeAi:
    """고정 응답. ai-stub 의 더미와 같은 모양이다."""

    def __init__(self, items: list[dict[str, Any]] | None = None) -> None:
        self.items = items if items is not None else _DEFAULT_ITEMS
        self.calls: list[dict[str, Any]] = []

    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "mealId": payload["mealId"],
            "modelVersion": "stub-vision-0",
            "safetyStatus": "SAFE",
            "items": self.items,
        }

    def short_feedback(self, payload):  # pragma: no cover
        raise NotImplementedError

    def long_feedback(self, payload):  # pragma: no cover
        raise NotImplementedError


_DEFAULT_ITEMS = [
    {
        "originalFoodName": "참치김밥",
        "candidateFoodRefId": None,
        "estimatedAmount": 250,
        "unit": "g",
        "confidence": 0.62,
        "clarifyQuestion": "김밥 속재료가 참치가 맞나요?",
    },
    {
        "originalFoodName": "삶은 계란",
        "candidateFoodRefId": None,
        "estimatedAmount": 2,
        "unit": "개",
        "confidence": 0.96,
        "clarifyQuestion": None,
    },
]


class FakeTask:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.receipt = "r1"
        self.receive_count = 1


@pytest.fixture
def meal():
    """users → medication_snapshots → meals 를 만들고 끝나면 지운다."""
    with SessionLocal() as db:
        user = User(nickname="테스트", baseline_meal_kcal=Decimal("650.00"))
        db.add(user)
        db.flush()

        snapshot = MedicationSnapshot(user_id=user.id, stage=MedicationStage.MAINTENANCE)
        db.add(snapshot)
        db.flush()

        row = Meal(
            user_id=user.id,
            medication_snapshot_id=snapshot.id,
            meal_type=MealType.LUNCH,
            raw_text="김밥 한 줄",
            eaten_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        meal_id = row.id
        user_id = user.id

    yield meal_id

    with SessionLocal() as db:
        db.delete(db.get(User, user_id))  # cascade 로 meal · items 까지 정리된다
        db.commit()


def _task(meal_id: uuid.UUID) -> FakeTask:
    return FakeTask(
        {
            "type": "meal.analyze",
            "mealId": str(meal_id),
            "mealType": "LUNCH",
            "eatenAt": "2026-08-21T12:40:00+09:00",
            "stage": "MAINTENANCE",
            "rawText": "김밥 한 줄",
        }
    )


def test_items_are_saved_and_status_moves(meal):
    ai = FakeAi()

    analyze_meal(_task(meal), ai)

    with SessionLocal() as db:
        row = db.get(Meal, meal)
        assert row.status is MealStatus.REVIEW_REQUIRED
        assert len(row.items) == 2

        by_name = {item.original_food_name: item for item in row.items}
        assert by_name["참치김밥"].display_name == "참치김밥"
        assert by_name["참치김밥"].source is MealItemSource.MODEL
        assert by_name["참치김밥"].confirmed_amount_g is None


def test_grams_saved_only_when_convertible(meal):
    """'계란 2개' 는 g 으로 옮길 근거가 없다. 지어내지 않고 NULL 로 둔다."""
    analyze_meal(_task(meal), FakeAi())

    with SessionLocal() as db:
        by_name = {i.original_food_name: i for i in db.get(Meal, meal).items}

    assert by_name["참치김밥"].estimated_amount_g == Decimal("250.00")
    assert by_name["삶은 계란"].estimated_amount_g is None
    # 원본 단위는 남아 있어야 사용자가 확인할 때 쓸 수 있다
    assert by_name["삶은 계란"].raw_ai_result["unit"] == "개"


def test_unknown_food_ref_is_dropped_not_crashed(meal):
    """DB 에 없는 food_ref_id 를 그대로 넣으면 FK 위반으로 커밋이 통째로 깨진다."""
    items = [dict(_DEFAULT_ITEMS[0], candidateFoodRefId="존재하지않는ID")]

    analyze_meal(_task(meal), FakeAi(items))

    with SessionLocal() as db:
        assert db.get(Meal, meal).items[0].food_ref_id is None


def test_redelivery_does_not_duplicate_items(meal):
    """SQS 는 at-least-once 다. 같은 메시지가 두 번 와도 항목이 늘면 안 된다."""
    ai = FakeAi()

    analyze_meal(_task(meal), ai)
    analyze_meal(_task(meal), ai)

    with SessionLocal() as db:
        assert len(db.get(Meal, meal).items) == 2
    # 두 번째는 AI 를 부르지도 않는다 — 상태를 먼저 본다
    assert len(ai.calls) == 1


def test_missing_meal_raises(meal):
    with pytest.raises(ValueError, match="식사를 찾을 수 없다"):
        analyze_meal(_task(uuid.uuid4()), FakeAi())
