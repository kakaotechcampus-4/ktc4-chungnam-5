"""food_refs 테이블 접근. 여기 말고는 아무도 FoodRef 를 직접 쿼리하지 않는다."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.food import FoodRef


def find_by_name(db: Session, name: str) -> FoodRef | None:
    """이름이 정확히 일치하는 음식 1건. 없으면 None.

    **정렬과 LIMIT 을 뺄 수 없다.** 33만건 테이블에 `name` 은 UNIQUE 가 아니고,
    가공식품은 제조사만 다른 동명 행이 흔하다.

    - `LIMIT 1` 이 없으면 `.first()` 가 SQL 을 제한하지 않아 일치하는 행을 전부
      받아 ORM 객체로 만든 뒤 하나만 남기고 버린다.
    - `ORDER BY` 가 없으면 어느 행이 뽑힐지 플랜과 힙 순서에 달린다. 같은 입력이
      시점에 따라 다른 kcal 을 돌려주고, 그 값이 `meal_items.food_ref_id` 로
      박제돼 Q/Q/S 점수까지 바뀐다.

    정렬 기준은 (1) 열량이 있는 행 먼저, (2) id. 원본에 결측이 많아서다 —
    `외식` 15,225건은 지방 87% · 탄수화물 84% 가 비어 있다(README "공공 영양DB").
    결측 행을 집으면 매칭에 성공하고도 영양성분이 빈 채로 나간다.
    """
    stmt = (
        select(FoodRef)
        .where(FoodRef.name == name)
        .order_by(FoodRef.calories.is_(None), FoodRef.id)
        .limit(1)
    )
    return db.execute(stmt).scalars().first()
