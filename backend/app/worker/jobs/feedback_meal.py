"""`feedback.meal` — 끼니 하나에 대한 피드백 문장을 만든다.

`/short-feedback` `scope=MEAL` → `meal_feedbacks`.

**채점(Q/Q/S)은 이미 끝나 있다.** Rule Engine 이 확인 API 에서 동기로 돌려
`qqs_evaluations` 에 저장한 뒤다. 여기서는 그 점수를 문장으로 옮기기만 한다.
그래서 AI 가 죽어도 점수는 남는다.

이 작업은 **확인 API(`POST /meals/{mealId}/confirm`)가 큐에 넣는다** — 채점과 같은
트랜잭션이다(`services/evaluation/__init__.py::confirm`). 제안 화면을 열 때 넣지 않는
이유는 하루 피드백이 끼니 피드백으로 채워지기 때문이다(6) — 화면을 안 연 끼니가 하루
요약에서 조용히 빠진다.

BE ↔ AI 계약은 `ai-stub/schemas.py` 의 `ShortFeedbackRequest` · `ShortFeedbackResponse` 다.

**끼니 피드백은 그날 앞 끼니를 누적한 문장이다.** 요청의 `previousMeals` 에 그날(KST)
이 끼니보다 먼저 먹은 확정 끼니를 **음식 · 점수로** 싣는다 — 앞 끼니의 문장으로 넘기면
연달아 등록할 때 앞 문장이 아직 만들어지는 중일 수 있어 순서에 묶인다. Q/Q/S 와 영양소는
이 끼니 단독이다. 그래서 가장 최근 끼니 피드백이 곧 그날의 하루 피드백이다 —
쓸 때마다 `daily_feedbacks` 에도 upsert 한다(6). 하루 피드백을 만드는 곳은 여기뿐이다.

**저녁(`DINNER`)은 다음 끼니 추천이 없다.** AI 에 `mealType` 을 넘기고, AI 가 제안을
주더라도 저장하지 않는다(`suggestions = []`).

**식사 안 함(`meals.skipped`)도 여기서 문장을 만든다.** 등록이 곧바로 `EVALUATED` 로 두고
이 작업을 넣는다(`services/meal.py::create_skipped_meal`). 점수 행이 없으므로 기다리지
않고 Q/Q/S 를 모두 null, `items` 를 비워 `skipped: true` 로 보낸다.

순서 (load 1~3 전반 · call_ai 3 · apply 4~6):
  1. 식사를 읽는다. 없거나 지워졌거나 확정 상태가 아니면 AI 를 부르지 않고 끝낸다
  2. `qqs_evaluations` 에서 Q/Q/S 를 읽는다. 없으면 채점이 아직이다 — raise 해서
     재시도에 맡긴다. 식사 안 함은 점수가 없는 게 정상이라 건너뛴다
  3. `items` · 포만감 · 앞선 끼니 컨텍스트를 만들고 AI 를 부른다
  4. 식사를 잠그고 다시 본다 — AI 를 기다리는 사이 지워지거나 고쳐졌거나 다시
     채점됐으면 쓰지 않는다
  5. `meal_feedbacks` 에 upsert
  6. 그날(KST) `daily_feedbacks` 에 upsert — 점수는 늘 그날 끼니 평균으로, 영양 합계는
     그날 끼니 합으로 덮는다. 문장은
     SAFE 이고 그날 이보다 늦게 먹은 확정 끼니가 없을 때만 이 끼니 피드백으로 덮고
     근거 링크(`daily_feedback_sources`)를 더한다
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import KST
from app.crud import daily_feedback as daily_feedback_crud
from app.crud import evaluation as evaluation_crud
from app.crud import feedback as feedback_crud
from app.crud import food as food_crud
from app.crud import meal as meal_crud
from app.crud import satiety as satiety_crud
from app.crud import user_state as user_state_crud
from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask
from app.models.enums import MealStatus, MealType, SafetyStatus
from app.models.evaluation import QQSEvaluation
from app.models.meal import Meal, MealItem
from app.services.meal import item_nutrition, resolved_amount
from app.services.evaluation.stage_profile import profile_for
from app.services.nutrition import resolve_by_food_ref_id
from app.worker.job import Job, Skip

logger = logging.getLogger("worker.feedback_meal")


def _number(value: Decimal | int | None) -> float | None:
    # AI payload 는 JSON 으로 나간다 — Decimal 을 싣지 않는다. None 은 "모른다" 라 그대로 둔다.
    return None if value is None else float(value)


def _kst_day_range(day: date) -> tuple[datetime, datetime]:
    """KST 하루 [00:00, 다음날 00:00). eaten_at 은 UTC 로 저장되므로 UTC 날짜로 자르지 않는다."""
    start = datetime.combine(day, time.min, tzinfo=KST)
    return start, start + timedelta(days=1)


def _round_or_none(value: Decimal | None) -> int | None:
    # Python round(은행가 반올림) — services/dashboard.py 의 _round_or_none 과 같은 규칙.
    return None if value is None else round(value)


def _upsert_daily(
    db: Session,
    *,
    meal: Meal,
    meal_feedback_id: uuid.UUID,
    result: dict[str, Any],
    safety_status: SafetyStatus,
) -> uuid.UUID:
    """이 끼니 피드백을 그날 하루 피드백에 반영한다. 행 id 를 돌려준다.

    점수는 판정과 무관하게 그날 평균으로 덮는다 — Rule Engine 결과라 판정과 상관없이
    쓴다. 문장은 SAFE 일 때만 덮고 근거 링크를 더한다. SAFE 가 아닌 문장으로 덮으면 앞
    끼니까지의 멀쩡한 요약이 사라진다 — 장기 피드백은 SAFE 하루 요약만 근거로 쓴다.

    그날 이 끼니보다 늦게 먹은 확정 끼니가 있으면 문장도 덮지 않는다. 하루 피드백은 가장
    늦은 끼니의 누적 문장이어야 하는데, 앞 끼니를 뒤늦게 입력·재확정하면 작업이 늦게 끝나
    앞 끼니만 본 문장이 저녁 문장을 덮는다.
    """
    feedback_date = meal.eaten_at.astimezone(KST).date()
    range_start, range_end = _kst_day_range(feedback_date)
    averages = daily_feedback_crud.day_score_averages(
        db, user_id=meal.user_id, range_start=range_start, range_end=range_end
    )
    scores = {
        "quantity_score": _round_or_none(averages.quantity),
        "quality_score": _round_or_none(averages.quality),
        "satiety_score": _round_or_none(averages.satiety),
        # 영양 합계도 점수와 같이 그날 전체를 다시 더해 덮는다 — 문장과 무관한 값이다.
        "nutrient_totals": daily_feedback_crud.day_nutrient_totals(
            db, user_id=meal.user_id, range_start=range_start, range_end=range_end
        ),
    }

    if safety_status is not SafetyStatus.SAFE:
        return daily_feedback_crud.upsert_scores(
            db,
            user_id=meal.user_id,
            feedback_date=feedback_date,
            safety_status=safety_status,
            **scores,
        )

    if meal_crud.has_later_evaluated_meal(db, meal=meal, range_end=range_end):
        # 행이 없을 때만 쓰이는 판정이다 — 문장 없는 행을 SAFE 로 만들지 않는다.
        # 늦은 끼니의 작업이 아직 안 끝난 경우라, 그 작업이 문장을 채운다.
        return daily_feedback_crud.upsert_scores(
            db,
            user_id=meal.user_id,
            feedback_date=feedback_date,
            safety_status=SafetyStatus.REVIEW_REQUIRED,
            **scores,
        )

    daily_feedback_id = daily_feedback_crud.upsert(
        db,
        user_id=meal.user_id,
        feedback_date=feedback_date,
        summary=result["body"],
        model_version=result["modelVersion"],
        safety_status=safety_status,
        **scores,
    )
    daily_feedback_crud.add_source(
        db, daily_feedback_id=daily_feedback_id, meal_feedback_id=meal_feedback_id
    )
    return daily_feedback_id


def _skip_reason(meal: Meal | None) -> str | None:
    """이 식사에 피드백을 쓰면 안 되는 이유. 써도 되면 None.

    둘 다 **낡은 작업**이지 실패가 아니다 — raise 하면 재시도가 AI 를 헛되이 부르다 DLQ 로 간다.

    - 없거나 지워졌다 — 쓸 곳이 없다
    - 확정 상태가 아니다 — 음식을 고쳐 재계산 대기로 돌아갔다. 지금 쓰면 고치기 전 끼니를
      보고 쓴 문장이 남는다. 다시 확정하면 그 확정이 새 작업을 넣는다
    """
    if meal is None or meal.deleted_at is not None:
        return "MEAL_NOT_FOUND"
    if meal.status is not MealStatus.EVALUATED:
        return "MEAL_NOT_EVALUATED"
    return None


_SKIPPED_BASIS: tuple[Any, ...] = ("SKIPPED",)
"""식사 안 함 끼니가 딛고 선 채점. 점수 행이 없고, 고칠 수도 다시 확정할 수도 없어
(`services/meal.py::_is_editable`) 바뀔 일이 없다."""


def _basis(db: Session, meal: Meal) -> tuple[Any, ...] | None:
    """문장이 딛고 선 채점. 아직 채점 전이면 None.

    식사 안 함은 채점하지 않으므로 고정값이다 — 점수 행이 없다고 "채점이 아직" 으로 보면
    재시도만 하다 DLQ 로 간다.
    """
    if meal.skipped:
        return _SKIPPED_BASIS
    evaluation = evaluation_crud.get_by_meal(db, meal.id)
    return None if evaluation is None else _scored_as(evaluation)


def _intake_payload(evaluation: QQSEvaluation) -> dict[str, Any] | None:
    """평소 대비 섭취 비율과 **채점 단계의** 권장 구간. 비율이 없으면 None.

    비율만으로는 방향을 모른다 — 같은 95% 도 조정기(60–90%)에서는 초과, 투약 전
    (85–105%)에서는 적정이다. 구간을 같이 보내 AI 가 단계 기준을 복사하지 않게 한다.
    """
    ratio = evaluation.intake_ratio_pct
    if ratio is None:
        return None
    band = profile_for(evaluation.stage_at_evaluation).quantity
    return {
        "ratioPct": float(ratio.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
        "rangePct": [float(band.lo), float(band.hi)],
    }


def _scored_as(evaluation: QQSEvaluation) -> tuple[Any, ...]:
    """문장이 딛고 선 채점 — 쓰기 직전에 이게 그대로인지 본다.

    **id 도 넣는다.** 음식을 고치면 `mark_recalculating` 이 평가 행을 지우고 재확정이
    새로 넣는데, 점수는 같게 나올 수 있다 — Quantity 는 구간 안이면 100 에 머물고,
    체중이 없으면 Quality 는 None 이다. 점수만 비교하면 음식이 바뀐 끼니에 옛 음식으로
    쓴 문장이 들어간다.
    """
    return (
        evaluation.id,
        evaluation.stage_at_evaluation,
        evaluation.quantity_score,
        evaluation.quality_score,
        evaluation.satiety_score,
        # 평소 섭취량만 고쳐 재확정하면 점수는 같아도 비율이 바뀐다 — 문장을 만든 AI 입력이 바뀐 것이다.
        evaluation.intake_ratio_pct,
    )


@dataclass(frozen=True)
class _Ctx:
    """`load` 가 넘기는 값. ORM 객체를 싣지 않는다 — `load` 의 세션은 곧 닫힌다."""

    meal_id: uuid.UUID
    scored_as: tuple[Any, ...]
    """AI 에 넘긴 채점(`_basis`). `apply` 가 쓰기 직전에 이게 그대로인지 본다."""
    request: dict[str, Any]


def _item_payload(db: Session, item: MealItem) -> dict[str, Any]:
    """항목 하나 → `FeedbackItem`. 성분은 BE 가 채운다 — AI 는 음식을 지목만 했다.

    양과 성분은 **화면과 같은 함수로** 만든다(`resolved_amount` · `item_nutrition`).
    여기서 따로 계산하면 사용자가 본 숫자와 AI 가 보고 쓴 숫자가 갈라진다.
    """
    amount_g, amount, unit = resolved_amount(item)
    public_db = (
        None
        if item.food_ref_id is None
        else resolve_by_food_ref_id(db, food_ref_id=item.food_ref_id, amount_g=amount_g)
    )
    nutrition, _source = item_nutrition(item, public_db)
    return {
        "displayName": item.display_name,
        "amount": _number(amount),
        "unit": unit,
        "nutrition": None
        if nutrition is None
        else {
            "kcal": _number(nutrition.kcal),
            "proteinG": _number(nutrition.protein_g),
            "fatG": _number(nutrition.fat_g),
            "carbG": _number(nutrition.carb_g),
            "fiberG": _number(nutrition.fiber_g),
            "sodiumMg": _number(nutrition.sodium_mg),
        },
    }


def _satiety_payload(db: Session, meal_id: uuid.UUID) -> dict[str, Any] | None:
    """`satiety_logs` + `satiety_checkins` → `SatietyContext`. 기록이 하나도 없으면 None."""
    log = satiety_crud.get_by_meal(db, meal_id)
    checkins = satiety_crud.list_checkins(db, meal_id)
    if log is None and not checkins:
        return None
    return {
        "beforePct": log.satiety_before if log else None,
        "afterPct": log.satiety_after if log else None,
        "checkins": [
            {"checkinOffsetHours": row.checkin_offset_hours, "satietyPct": row.satiety_pct}
            for row in checkins
        ],
        "hungerReturnMinutes": log.hunger_return_minutes if log else None,
        "userComment": log.user_comment if log else None,
    }


def _stored_suggestions(db: Session, raw: object) -> list[dict[str, Any]] | None:
    """AI 제안을 저장할 모양으로 바꾼다 — `{foodName, advice, candidateFoodRefId}`.

    **`candidateFoodRefId` 는 `food_refs` 에 실재할 때만 남긴다.** 없는 id 는 읽는 쪽이
    성분을 못 채울 뿐이지만, 남겨 두면 "공공 DB 의 이 음식" 이라는 거짓 링크가 된다.
    제안 자체는 버리지 않는다 — "두부 반 모" 는 성분 숫자 없이도 쓸모가 있다.

    모양이 깨진 원소(dict 가 아닌 것)는 버린다. 문구가 빈 원소는 읽는 쪽
    (`crud/feedback.py::parse_suggestion`)이 거르므로 여기서 또 판단하지 않는다.
    """
    if not isinstance(raw, list):
        return None
    items = [item for item in raw if isinstance(item, dict)]
    # 문자열이 아닌 id 는 없는 것으로 본다 — list 는 해시가 안 돼 집합·`in` 에서 터진다.
    refs = [
        ref if isinstance(ref := item.get("candidateFoodRefId"), str) else None
        for item in items
    ]
    known = food_crud.existing_ids(db, {ref for ref in refs if ref is not None})
    return [
        {
            "foodName": item.get("foodName"),
            "advice": item.get("advice"),
            "candidateFoodRefId": ref if ref in known else None,
        }
        for item, ref in zip(items, refs, strict=True)
    ]


def _meal_totals(meal: Meal) -> dict[str, Decimal | None]:
    """`meals.total_*` 를 `NUTRIENT_TOTAL_COLUMNS` 키로 꺼낸다 — 하루 합과 같은 모양."""
    return {name: getattr(meal, name) for name in daily_feedback_crud.NUTRIENT_TOTAL_COLUMNS}


def _totals_payload(totals: dict[str, Decimal | None]) -> dict[str, float | None] | None:
    """`total_*` 합계 → `Nutrition`. 전부 비었으면 None — 0 이면 "아무것도 안 먹었다" 로 읽힌다.

    끼니 합계(`meals.total_*`)와 하루 합계(`daily_feedbacks.total_*`)가 같은 키라 함께 쓴다.
    """
    values = tuple(totals[name] for name in daily_feedback_crud.NUTRIENT_TOTAL_COLUMNS)
    if all(value is None for value in values):
        return None
    kcal, protein_g, fat_g, carb_g, fiber_g, sodium_mg = values
    return {
        "kcal": _number(kcal),
        "proteinG": _number(protein_g),
        "fatG": _number(fat_g),
        "carbG": _number(carb_g),
        "fiberG": _number(fiber_g),
        "sodiumMg": _number(sodium_mg),
    }


def _previous_meals_payload(db: Session, meal: Meal) -> list[dict[str, Any]]:
    """그날(KST) 이 끼니보다 먼저 먹은 확정 끼니 → `PreviousMeal[]`. 먹은 순서다.

    **문장이 아니라 음식 · 점수 · 영양 합계를 싣는다** — 앞 끼니 문장은 아직 만들어지는
    중일 수 있다(모듈 독스트링). 음식은 이름 · 양만 싣고, 성분은 확정이 남긴 끼니 합계
    (`meals.total_*` — AI 입력용 스냅샷)로 대신한다.
    """
    range_start, _ = _kst_day_range(meal.eaten_at.astimezone(KST).date())
    rows = meal_crud.list_earlier_evaluated_meals(db, meal=meal, range_start=range_start)
    previous = []
    for earlier, quantity, quality, satiety in rows:
        items = []
        for item in earlier.items:
            _amount_g, amount, unit = resolved_amount(item)
            items.append(
                {"displayName": item.display_name, "amount": _number(amount), "unit": unit}
            )
        previous.append(
            {
                "mealType": earlier.meal_type.value,
                "skipped": earlier.skipped,
                # 식사 안 함 · 채점 못 한 축은 null 이다.
                "qqs": {
                    "quantity": _number(quantity),
                    "quality": _number(quality),
                    "satiety": _number(satiety),
                },
                "items": items,
                "nutritionTotals": _totals_payload(_meal_totals(earlier)),
            }
        )
    return previous


def _daily_totals_payload(db: Session, meal: Meal) -> dict[str, float | None] | None:
    """그날(KST) 영양 합 → `Nutrition`. `daily_feedbacks.total_*` 에 들어갈 값과 같다.

    **행을 읽지 않고 같은 함수로 다시 더한다.** 그 행은 이 작업의 `apply` 가
    (`_upsert_daily`) 갱신하므로 지금 읽으면 방금 확정한 이 끼니가 빠진 옛 합이다. 같은
    `day_nutrient_totals` 를 쓰니 숫자는 이 작업이 끝난 뒤 행에 남는 값과 같다.

    ⚠️ 그날 **전체** 합이다 — 이 끼니보다 늦게 먹은 확정 끼니가 있으면(앞 끼니를 뒤늦게
    입력) 그 끼니도 들어간다. 행과 같은 값을 주려고 자르지 않는다.
    """
    range_start, range_end = _kst_day_range(meal.eaten_at.astimezone(KST).date())
    return _totals_payload(
        daily_feedback_crud.day_nutrient_totals(
            db, user_id=meal.user_id, range_start=range_start, range_end=range_end
        )
    )


def load(db: Session, task: ClaimedTask) -> _Ctx | Skip:
    """식사·평가를 읽고 AI 요청을 만든다. 평가가 아직 없으면 raise — 재시도에 맡긴다."""
    meal_id = uuid.UUID(task.payload["mealId"])

    meal = meal_crud.get_meal_for_worker(db, meal_id)
    if (reason := _skip_reason(meal)) is not None:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=%s", meal_id, reason)
        return Skip({"skipped": reason})

    if meal.skipped:
        # 채점하지 않은 끼니다. 단계는 먹을 때의 스냅샷이다 — 채점이 쓰는 단계도 같은
        # 스냅샷이다(`services/evaluation::_stage_of`).
        stage = meal_crud.get_stage(db, meal.medication_snapshot_id)
        qqs: dict[str, float | None] = {"quantity": None, "quality": None, "satiety": None}
        items: list[dict[str, Any]] = []
        satiety = None
        intake = None
        basis = _SKIPPED_BASIS
    else:
        evaluation = evaluation_crud.get_by_meal(db, meal.id)
        if evaluation is None:
            raise LookupError(f"meal {meal_id} 의 Q/Q/S 평가가 아직 없다")
        # 채점 시점의 단계다. 점수와 같은 기준으로 문장을 써야 한다.
        stage = evaluation.stage_at_evaluation
        # 채점 못 한 축은 null 이다 — 0 으로 채우면 "못 쟀다" 가 "바닥이다" 가 된다.
        qqs = {
            "quantity": _number(evaluation.quantity_score),
            "quality": _number(evaluation.quality_score),
            "satiety": _number(evaluation.satiety_score),
        }
        items = [_item_payload(db, item) for item in meal.items]
        satiety = _satiety_payload(db, meal.id)
        intake = _intake_payload(evaluation)
        basis = _scored_as(evaluation)

    return _Ctx(
        meal_id=meal.id,
        scored_as=basis,
        request={
            "scope": "MEAL",
            "userId": str(meal.user_id),
            "stage": stage.value,
            "qqs": qqs,
            # 평소 대비 섭취 비율 + 단계 권장 구간. Quantity 점수의 방향(과식 · 소식) 근거다.
            "intake": intake,
            "mealId": str(meal.id),
            # 저녁이면 AI 는 다음 끼니 추천을 비운다.
            "mealType": meal.meal_type.value,
            "skipped": meal.skipped,
            "items": items,
            "satiety": satiety,
            "previousMeals": _previous_meals_payload(db, meal),
            "dailyNutritionTotals": _daily_totals_payload(db, meal),
            # 먹을 때의 체중(없으면 가장 최근). Quality 채점이 쓰는 값과 같다. 기록이 없으면 null.
            "weightKg": _number(
                user_state_crud.get_weight_at(db, user_id=meal.user_id, at=meal.eaten_at)
            ),
        },
    )


def call_ai(ctx: _Ctx, ai: AiClient) -> dict[str, Any]:
    return ai.short_feedback(ctx.request)


def apply(db: Session, task: ClaimedTask, ctx: _Ctx, result: dict[str, Any]) -> dict[str, Any]:
    """식사를 잠그고 다시 본 뒤 upsert 한다."""
    # AI 를 부르는 동안(최대 45초) 잠그지 않았다 — 그 사이 사용자가 지우거나 고쳤을 수 있다.
    # 워커 루프에서는 새 세션이라 identity map 이 비어 있지만, `JOB.run_inline` 은 `load` 와
    # 같은 세션을 쓴다. 그래서 다시 읽기 전에 비운다. 객체 하나가 아니라 전부다 — 그 사이 평가
    # 행이 지워졌으면 옛 객체는 이미 세션에서 떨어져 있을 수 있다.
    db.expire_all()
    meal = meal_crud.lock_meal_for_worker(db, ctx.meal_id)
    if (reason := _skip_reason(meal)) is not None:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=%s", ctx.meal_id, reason)
        return {"skipped": reason}

    # 상태만으로는 부족하다 — 포만감만 고쳐 재확정하면 EVALUATED 그대로다. 그 재확정이
    # 넣은 작업이 다른 워커에서 먼저 끝날 수 있어서, 여기서 쓰면 옛 점수로 쓴 문장이
    # 새 문장을 덮고 영영 남는다. 확정이 식사 행을 먼저 잠그므로(`services/evaluation`)
    # 잠금을 얻은 지금 읽는 평가는 커밋이 끝난 값이다.
    if _basis(db, meal) != ctx.scored_as:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=EVALUATION_CHANGED", ctx.meal_id)
        return {"skipped": "EVALUATION_CHANGED"}

    # 저녁은 다음 끼니 추천이 없다 — AI 가 주더라도 싣지 않는다(FE 화면 합의).
    suggestions = (
        []
        if meal.meal_type is MealType.DINNER
        else _stored_suggestions(db, result.get("suggestions"))
    )
    # safetyStatus 는 AI 가 준 그대로 저장한다. SAFE 로 올리지 않는다 (규칙 1).
    safety_status = SafetyStatus(result["safetyStatus"])
    meal_feedback_id = feedback_crud.upsert(
        db,
        user_id=meal.user_id,
        meal_id=meal.id,
        body=result["body"],
        reasoning=result.get("reasoning"),
        suggestions=suggestions,
        model_version=result["modelVersion"],
        safety_status=safety_status,
    )

    daily_feedback_id = _upsert_daily(
        db,
        meal=meal,
        meal_feedback_id=meal_feedback_id,
        result=result,
        safety_status=safety_status,
    )

    # 커밋하지 않는다. 큐가 DONE 과 함께 한 번에 커밋한다(`queue.complete`).

    # 로그·반환값(task_queue.result)에는 식별자와 개수만 — 음식명·본문·제안 문구는
    # 남기지 않는다 (규칙 6).
    logger.info(
        "끼니 피드백 완료 mealId=%s safetyStatus=%s",
        ctx.meal_id,
        result["safetyStatus"],
    )

    return {
        "mealFeedbackId": str(meal_feedback_id),
        "dailyFeedbackId": str(daily_feedback_id),
        "suggestionCount": len(suggestions or []),
    }


JOB = Job(load=load, call_ai=call_ai, apply=apply)
