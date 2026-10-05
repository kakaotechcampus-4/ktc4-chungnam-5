"""`meal.analyze` 워커 — AI 인식 결과를 meal_items 에 넣고 `ANALYZING → REVIEW_REQUIRED`.

틀리기 쉬운 것:
  - 지운 식사·이미 끝난 식사·사용자가 고치는 중인 식사를 분석해 항목을 덮는 것
  - 없는 `candidateFoodRefId` 를 그대로 넣어 FK 로 커밋이 통째로 깨지는 것
  - 사용자가 직접 넣은 항목(`source=USER`)까지 지우는 것
  - "2개" 같은 양을 g 으로 지어내는 것
  - AI 가 끝내 실패했는데 식사가 `ANALYZING` 에 영원히 남는 것
  - 워커 안에서 커밋하는 것 — 큐가 DONE 과 함께 커밋해야 실패 시 통째로 롤백된다
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, update

from app.infra.queue import ClaimedTask, QueueSettings
from app.models.enums import MealItemSource, MealStatus, MealType
from app.models.meal import Meal, MealItem, UserCorrection
from app.tests.factories import make_food_ref, make_meal, make_meal_item, make_user
from app.worker.dispatch import handle
from app.worker.jobs import analyze_meal

LAST_ATTEMPT = QueueSettings().QUEUE_MAX_ATTEMPTS - 1

# 세 단계를 세션 하나로 이어 돈다. 시나리오 단언은 3단계 분리 전과 같다 — 동작이 바뀌지
# 않았다는 증거다.
run = analyze_meal.JOB.run_inline


# ─────────────────────────── Fake ───────────────────────────


def _item(
    name: str = "참치김밥",
    amount: float = 250,
    unit: str = "g",
    confidence: float = 0.62,
    food_ref_id: str | None = None,
    clarify_question: str | None = None,
) -> dict[str, Any]:
    """`ai-stub/schemas.py` RecognizedItem 모양."""
    return {
        "originalFoodName": name,
        "estimatedAmount": amount,
        "unit": unit,
        "confidence": confidence,
        "candidateFoodRefId": food_ref_id,
        "clarifyQuestion": clarify_question,
    }


class FakeAi:
    """`/analyze-meal` 호출을 기록하고 정해진 응답을 돌려주는 가짜 AI."""

    def __init__(
        self,
        *,
        items: list[dict[str, Any]] | None = None,
        safety_status: str = "SAFE",
        error: Exception | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        self.items = [_item()] if items is None else items
        self.safety_status = safety_status
        self.error = error
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response
        return {
            "mealId": payload["mealId"],
            "modelVersion": "stub-vision-0",
            "safetyStatus": self.safety_status,
            "items": self.items,
        }

    def short_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("meal.analyze 가 short_feedback 을 불렀다")

    def long_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("meal.analyze 가 long_feedback 을 불렀다")


class FakeStorage:
    def __init__(self) -> None:
        self.url_calls: list[str] = []

    def save(self, key: str, data: bytes) -> None:
        raise AssertionError("워커는 파일을 저장하지 않는다")

    def url(self, key: str) -> str:
        self.url_calls.append(key)
        return f"https://signed.example/{key}?sig=fresh"


@pytest.fixture
def storage(monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(analyze_meal, "build_file_storage", lambda: fake)
    return fake


# ─────────────────────────── helper ───────────────────────────


def _analyzing_meal(db, *, image_key: str | None = None, raw_text: str | None = "김밥 한 줄"):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.ANALYZING)
    meal.image_key = image_key
    meal.raw_text = raw_text
    db.flush()
    return meal


def _task(meal: Meal, *, attempts: int = 0) -> ClaimedTask:
    """`services/meal.py::create_meal` 이 넣는 payload 그대로."""
    return ClaimedTask(
        id=uuid.uuid4(),
        type="meal.analyze",
        payload={
            "mealId": str(meal.id),
            "mealType": meal.meal_type.value,
            "eatenAt": meal.eaten_at.isoformat(),
            "stage": "MAINTENANCE",
            "imageKey": meal.image_key,
            "rawText": meal.raw_text,
        },
        attempts=attempts,
    )


def _items(db, meal_id: uuid.UUID) -> list[MealItem]:
    # 프로덕션에선 큐의 커밋이 flush 한다. flush 없이 expire 하면 대기 중인 변경이 버려진다.
    db.flush()
    db.expire_all()
    return list(
        db.execute(
            select(MealItem).where(MealItem.meal_id == meal_id).order_by(MealItem.original_food_name)
        ).scalars()
    )


def _status(db, meal: Meal) -> MealStatus:
    db.flush()
    db.expire_all()
    return db.get(Meal, meal.id).status


# ─────────────────────────── 정상 ───────────────────────────


def test_recognized_items_are_saved_and_meal_moves_to_review_required(db, storage):
    """A1: 인식 결과가 meal_items 로 들어가고 식사는 REVIEW_REQUIRED 가 된다."""
    meal = _analyzing_meal(db)
    ai = FakeAi(items=[_item("참치김밥", 250, "g", 0.62)])

    run(db, _task(meal), ai)

    assert _status(db, meal) is MealStatus.REVIEW_REQUIRED
    [item] = _items(db, meal.id)
    assert item.original_food_name == "참치김밥"
    assert item.display_name == "참치김밥"
    assert item.estimated_amount == Decimal("250.00")
    assert item.estimated_unit == "g"
    assert item.estimated_amount_g == Decimal("250.00")
    assert item.confidence == Decimal("0.620")
    assert item.source is MealItemSource.MODEL
    # 확인 전이다 — 사용자가 말한 양은 아직 없다
    assert item.confirmed_amount is None
    assert item.confirmed_amount_g is None


def test_ai_request_follows_the_contract(db, storage):
    """A2: AI 요청은 `AnalyzeMealRequest` 모양이다. 이미지가 없으면 imageUrl 은 None."""
    meal = _analyzing_meal(db, raw_text="김밥 한 줄")
    ai = FakeAi()

    run(db, _task(meal), ai)

    [payload] = ai.calls
    assert payload == {
        "mealId": str(meal.id),
        "mealType": meal.meal_type.value,
        "eatenAt": meal.eaten_at.isoformat(),
        "stage": "MAINTENANCE",
        "imageUrl": None,
        "rawText": "김밥 한 줄",
    }
    assert storage.url_calls == []


def test_image_url_is_issued_fresh_from_image_key(db, storage):
    """A3: payload 엔 imageKey 만 있다 — URL 은 AI 를 부르기 직전에 새로 발급한다."""
    meal = _analyzing_meal(db, image_key="meals/abc.jpg", raw_text=None)
    ai = FakeAi()

    run(db, _task(meal), ai)

    assert storage.url_calls == ["meals/abc.jpg"]
    assert ai.calls[0]["imageUrl"] == "https://signed.example/meals/abc.jpg?sig=fresh"


def test_unconvertible_amount_keeps_number_and_unit_but_no_grams(db, storage):
    """A4: "2개" 는 g 으로 지어내지 않는다 — 숫자·단위는 남고 estimated_amount_g 만 None."""
    meal = _analyzing_meal(db)
    ai = FakeAi(items=[_item("삶은 계란", 2, "개", 0.96)])

    run(db, _task(meal), ai)

    [item] = _items(db, meal.id)
    assert item.estimated_amount == Decimal("2.00")
    assert item.estimated_unit == "개"
    assert item.estimated_amount_g is None


def test_existing_food_ref_is_linked_and_unknown_one_becomes_null(db, storage):
    """A5: food_refs 에 있는 후보만 연결한다. 없는 id 를 넣으면 FK 로 커밋이 통째로 깨진다."""
    make_food_ref(db, food_ref_id="D000123", name="참치김밥")
    meal = _analyzing_meal(db)
    ai = FakeAi(
        items=[
            _item("참치김밥", food_ref_id="D000123"),
            _item("삶은 계란", 2, "개", food_ref_id="NOT_IN_DB"),
        ]
    )

    run(db, _task(meal), ai)
    db.flush()  # FK 위반이 있으면 여기서 터진다

    by_name = {item.original_food_name: item for item in _items(db, meal.id)}
    assert by_name["참치김밥"].food_ref_id == "D000123"
    assert by_name["삶은 계란"].food_ref_id is None


def test_raw_ai_result_keeps_the_original_item_and_response_meta(db, storage):
    """A6: raw_ai_result 는 AI 가 원래 뭐라고 했는지의 보관 자리다 — 항목 원본 + 모델·안전 상태."""
    meal = _analyzing_meal(db)
    original = _item("참치김밥", clarify_question="김밥 속재료가 참치가 맞나요?")
    ai = FakeAi(items=[original], safety_status="REVIEW_REQUIRED")

    run(db, _task(meal), ai)

    [item] = _items(db, meal.id)
    assert item.raw_ai_result == {
        "modelVersion": "stub-vision-0",
        "safetyStatus": "REVIEW_REQUIRED",
        "item": original,
    }


def test_safety_status_does_not_decide_the_meal_status(db, storage):
    """A7: safetyStatus 는 식사 상태 판단에 쓰지 않는다 — 항목이 있으면 BLOCKED 여도 확인 화면으로 간다."""
    meal = _analyzing_meal(db)

    run(db, _task(meal), FakeAi(items=[_item()], safety_status="BLOCKED"))

    assert _status(db, meal) is MealStatus.REVIEW_REQUIRED
    assert len(_items(db, meal.id)) == 1


def test_returns_summary_for_task_result(db, storage):
    """A8: task_queue.result 에는 식별자·상태·개수만 — 음식명은 남기지 않는다 (규칙 6)."""
    meal = _analyzing_meal(db)

    result = run(db, _task(meal), FakeAi(items=[_item("a"), _item("b")]))

    assert result == {"mealId": str(meal.id), "status": "REVIEW_REQUIRED", "itemCount": 2}


# ─────────────────────────── 기존 항목 ───────────────────────────


def test_previous_model_items_are_replaced_but_user_items_are_kept(db, storage):
    """A9: 기존 MODEL 항목은 지우고 새로 넣는다. USER 항목은 사용자가 직접 넣은 것이라 남긴다."""
    meal = _analyzing_meal(db)
    old = make_meal_item(db, meal_id=meal.id, display_name="옛 인식")
    db.add(UserCorrection(meal_item_id=old.id, original_value={"a": 1}, corrected_value={"a": 2}))
    make_meal_item(
        db,
        meal_id=meal.id,
        display_name="사용자 추가",
        source=MealItemSource.USER,
        confidence=None,
        estimated_amount=None,
        estimated_unit=None,
        confirmed_amount=Decimal("1"),
        confirmed_unit="개",
    )
    db.flush()

    run(db, _task(meal), FakeAi(items=[_item("새 인식")]))
    db.flush()

    names = sorted(item.original_food_name for item in _items(db, meal.id))
    assert names == ["사용자 추가", "새 인식"]


# ─────────────────────────── 건너뛰기 (멱등성) ───────────────────────────


def test_missing_meal_raises(db, storage):
    """A10: 식사가 없으면 raise — 조용히 DONE 으로 끝내면 원인을 추적할 수 없다."""
    # 세션에 붙이지 않은 객체 — DB 에는 이 id 의 행이 없다
    ghost = Meal(id=uuid.uuid4(), meal_type=MealType.LUNCH, eaten_at=datetime.now(UTC))
    ai = FakeAi()

    with pytest.raises(ValueError, match="식사를 찾을 수 없다"):
        run(db, _task(ghost), ai)
    assert ai.calls == []


def test_soft_deleted_meal_is_skipped(db, storage):
    """A11: 사용자가 지운 식사는 분석하지 않는다 — meals 는 soft delete 라 행이 남아 있다."""
    meal = _analyzing_meal(db)
    meal.deleted_at = datetime.now(UTC)
    db.flush()
    ai = FakeAi()

    result = run(db, _task(meal), ai)

    assert result is None
    assert ai.calls == []
    assert _items(db, meal.id) == []
    assert _status(db, meal) is MealStatus.ANALYZING


@pytest.mark.parametrize(
    "status", [MealStatus.REVIEW_REQUIRED, MealStatus.EVALUATED, MealStatus.FAILED]
)
def test_meal_not_analyzing_is_skipped(db, storage, status):
    """A12: 이미 분석이 끝난 식사는 건너뛴다 — 같은 작업이 두 번 등록된 경우."""
    meal = _analyzing_meal(db)
    meal.status = status
    db.flush()
    ai = FakeAi()

    assert run(db, _task(meal), ai) is None
    assert ai.calls == []
    assert _status(db, meal) is status


def test_recalculating_meal_is_skipped(db, storage):
    """A13: `ANALYZING` + `is_recalculation=True` 는 사용자가 고친 뒤 재계산 대기다 — 분석이 아니다.

    여기서 분석하면 사용자가 고친 항목을 AI 결과로 덮는다.
    """
    meal = _analyzing_meal(db)
    meal.is_recalculation = True
    make_meal_item(db, meal_id=meal.id, display_name="사용자가 고친 것")
    db.flush()
    ai = FakeAi()

    assert run(db, _task(meal), ai) is None
    assert ai.calls == []
    assert [i.display_name for i in _items(db, meal.id)] == ["사용자가 고친 것"]


def test_second_run_after_success_is_a_no_op(db, storage):
    """A14: 두 번 돌아도 항목이 쌓이지 않는다."""
    meal = _analyzing_meal(db)
    ai = FakeAi(items=[_item("참치김밥")])

    run(db, _task(meal), ai)
    run(db, _task(meal), ai)

    assert len(ai.calls) == 1
    assert len(_items(db, meal.id)) == 1


# ─────────────────────────── 실패 ───────────────────────────


def test_empty_recognition_marks_meal_failed(db, storage):
    """A15: 음식을 하나도 못 알아보면 FAILED — 확인할 음식이 없다(`_is_editable` 과 같은 뜻)."""
    meal = _analyzing_meal(db)

    result = run(db, _task(meal), FakeAi(items=[]))

    assert _status(db, meal) is MealStatus.FAILED
    assert _items(db, meal.id) == []
    assert result == {"mealId": str(meal.id), "status": "FAILED", "itemCount": 0}


def test_ai_error_before_last_attempt_propagates(db, storage):
    """A16: 마지막 시도 전에는 예외를 올린다 — 큐가 롤백하고 재시도한다."""
    meal = _analyzing_meal(db)

    with pytest.raises(RuntimeError, match="AI 다운"):
        run(db, _task(meal, attempts=LAST_ATTEMPT - 1), FakeAi(error=RuntimeError("AI 다운")))

    assert _status(db, meal) is MealStatus.ANALYZING


def test_ai_error_on_last_attempt_marks_meal_failed(db, storage):
    """A17: 마지막 시도에서도 실패하면 FAILED 로 끝낸다 — 안 그러면 식사가 영원히 ANALYZING 이다."""
    meal = _analyzing_meal(db)

    result = run(db, _task(meal, attempts=LAST_ATTEMPT), FakeAi(error=RuntimeError("AI 다운")))

    assert _status(db, meal) is MealStatus.FAILED
    assert _items(db, meal.id) == []
    assert result == {"mealId": str(meal.id), "status": "FAILED", "itemCount": 0}


def test_non_retryable_ai_error_marks_meal_failed_on_the_first_attempt(db, storage):
    """AI 가 요청을 거부하면(4xx) 재시도해도 같다 — 첫 시도에서 바로 FAILED 로 끝낸다."""
    from app.infra.ai import AiRequestRejected

    meal = _analyzing_meal(db)
    error = AiRequestRejected(422, "/analyze-meal")

    result = run(db, _task(meal, attempts=0), FakeAi(error=error))

    assert _status(db, meal) is MealStatus.FAILED
    assert result == {"mealId": str(meal.id), "status": "FAILED", "itemCount": 0}


def test_malformed_response_is_treated_as_ai_failure(db, storage):
    """A18: 계약과 다른 응답도 AI 실패다 — 마지막 전엔 raise, 마지막엔 FAILED. 반쯤 넣지 않는다."""
    meal = _analyzing_meal(db)
    broken = FakeAi(response={"mealId": str(meal.id), "items": [{"unit": "g"}]})

    with pytest.raises(Exception):
        run(db, _task(meal, attempts=0), broken)
    assert _items(db, meal.id) == []

    run(db, _task(meal, attempts=LAST_ATTEMPT), broken)
    assert _status(db, meal) is MealStatus.FAILED
    assert _items(db, meal.id) == []


# ─────────────────────────── 트랜잭션 · 라우팅 ───────────────────────────


def test_worker_does_not_commit(db, storage, monkeypatch):
    """A19: 워커는 커밋하지 않는다 — 큐가 DONE 과 함께 커밋해야 실패 시 도메인 변경까지 롤백된다."""
    meal = _analyzing_meal(db)
    commits: list[None] = []
    monkeypatch.setattr(db, "commit", lambda: commits.append(None))

    run(db, _task(meal), FakeAi())
    run(db, _task(_analyzing_meal(db), attempts=LAST_ATTEMPT), FakeAi(error=RuntimeError("x")))

    assert commits == []


def test_dispatch_routes_meal_analyze_to_this_job(db, storage):
    """A20: dispatch 가 meal.analyze 를 이 핸들러로 보낸다."""
    meal = _analyzing_meal(db)

    handle(db, _task(meal), FakeAi())

    assert _status(db, meal) is MealStatus.REVIEW_REQUIRED


# ─────────────────────────── 리뷰 반영: 식사가 ANALYZING 에 멈추지 않는다 ───────────────────────────


@pytest.mark.parametrize(
    "bad_item",
    [
        pytest.param(_item(name="가" * 256), id="name-over-255"),  # String(255)
        pytest.param(_item(unit="공기(약 210g, 흰쌀밥 기준)" + "x" * 20), id="unit-over-32"),  # String(32)
        pytest.param(_item(name="김\x00밥"), id="nul-in-name"),  # Postgres text 가 거부한다
        pytest.param(_item(food_ref_id=123), id="food-ref-id-not-str"),  # varchar = integer 오류
        pytest.param(_item(confidence=1.5), id="confidence-over-1"),
        pytest.param(_item(amount=-1), id="negative-amount"),
        pytest.param(_item(amount=float("nan")), id="nan-amount"),
    ],
)
def test_contract_violation_is_ai_failure_not_db_error(db, storage, bad_item):
    """A21: 계약과 다른 항목은 DB 에 닿기 전에 걸러 AI 실패로 다룬다 — 마지막 전엔 raise, 마지막엔 FAILED.

    flush 에서 터지면 트랜잭션이 aborted 가 돼 FAILED 로 바꿀 기회도 사라진다.
    """
    meal = _analyzing_meal(db)
    ai = FakeAi(items=[bad_item])

    with pytest.raises(Exception):
        run(db, _task(meal, attempts=0), ai)

    result = run(db, _task(meal, attempts=LAST_ATTEMPT), ai)

    assert result["status"] == "FAILED"
    assert _status(db, meal) is MealStatus.FAILED
    assert _items(db, meal.id) == []


def test_db_error_while_writing_on_last_attempt_marks_meal_failed(db, storage):
    """A22: 검증을 통과해도 쓰기에서 DB 오류가 나면 마지막 시도에선 FAILED 로 끝낸다.

    검증하지 않는 필드(`clarifyQuestion`)의 NUL 은 raw_ai_result(jsonb)에서만 터진다 —
    모르는 DB 오류의 대표다. 세션은 이후에도 쓸 수 있어야 큐가 DONE 을 커밋한다.
    """
    meal = _analyzing_meal(db)
    ai = FakeAi(items=[_item(clarify_question="속재료\x00가")])

    result = run(db, _task(meal, attempts=LAST_ATTEMPT), ai)

    assert result == {"mealId": str(meal.id), "status": "FAILED", "itemCount": 0}
    assert _status(db, meal) is MealStatus.FAILED  # 세션이 살아 있다
    assert _items(db, meal.id) == []


def test_db_error_while_writing_before_last_attempt_propagates(db, storage):
    """A23: 마지막 전 시도의 DB 오류는 그대로 올린다 — 큐가 롤백하고 재시도한다."""
    meal = _analyzing_meal(db)
    ai = FakeAi(items=[_item(clarify_question="속재료\x00가")])

    with pytest.raises(Exception):
        run(db, _task(meal, attempts=0), ai)


def test_meal_deleted_during_ai_call_is_not_written(db, storage):
    """A24: AI 를 부르는 동안(최대 45초) 사용자가 식사를 지우면 항목을 쓰지 않는다.

    처음 읽을 때는 살아 있었다 — 쓰기 직전에 다시 읽어 확인해야 한다.
    """
    meal = _analyzing_meal(db)

    class DeletingAi(FakeAi):
        def analyze_meal(self, payload):
            # 다른 요청이 커밋한 soft delete 를 흉내 낸다 — ORM 객체는 모른다.
            db.execute(
                update(Meal).where(Meal.id == meal.id).values(deleted_at=datetime.now(UTC))
            )
            return super().analyze_meal(payload)

    result = run(db, _task(meal), DeletingAi())

    assert result is None
    assert _items(db, meal.id) == []
    assert _status(db, meal) is MealStatus.ANALYZING


# ─────────────────────────── 3단계 계약 ───────────────────────────


def test_load_hands_over_plain_data_not_orm_objects(db, storage):
    """`load` 의 세션은 큐가 닫는다. ctx 에 ORM 객체가 실리면 `apply` 에서 못 쓴다."""
    from app.db.base import Base

    meal = _analyzing_meal(db)

    ctx = analyze_meal.load(db, _task(meal))

    assert ctx.meal_id == meal.id
    assert not any(isinstance(value, Base) for value in vars(ctx).values())


def test_load_skips_without_calling_ai(db, storage):
    from app.worker.job import Skip

    meal = _analyzing_meal(db)
    meal.status = MealStatus.REVIEW_REQUIRED
    db.flush()

    assert analyze_meal.load(db, _task(meal)) == Skip(None)


def test_ai_error_on_last_attempt_skips_a_meal_deleted_meanwhile(db, storage):
    """AI 가 끝내 실패했는데 그사이 사용자가 지웠으면 FAILED 로 덮지 않는다."""
    meal = _analyzing_meal(db)
    ctx = analyze_meal.load(db, _task(meal))
    db.execute(update(Meal).where(Meal.id == meal.id).values(deleted_at=datetime.now(UTC)))

    result = analyze_meal.on_ai_error(db, _task(meal, attempts=LAST_ATTEMPT), ctx, RuntimeError("AI 다운"))

    assert result is None
