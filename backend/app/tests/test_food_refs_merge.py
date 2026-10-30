"""동명 음식 병합 검증 — 마이그레이션 `8d3f6a1c5e27` 과 적재 스크립트.

공공 DB 에는 이름이 같은 음식이 여러 건씩 있다. `(category, 정규화한 이름)` 마다 한 행만
남기고 성분은 100g 당 평균으로 둔다. 두 경로가 같은 규칙이어야 해서 함께 검증한다.

마이그레이션은 되감지 않고 SQL 목록을 그대로 실행한다(`test_meal_items_migration.py` 와
같은 이유 — 이 스위트는 `alembic upgrade head` 가 끝난 DB 위에서 돈다).
"""

from decimal import Decimal

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.crud import food as food_crud
from app.models.chat import ChatConversation, ChatFoodCard, ChatMessage
from app.models.enums import ChatMessageRole, ChatMessageStatus, FoodCategory
from app.models.feedback import MealFeedback
from app.models.food import FoodRef
from app.models.meal import MealItem
from app.tests.factories import (
    make_food_ref,
    make_meal,
    make_meal_feedback,
    make_meal_item,
    make_user,
)
from scripts.load_food_refs import merge_stage_into_food_refs


def _run_migration(db: Session) -> None:
    """마이그레이션 파일이 실제로 실행하는 SQL 을 그대로 돌린다."""
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    for statement in script.get_revision("8d3f6a1c5e27").module.MERGE_STATEMENTS:
        db.execute(text(statement))
    db.expire_all()


def _ids(db: Session) -> list[str]:
    return list(db.execute(select(FoodRef.id).order_by(FoodRef.id)).scalars())


def test_same_name_rows_become_one_with_averaged_nutrients(db: Session):
    make_food_ref(db, food_ref_id="A02", name="미역국", calories=Decimal("100"))
    make_food_ref(db, food_ref_id="A01", name="미역 국", calories=Decimal("50"))
    # 기준량이 다르면 100g 당으로 맞춘 뒤 평균한다: 30 kcal / 50g = 60 kcal / 100g
    make_food_ref(
        db,
        food_ref_id="A03",
        name="미역_국",
        serving_size=Decimal("50"),
        calories=Decimal("30"),
    )

    _run_migration(db)

    assert _ids(db) == ["A01"]
    merged = db.get(FoodRef, "A01")
    assert merged.name == "미역 국"  # 대표(가장 작은 id) 행의 이름이 남는다
    assert merged.serving_size == Decimal("100")
    assert merged.calories == Decimal("70.000")  # (50 + 100 + 60) / 3


def test_null_nutrient_is_averaged_only_over_rows_that_have_it(db: Session):
    make_food_ref(db, food_ref_id="B01", name="김치찌개", fiber_g=None)
    make_food_ref(db, food_ref_id="B02", name="김치찌개", fiber_g=Decimal("2"))

    _run_migration(db)

    assert db.get(FoodRef, "B01").fiber_g == Decimal("2.000")


def test_general_and_processed_stay_separate(db: Session):
    make_food_ref(db, food_ref_id="C01", name="배추김치", category=FoodCategory.GENERAL)
    make_food_ref(db, food_ref_id="C02", name="배추김치", category=FoodCategory.PROCESSED)
    make_food_ref(db, food_ref_id="C03", name="배추김치", category=FoodCategory.PROCESSED)

    _run_migration(db)

    assert _ids(db) == ["C01", "C02"]
    # 같은 category 안에서는 한 건이라 이름 매칭이 GENERAL 을 고른다
    assert food_crud.find_unique_by_name(db, "배추김치").id == "C01"


def test_references_to_removed_rows_move_to_the_representative(db: Session):
    make_food_ref(db, food_ref_id="D01", name="현미밥")
    make_food_ref(db, food_ref_id="D02", name="현미밥")
    user = make_user(db)
    meal = make_meal(db, user_id=user.id)
    item = make_meal_item(db, meal_id=meal.id, display_name="현미밥", food_ref_id="D02")
    feedback = make_meal_feedback(
        db,
        user_id=user.id,
        meal_id=meal.id,
        suggestions=[
            {"foodName": "현미밥", "advice": "a", "candidateFoodRefId": "D02"},
            {"foodName": "두부", "advice": "b", "candidateFoodRefId": None},
            {"foodName": "없는 음식", "advice": "c", "candidateFoodRefId": "NOPE"},
        ],
    )
    conversation = ChatConversation(user_id=user.id)
    db.add(conversation)
    db.flush()
    message = ChatMessage(
        conversation_id=conversation.id,
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.READY,
        text="추천",
    )
    db.add(message)
    db.flush()
    card = ChatFoodCard(
        message_id=message.id, position=0, food_name="현미밥", advice="a", food_ref_id="D02"
    )
    db.add(card)
    db.flush()

    _run_migration(db)  # FK(RESTRICT) 가 남아 있으면 여기서 DELETE 가 터진다

    assert _ids(db) == ["D01"]
    assert db.get(MealItem, item.id).food_ref_id == "D01"
    assert db.get(ChatFoodCard, card.id).food_ref_id == "D01"
    # 순서와 다른 원소는 그대로 두고 지워진 id 만 바꾼다
    assert db.get(MealFeedback, feedback.id).suggestions == [
        {"foodName": "현미밥", "advice": "a", "candidateFoodRefId": "D01"},
        {"foodName": "두부", "advice": "b", "candidateFoodRefId": None},
        {"foodName": "없는 음식", "advice": "c", "candidateFoodRefId": "NOPE"},
    ]


def test_loader_merges_staged_rows_the_same_way(db: Session):
    """적재 스크립트의 병합 SQL — 마이그레이션과 같은 결과가 나와야 한다."""
    cur = db.connection().connection.cursor()
    cur.execute(
        "CREATE TEMP TABLE food_refs_stage (LIKE food_refs INCLUDING DEFAULTS) ON COMMIT DROP"
    )
    rows = [
        # (id, name, origin_type, serving_size, calories)
        ("E02", "잡곡밥", "가정식", Decimal("100"), Decimal("150")),
        ("E01", "잡곡 밥", "외식", Decimal("200"), Decimal("200")),
        ("E01", "잡곡 밥", "외식", Decimal("200"), Decimal("200")),  # 파일 내 중복 식품코드
        ("E03", "보리밥", "가정식", Decimal("100"), Decimal("140")),
    ]
    for food_id, name, origin_type, serving_size, calories in rows:
        cur.execute(
            "INSERT INTO food_refs_stage (id, name, origin_type, serving_size, calories,"
            " category, dataset_version) VALUES (%s, %s, %s, %s, %s, 'GENERAL', 'test')",
            (food_id, name, origin_type, serving_size, calories),
        )

    merge_stage_into_food_refs(cur)

    assert cur.rowcount == 2
    assert _ids(db) == ["E01", "E03"]
    merged = db.get(FoodRef, "E01")
    assert merged.name == "잡곡 밥"
    assert merged.origin_type is None  # 식품기원명이 갈리면 비운다
    assert merged.serving_size == Decimal("100")
    assert merged.calories == Decimal("125.000")  # (100 + 150) / 2
    assert db.get(FoodRef, "E03").origin_type == "가정식"
