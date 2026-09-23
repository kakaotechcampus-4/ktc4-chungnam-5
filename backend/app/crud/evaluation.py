"""qqs_evaluations 접근 + 채점에 필요한 한 끼 영양 합계.

`qqs_evaluations` 는 `(meal_id)` UNIQUE 라 create 가 아니라 **upsert** 다
(`crud/__init__.py` 의 목록 참고). 재평가하면 덮어쓴다 — 식사당 1행이다.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Final, NamedTuple

from sqlalchemy import Numeric, and_, case, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.enums import MedicationStage
from app.models.evaluation import QQSEvaluation
from app.models.food import FoodRef
from app.models.meal import MealItem


def get_by_meal(db: Session, meal_id: uuid.UUID) -> QQSEvaluation | None:
    stmt = select(QQSEvaluation).where(QQSEvaluation.meal_id == meal_id)
    return db.execute(stmt).scalar_one_or_none()


def upsert(
    db: Session,
    *,
    meal_id: uuid.UUID,
    stage: MedicationStage,
    quantity_score: int | None,
    quality_score: int | None,
    satiety_score: int | None,
) -> QQSEvaluation:
    """있으면 덮고 없으면 만든다. flush 까지만 — 커밋은 services 가 한다.

    재평가는 이력을 쌓지 않는다. `qqs_evaluations` 는 "지금 이 식사의 점수" 하나만
    들고 있고, 추이는 식사 단위로 쌓인 행들이 만든다.

    **`ON CONFLICT` 한 문장이다.** 읽고 나서 넣으면 그 사이에 다른 요청이 넣을 수
    있고, `(meal_id)` UNIQUE 라 두 번째가 무결성 위반으로 죽어 500 이 나간다.
    확인 버튼 더블탭이나 클라이언트 재시도가 그 트리거다.
    """
    values = {
        "meal_id": meal_id,
        "stage_at_evaluation": stage,
        "quantity_score": None if quantity_score is None else Decimal(quantity_score),
        "quality_score": None if quality_score is None else Decimal(quality_score),
        "satiety_score": None if satiety_score is None else Decimal(satiety_score),
    }
    stmt = insert(QQSEvaluation).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[QQSEvaluation.meal_id],
        set_={k: v for k, v in values.items() if k != "meal_id"},
    ).returning(QQSEvaluation)
    row = db.execute(stmt).scalar_one()
    db.flush()
    return row


# ── 채점 입력: 한 끼 영양 합계 ────────────────────────────────


class NutrientTotals(NamedTuple):
    """한 끼 성분 합계 + **무엇이 합산에 들어갔는지**.

    개수를 같이 주는 게 핵심이다. 합계만 주면 호출부가 "성분을 못 구한 음식이
    있었다" 를 알 길이 없어, 일부만 더한 값을 완전한 값처럼 내보내게 된다
   . 건강 코칭 앱에서 단백질이 조용히 적게 나오면 사용자는
    그 숫자를 믿고 다음 끼니를 조절한다.
    """

    kcal: Decimal | None = None
    """지금은 읽는 곳이 없다. Quantity 채점(개인 baseline 대비 감소폭, 절대 규칙 4)
    이 들어올 때 분자가 될 값이라 미리 구해 둔다 — 그때 쿼리를 다시 짜지 않으려는
    것이다. 채점을 안 하기로 최종 확정되면 이 줄과 SELECT 항목을 같이 지운다."""
    protein_g: Decimal | None = None
    fiber_g: Decimal | None = None
    sodium_mg: Decimal | None = None
    counted: int = 0
    """합산에 들어간 항목 수."""
    unmatched: int = 0
    """**성분을 못 구한** 항목 수. 이름 매칭 실패이거나 기준량이 못 쓸 값이다.
    사용자가 영양정보를 직접 넣으면 풀린다."""
    no_amount: int = 0
    """성분은 구했는데 **먹은 양을 모르는** 항목 수. "2개" 처럼 g 환산이 안 된 단위다.
    영양정보를 넣어도 안 풀린다 — 양을 g 으로 고쳐야 한다. `unmatched` 와 섞으면
    사용자를 못 고치는 화면으로 보내게 된다."""
    missing: Mapping[str, int] = MappingProxyType({})
    """**성분별로** 값이 비어 있던 항목 수. 키는 위 합계 필드 이름이다.

    항목 단위 제외(`unmatched` · `no_amount`)와 다른 축이다. 매칭도 됐고 양도 아는
    항목인데 `food_refs` 의 그 **컬럼만** NULL 인 경우가 있다 — 공공 DB 에 식이섬유나
    나트륨이 비어 있는 행이 흔하고, 시드 로더가 파싱 실패를 NULL 로 넣는다.

    SQL 의 `SUM` 은 NULL 입력 행을 조용히 건너뛴다. 그래서 이걸 안 세면 **같은 응답
    안에서 단백질만 부분합이고 식이섬유는 완전한** 상태가 되는데, 겉으로는 구분이
    안 된다. 0 이 아닌 성분은 합계를 내보내지 않는다 (`services/evaluation`).
    """

    @property
    def excluded(self) -> int:
        """합산에서 빠진 항목 수 전체."""
        return self.unmatched + self.no_amount


_NUTRIENT_COLUMNS: Final = {
    "kcal": FoodRef.calories,
    "protein_g": FoodRef.protein_g,
    "fiber_g": FoodRef.fiber_g,
    "sodium_mg": FoodRef.sodium_mg,
}
"""`NutrientTotals` 의 합계 필드 ↔ `food_refs` 컬럼.

합계와 결측 카운터가 **같은 목록에서 나와야** 한다. 따로 적으면 성분을 하나 더할 때
합계만 늘고 카운터를 빠뜨려서, 그 성분만 조용히 부분합이 된다.
"""


def _eaten_amount_g():
    """실제 먹은 양(g). 없으면 NULL 이다.

    **확인 여부의 센티넬은 `confirmed_amount` 다** — `confirmed_amount_g` 가 아니다.
    그쪽은 g 으로 환산된 값만 담아서 "계란 2개" 로 확인한 항목도 NULL 이라,
    그 NULL 은 "확인 전" 과 "확인했지만 환산 불가" 를 구분하지 못한다.

    그래서 `COALESCE` 를 쓸 수 없다. 사용자가 "2개" 로 **고쳐 놓은** 항목이
    `confirmed_amount_g IS NULL` 이라는 이유로 AI 추정 g 으로 폴백하면,
    사용자가 방금 부정한 값이 합계에 들어간다. 그건 빠뜨리는 것보다 나쁘다 —
    경고도 못 붙고(`no_amount` 가 0 이다) 사용자는 자기가 고친 값이 반영된 줄 안다.

    확인된 항목은 환산에 실패했으면 거기서 끝이다. AI 추정값으로 되돌아가지 않는다.

    🔗 `services/meal.py::_stored_amount` 와 같은 규칙이다. 한쪽만 바꾸면 갈라진다.
    """
    return case(
        (MealItem.confirmed_amount.isnot(None), MealItem.confirmed_amount_g),
        else_=MealItem.estimated_amount_g,
    )


def _scaled(column, amount, usable):
    """기준량 대비 실제 섭취량으로 환산한 성분값의 합.

    `food_refs` 의 성분은 `serving_size`(영양성분함량기준량, 보통 100g) 기준이다.

    `amount` 를 인자로 받는 건 **호출부와 같은 식을 쓰기 위해서다.** 여기서 따로
    만들면 합계와 카운터가 서로 다른 양을 보게 되고, 한쪽만 고치는 실수가 난다.
    """
    return func.sum(
        cast(column * amount / FoodRef.serving_size, Numeric(12, 3))
    ).filter(usable)


def sum_nutrients(db: Session, meal_id: uuid.UUID) -> NutrientTotals:
    """한 끼의 성분 합계와 합산 가능 여부.

    **LEFT JOIN 이다.** 성분을 못 구한 항목도 세어야 하므로 버리지 않고 남긴다.
    합산에서 빠지는 조건은 셋이다:

    - `food_ref_id` 가 없다 — 이름 매칭에 실패했다. 0 으로 채우면 "안 먹었다" 가
      되어 합계가 거짓으로 작아진다.
    - 먹은 양을 모른다 — 아무도 양을 말해 주지 않았거나, 사용자가 말한 양이
      "2개" 처럼 g 으로 환산되지 않는다 (`_eaten_amount_g` 참고).
    - 기준량이 없거나 0 이거나 NaN 이다 — 나누면 터지거나 합이 NaN 이 된다.

    NaN 은 `> 0` 으로 걸러지지 않는다. Postgres 에서 `NaN > 0` 은 **참**이고
    `NaN = NaN` 도 참이라, 배제하려면 `<> 'NaN'` 을 써야 한다.

    **`meal_items` · `food_refs` 를 읽지만 이 파일에 둔다.** `crud/__init__.py` 의
    "한 파일 한 엔티티" 를 어기는 것처럼 보이는데, 남의 엔티티 파일에 넣으면 동시
    작업 충돌이 난다 — 그 규약의 목적이 바로 그 충돌 회피다. 평가 전용 집계라 다른
    도메인이 쓸 일도 없다. 옮기려면 두 파일 담당자와 먼저 이야기할 것.
    """
    amount = _eaten_amount_g()
    # 성분을 쓸 수 있는가 — 매칭됐고 기준량이 멀쩡한가. 양과는 별개다.
    has_nutrition = and_(
        MealItem.food_ref_id.isnot(None),
        FoodRef.serving_size.isnot(None),
        FoodRef.serving_size > 0,
        FoodRef.serving_size != Decimal("NaN"),
    )
    usable = and_(has_nutrition, amount.isnot(None))
    stmt = (
        select(
            *(
                _scaled(column, amount, usable).label(key)
                for key, column in _NUTRIENT_COLUMNS.items()
            ),
            # **성분별 결측을 따로 센다.** 합산 대상인 항목인데 그 컬럼만 NULL 인
            # 경우가 있고, SQL 의 SUM 은 그런 행을 조용히 건너뛴다. 안 세면 부분합이
            # 완전한 값처럼 나간다.
            *(
                func.count().filter(and_(usable, column.is_(None))).label(f"{key}_missing")
                for key, column in _NUTRIENT_COLUMNS.items()
            ),
            func.count().filter(usable).label("counted"),
            # 제외 사유를 나눠 센다. 사용자에게 줄 안내가 다르다.
            func.count().filter(~has_nutrition).label("unmatched"),
            func.count().filter(and_(has_nutrition, amount.is_(None))).label("no_amount"),
        )
        .select_from(MealItem)
        .outerjoin(FoodRef, FoodRef.id == MealItem.food_ref_id)
        .where(MealItem.meal_id == meal_id)
    )
    row = db.execute(stmt).one()
    return NutrientTotals(
        kcal=row.kcal,
        protein_g=row.protein_g,
        fiber_g=row.fiber_g,
        sodium_mg=row.sodium_mg,
        counted=row.counted,
        unmatched=row.unmatched,
        no_amount=row.no_amount,
        missing=MappingProxyType(
            {key: getattr(row, f"{key}_missing") for key in _NUTRIENT_COLUMNS}
        ),
    )
