"""식사 확정·평가 조회.

`POST /meals/{mealId}/confirm` 과 `GET /meals/{mealId}/evaluation` 의 도메인 로직.
채점 자체는 순수 함수(`rule_engine`)가 하고 여기서는 **입력을 모아 주고 결과를
저장**한다.

## 여기서 비동기 작업을 만들지 않는다

Q/Q/S 는 순수 함수라 0.01 초면 끝난다(절대 규칙 2). 확정은 200 으로 즉답한다 —
명세의 `202 + 폴링` 은 `POST /meals`(사진 인식) 자리다. 피드백 **문장**은 AI 가
따로 만들고, 그래서 응답의 `feedbackStatus` 가 `PENDING` 으로 나간다.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, NamedTuple

from sqlalchemy.orm import Session

from app.crud import evaluation as evaluation_crud
from app.crud import feedback as feedback_crud
from app.crud import meal as meal_crud
from app.crud import medication as medication_crud
from app.crud import satiety as satiety_crud
from app.crud import user as user_crud
from app.crud import user_state as user_state_crud
from app.crud.evaluation import NutrientTotals
from app.models.enums import (
    FeedbackStatus,
    MealStatus,
    MedicationStage,
    NutrientCode,
    NutrientState,
    NutritionSource,
)
from app.models.meal import Meal
from app.schemas.evaluation import (
    EvaluationEvidence,
    MealConfirmRequest,
    MealConfirmResponse,
    MealEvaluationResponse,
    NutrientRow,
    QqsScores,
)
from app.models.evaluation import QQSEvaluation
from app.services.evaluation.rule_engine import MealTargets, Scores, evaluate, meal_targets
from app.services.evaluation.stage_profile import emphasis_for, profile_for


class MealNotFoundError(Exception):
    """없거나, 남의 것이거나, 이미 삭제된 식사."""


class MealNotConfirmableError(Exception):
    """지금 상태로는 확정할 수 없는 식사."""


class EvaluationNotFoundError(Exception):
    """아직 확정되지 않아 평가가 없는 식사. 명세의 `NOT_CONFIRMED` 다."""


class EvaluationResult(NamedTuple):
    """응답 + 성분 합계가 완전한지.

    둘 중 하나라도 0 이 아니면 `nutrients[].current` 가 **일부만 더한 값**이다.
    숫자만 내보내면 사용자는 적게 나온 단백질을 믿고 다음 끼니를 조절한다.

    **사유를 나눠 싣는 이유**: 안내가 달라야 한다. 성분을 못 구한 건 영양정보를
    직접 넣어 풀 수 있지만, 양을 모르는 건 그 화면에서 못 고친다 — 같은 메시지로
    묶으면 사용자를 막다른 길로 보낸다.
    """

    view: MealEvaluationResponse | MealConfirmResponse
    unmatched_items: int
    """성분을 못 구한 항목 수 — 영양정보를 직접 넣으면 풀린다."""
    items_without_amount: int
    """양을 몰라 빠진 항목 수 — 영양정보를 넣어도 안 풀린다. g 으로 고쳐야 한다."""


DB_SOURCE: Final = "식품안전나라 식품영양성분DB"
STAGE_RULE_VERSION: Final = "v2"
WEIGHT_PROFILE_VERSION: Final = "v2"
"""명세 `evidence` 의 상수들. 채점 기준선(`stage_profile`)을 바꾸면 올린다.

v2: Quantity · Quality 채점 도입 (`docs/be-qqs-scoring-rule.md`).
"""

_CONFIRMABLE: Final = frozenset({MealStatus.REVIEW_REQUIRED, MealStatus.EVALUATED})
"""상태만 보고 확정 가능한 것들. `_is_confirmable` 이 쓴다.

`EVALUATED` 를 포함하는 건 **재확정**을 허용하기 위해서다 — 사용자가 음식을
고치면 점수가 다시 매겨져야 하고, `qqs_evaluations` 가 식사당 1행(upsert)인 것도
그 전제다.

🔗 **재확정은 `meal_feedbacks` 도 무효화한다** — `_is_stale_feedback`(아래) 이 판정하고
`crud/feedback.py::invalidate_by_meal` 이 내용을 비운다. 점수는 upsert 로 덮이는데 AI 가
쓴 문장은 아무도 안 건드려서, 그냥 두면 닭가슴살을 더해 재확정해도 "단백질 비중이
낮았어요" 가 그대로 나갔다. **여기서 또 구현하지 말 것.**
"""


def _is_stale_feedback(
    meal: Meal, previous: QQSEvaluation | None, scores: Scores
) -> bool:
    """이번 확정이 옛 AI 문장을 무효로 만드는가.

    **무조건 지우면 안 된다.** `_CONFIRMABLE` 에 `EVALUATED` 가 있어 아무것도 고치지
    않은 재확정(확정 버튼 더블탭 · FE 타임아웃 재시도 · 같은 값 재전송)도 허용되는데,
    그때까지 지우면 멀쩡한 문장이 날아간다. 되살릴 길도 없다 — `feedback.meal` 을
    큐에 넣는 코드가 아직 없고 워커도 스텁이라, 한 번 비우면 `PENDING` 에 고착된다.

    두 가지를 본다. **하나만으로는 부족하다:**

    - **상태가 `EVALUATED` 가 아니다** — 음식을 고쳐 `mark_recalculating` 을 거쳐
      왔다는 뜻이다. 성분이 달라졌으니 그 성분을 보고 쓴 문장은 낡았다.
      점수 비교로는 다 못 잡는다 — 체중이 없으면 Quality 가 `None` 이고, 음식을 바꿔도
      Quantity 가 같은 점수(구간 안이면 100)에 머물 수 있다. **점수가 같아도 성분은 바뀌었다.**
    - **점수가 달라졌다** — 사용자가 `satietyAfterPct` 를 고쳐 다시 확정했다.
      상태로는 못 잡는다. 음식을 안 고쳤으면 `EVALUATED` 그대로 들어온다.
      Quantity·Quality 의 변화(예: 체중을 새로 기록해 목표가 바뀜)도 여기서 잡힌다.

    첫 확정은 `previous` 가 없어 True 다 — 지울 행도 없어 0 행 UPDATE 다.
    """
    if meal.status is not MealStatus.EVALUATED:
        return True
    if previous is None:
        return True
    return (
        previous.quantity_score != scores.quantity
        or previous.quality_score != scores.quality
        or previous.satiety_score != scores.satiety
    )


def _is_confirmable(meal: Meal) -> bool:
    """지금 이 식사를 확정할 수 있는가.

    **`ANALYZING` 은 두 가지 뜻이다.** 상태만 보면 안 되는 이유다:

    - `is_recalculation=False` — 워커가 **최초 분석 중**이다. 끼어들면 아직 만들어지지
      않은 항목으로 채점한다. 거부한다.
    - `is_recalculation=True` — 사용자가 음식을 고쳐 **재계산 대기**다
      (`PATCH`·`POST`·`DELETE /meals/{mealId}/items`). 허용해야 한다. 막으면
      음식을 고친 사용자가 영원히 확정하지 못한다.

    🔗 `services/meal.py::_is_editable` 과 같은 모양이다 — 그쪽은 "고칠 수 있는가",
    이쪽은 "확정할 수 있는가" 이고, `ANALYZING` 을 가르는 기준이 같다.
    """
    if meal.status in _CONFIRMABLE:
        return True
    return meal.status is MealStatus.ANALYZING and meal.is_recalculation

_NUTRIENT_ROWS: Final[tuple[tuple[NutrientCode, str, str, str], ...]] = (
    (NutrientCode.PROTEIN, "protein_g", "단백질", "g"),
    (NutrientCode.FIBER, "fiber_g", "식이섬유", "g"),
    (NutrientCode.SODIUM, "sodium_mg", "나트륨", "mg"),
)
"""명세 `nutrients[]` 의 세 줄 — (코드, 합계 필드, 라벨, 단위). 순서·라벨·단위가 명세 그대로다."""

_TARGET_STEP: Final = Decimal("0.1")

_LIMIT_KEYS: Final = frozenset({"sodium_mg"})
"""목표가 아니라 **한도**인 성분. 넘으면 `OVER`, 모자란 건 문제가 아니다."""


def _weight_at(db: Session, meal: Meal) -> Decimal | None:
    """먹을 때의 체중. 그 이전 기록이 없으면 가장 최근 체중으로 대신한다.

    투약 단계를 "먹을 때의 단계" 로 읽는 것과 같은 원칙이다(`_stage_of`). 대신하는
    경우는 가입 직후 과거 시각으로 식사를 적었을 때다 — 그때 체중이 없다고 Quality 를
    비우면, 온보딩에서 받은 체중이 있는데도 점수가 안 나온다.
    """
    weight = user_state_crud.get_latest_weight_before(
        db, user_id=meal.user_id, before=meal.eaten_at
    )
    if weight is None:
        weight = user_state_crud.get_latest_weight(db, meal.user_id)
    return weight


def _targets(
    stage: MedicationStage, weight_kg: Decimal | None
) -> MealTargets:
    """그 단계 · 그때 체중의 끼니 목표. confirm 과 조회가 **같은 함수**로 구한다."""
    return meal_targets(profile_for(stage).daily, weight_kg)


def _usable(totals: NutrientTotals, key: str) -> Decimal | None:
    """채점에 쓰는 성분 합계. **화면의 `current` 와 같은 기준이다.**

    합산된 음식 중 그 성분 컬럼이 비어 있던 게 있으면(`missing`) None — 화면도 그
    합계를 안 내보낸다.

    **빠진 음식(`excluded > 0`)이 있어도 있는 것만으로 쓴다** (2026-09-30 결정).
    그 경우 응답에 `NUTRITION_NOT_MATCHED` 경고가 이미 붙는다
    (`api/v1/endpoints/evaluations.py::_respond`). 감수하는 것: 빠진 만큼 낮게 나온다.
    """
    if totals.missing.get(key):
        return None
    return getattr(totals, key)


def _state(
    current: Decimal | None, target: Decimal | None, *, is_limit: bool
) -> NutrientState | None:
    if current is None or target is None:
        return None
    if is_limit:
        return NutrientState.OVER if current > target else NutrientState.OK
    return NutrientState.SHORT if current < target else NutrientState.OK


def _stage_of(db: Session, meal: Meal) -> MedicationStage:
    """그 식사 시점의 투약 단계.

    `medication_snapshots` 에서 읽는다 — 지금 단계가 아니라 **먹을 때의 단계**다.
    나중에 용량이 바뀌어도 과거 평가가 흔들리면 안 된다.

    스냅샷은 `meals.medication_snapshot_id` 가 NOT NULL · FK RESTRICT 라 항상 있다.
    """
    snapshot = medication_crud.get_snapshot(db, meal.medication_snapshot_id)
    if snapshot is None:  # NOT NULL FK 라 도달 불가. assert 는 -O 에서 지워진다
        raise RuntimeError(f"meal {meal.id} 의 투약 스냅샷이 없습니다.")
    return snapshot.stage


def _as_float(value: Decimal | None) -> float | None:
    """명세의 `nutrients[].current` 는 숫자다. Decimal 을 그대로 두면 문자열로 샌다."""
    return None if value is None else float(value)


def _shown_target(value: Decimal | None) -> Decimal | None:
    """화면에 내보내는 목표치 — 소수 1자리. 표시와 `state` 판정이 **같은 값**을 쓴다.

    8.333… g 을 그대로 주면 화면이 자리수를 다뤄야 하고, 표시만 반올림하면
    보이는 숫자와 판정이 어긋난다.
    """
    return None if value is None else value.quantize(_TARGET_STEP, rounding=ROUND_HALF_UP)


def _build_view(
    meal: Meal,
    *,
    stage: MedicationStage,
    totals: NutrientTotals,
    targets: MealTargets,
    scores: QqsScores,
    model: type[MealEvaluationResponse] = MealEvaluationResponse,
    **extra: object,
) -> MealEvaluationResponse:
    """응답 조립. `stage` 를 받는다 — 호출부가 이미 구한 값을 다시 조회하지 않는다.

    `model` 로 만들 클래스를 받는다. confirm 응답(`MealConfirmResponse`)은 여기에
    `feedbackStatus` 하나만 더한 것이라, 완성된 모델을 `model_dump()` 로 풀었다
    다시 검증하면 왕복이 생긴다. 지금은 동작하지만 어느 필드에 `exclude=True` 나
    직렬화 alias 가 붙는 날 **confirm 쪽에서** 조용히 깨진다 — 원인과 증상이
    떨어져 있어 찾기 어렵다. 처음부터 원하는 클래스로 만든다.
    """
    return model(
        meal_id=meal.id,
        status=meal.status,
        stage=stage,
        scores=scores,
        stage_emphasis=list(emphasis_for(stage)),
        nutrients=[
            NutrientRow(
                code=code,
                label=label,
                # **결측이 있으면 합계를 안 내보낸다.** (기존 주석 유지)
                current=(
                    None
                    if totals.missing.get(key)
                    else _as_float(getattr(totals, key))
                ),
                target=_as_float(_shown_target(getattr(targets, key))),
                unit=unit,
                # 상태는 **화면에 보이는 목표**와 비교한다. 반올림 전 값과 비교하면
                # `8.3 / 8.3` 인데 SHORT 가 나간다. 채점(`rule_engine`)은 반올림 전 값을 쓴다.
                state=_state(
                    _usable(totals, key),
                    _shown_target(getattr(targets, key)),
                    is_limit=key in _LIMIT_KEYS,
                ),
            )
            for code, key, label, unit in _NUTRIENT_ROWS
        ],
        evidence=EvaluationEvidence(
            db_source=DB_SOURCE,
            stage_rule_version=STAGE_RULE_VERSION,
            weight_profile_version=WEIGHT_PROFILE_VERSION,
            nutrition_sources=_sources_of(totals),
        ),
        **extra,
    )


def _sources_of(totals: NutrientTotals) -> list[NutritionSource]:
    """성분 숫자가 **실제로 어디서 왔는지.** 합산된 게 없으면 빈 목록이다.

    항목의 존재가 아니라 **합산에 들어갔는지**로 판단한다. 예전에는
    `food_ref_id IS NULL` 을 `USER_INPUT` 으로 읽었는데 그건 "사용자가 성분을
    입력했다" 가 아니라 "성분을 아예 모른다" 다 — 명세도 그 경우 항목별
    `nutritionSource` 를 null 로 둔다.

    `USER_INPUT` 은 아직 낼 수 없다. 사용자가 성분을 직접 넣는 경로
    (`PUT /meals/{mealId}/items/{itemId}/nutrition`)가 미구현이고 `meal_items` 에
    출처 컬럼도 없다. 그 둘이 생기면 여기에 더한다.
    """
    return [NutritionSource.PUBLIC_DB] if totals.counted > 0 else []


def _owned_meal(db: Session, user_id: uuid.UUID, meal_id: uuid.UUID) -> Meal:
    meal = meal_crud.get_owned_meal(db, user_id=user_id, meal_id=meal_id)
    if meal is None:
        raise MealNotFoundError(f"meal {meal_id} 를 찾을 수 없습니다.")
    return meal


def confirm(
    db: Session,
    *,
    user_id: uuid.UUID,
    meal_id: uuid.UUID,
    request: MealConfirmRequest,
) -> EvaluationResult:
    """식사를 확정하고 Q/Q/S 를 매긴다. 커밋까지 한다."""
    meal = _owned_meal(db, user_id, meal_id)
    if not _is_confirmable(meal):
        raise MealNotConfirmableError(
            f"{meal.status.value} 상태의 식사는 확정할 수 없습니다."
        )

    satiety_crud.set_satiety_after(db, meal_id=meal.id, pct=request.satiety_after_pct)

    stage = _stage_of(db, meal)
    totals = evaluation_crud.sum_nutrients(db, meal.id)
    user = user_crud.get(db, user_id)
    if user is None:  # _owned_meal 을 통과했으니 도달 불가. assert 는 -O 에서 지워진다
        raise RuntimeError(f"user {user_id} 가 없습니다.")
    weight = _weight_at(db, meal)
    targets = _targets(stage, weight)
    scores = evaluate(
        profile=profile_for(stage),
        satiety_after_pct=request.satiety_after_pct,
        meal_kcal=_usable(totals, "kcal"),
        baseline_meal_kcal=user.baseline_meal_kcal,
        protein_g=_usable(totals, "protein_g"),
        fiber_g=_usable(totals, "fiber_g"),
        sodium_mg=_usable(totals, "sodium_mg"),
        weight_kg=weight,
    )

    # `upsert` 보다 **먼저** 읽는다 — 덮고 나면 비교할 옛 점수가 없다.
    stale = _is_stale_feedback(meal, evaluation_crud.get_by_meal(db, meal.id), scores)

    evaluation_crud.upsert(
        db,
        meal_id=meal.id,
        stage=stage,
        quantity_score=scores.quantity,
        quality_score=scores.quality,
        satiety_score=scores.satiety,
    )
    if stale:
        # 행이 아니라 내용만 비운다 — `daily_feedback_sources` 가 CASCADE 라 지우면
        # 일일 피드백의 출처 링크가 사라진다. 자세한 근거는 `crud/feedback.py` 참고.
        feedback_crud.invalidate_by_meal(db, meal.id)
    meal_crud.set_status(db, meal, MealStatus.EVALUATED)
    db.commit()

    return EvaluationResult(
        _build_view(
            meal,
            stage=stage,
            totals=totals,
            targets=targets,
            scores=QqsScores(**scores._asdict()),
            model=MealConfirmResponse,
            # 점수는 났지만 문장은 아직이다. AI 가 따로 만든다.
            feedback_status=FeedbackStatus.PENDING,
        ),
        unmatched_items=totals.unmatched,
        items_without_amount=totals.no_amount,
    )


def get_view(
    db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID
) -> EvaluationResult:
    """`GET /meals/{mealId}/evaluation`. 저장된 점수를 읽는다 — 다시 매기지 않는다.

    재채점하면 같은 식사가 조회할 때마다 다른 점수를 낼 수 있다(기준선을 튜닝하면
    특히). 평가는 확정 시점의 기록이다.
    """
    meal = _owned_meal(db, user_id, meal_id)
    row = evaluation_crud.get_by_meal(db, meal.id)
    # **행 존재가 아니라 상태를 본다.** 사용자가 확정 뒤 음식을 고치면 식사는 재계산
    # 대기로 돌아가는데(`mark_recalculating`) 옛 `qqs_evaluations` 행은 남는다.
    # 행만 보면 무효가 된 점수를 그대로 내보낸다.
    #
    # 🔗 이 가드에 기대는 건 여기뿐이다. 목록 · 달력 · dashboard 는 상태를 안 보고
    # `qqs_evaluations` 를 join 하므로, 무효가 된 점수는 **행 자체를 없애서** 막는다
    # (`crud/meal.py::mark_recalculating` → `crud/evaluation.py::delete_by_meal`).
    # 그래서 여기 도달할 때 `row` 는 이미 없다 — 상태 검사는 그 뒤를 받치는 것이다.
    #
    # ⚠️ 남는 위험: `deleted_at` 이 있는 식사의 점수는 그대로 남는다. `crud/__init__.py`
    # 가 적어 둔 대로 soft delete 라 자식 행이 살아 있고, 자식을 집계할 때 `meals` 를
    # join 해 거르는 건 읽는 쪽 책임이다. 이번 변경은 그 문제를 건드리지 않는다.
    if row is None or meal.status is not MealStatus.EVALUATED:
        raise EvaluationNotFoundError(f"meal {meal_id} 는 아직 확정되지 않았습니다.")

    def _int(value: Decimal | None) -> int | None:
        # round 다 — services/meal.py 의 점수 변환과 맞춘다. int() 로 자르면 같은
        # 저장값이 GET /meals 와 여기서 1 점 다르게 보인다.
        return None if value is None else round(value)

    totals = evaluation_crud.sum_nutrients(db, meal.id)
    return EvaluationResult(
        _build_view(
            meal,
            # **저장된 단계를 읽는다.** 스냅샷을 다시 보면 같은 사실에 진실이 둘이
            # 된다 — 지금은 스냅샷이 식사당 불변이라 값이 같지만, 그 불변이 깨지는
            # 순간 `confirm` 응답과 재조회가 다른 단계를 말하게 된다. 채점이 무엇을
            # 기준으로 매겨졌는지는 `qqs_evaluations` 가 들고 있는 사실이다.
            # 조회도 하나 줄어든다.
            stage=row.stage_at_evaluation,
            totals=totals,
            targets=_targets(row.stage_at_evaluation, _weight_at(db, meal)),

            scores=QqsScores(
                quantity=_int(row.quantity_score),
                quality=_int(row.quality_score),
                satiety=_int(row.satiety_score),
            ),
        ),
        unmatched_items=totals.unmatched,
        items_without_amount=totals.no_amount,
    )
