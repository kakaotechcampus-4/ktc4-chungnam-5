"""`food_refs` 접근.

공공 영양 DB 복제본이라 읽기만 한다. 적재는 `scripts/load_food_refs.py` 가 맡는다.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.food import FoodRef


def get(db: Session, food_ref_id: str | None) -> FoodRef | None:
    if not food_ref_id:
        return None
    return db.get(FoodRef, food_ref_id)


def get_many(db: Session, food_ref_ids: Iterable[str | None]) -> dict[str, FoodRef]:
    """여러 건을 한 번에 읽는다. 키가 없는 것은 결과에서 빠진다.

    한 끼에서 음식이 여러 개 나오므로 항목마다 조회하면 왕복이 그만큼 는다.
    AI 쪽 `search_foods` 가 배열을 받는 것과 같은 이유다.
    """
    ids = {ref_id for ref_id in food_ref_ids if ref_id}
    if not ids:
        return {}

    rows = db.scalars(select(FoodRef).where(FoodRef.id.in_(ids))).all()
    return {row.id: row for row in rows}
