"""공공 영양 DB 복제본."""

from decimal import Decimal

from sqlalchemy import Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import FoodCategory, pg_enum


class FoodRef(Base):
    """공공 영양 DB 음식 1건. id 는 공공 DB 의 음식 ID 를 그대로 쓴다."""

    __tablename__ = "food_refs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[FoodCategory | None] = mapped_column(
        pg_enum(FoodCategory, "food_category"), nullable=True
    )
    """PROCESSED(가공식품) / GENERAL(일반음식). 공공 DB 적재 시 미분류면 NULL."""
    origin_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    """식품기원명."""

    serving_size: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    """영양성분함량기준량(g)."""
    calories: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    carbohydrate_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    cholesterol_mg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    saturated_fat_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    trans_fat_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    sodium_mg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)

    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)


# 이름 매칭은 공백·밑줄을 지운 뒤에 한다(`crud.food.normalize_name`). 표현식 인덱스가
# 없으면 그 비교가 33만건 순차 스캔이 된다.
#
# **식이 `crud.food._normalized_name` 과 글자까지 같아야 인덱스를 탄다.** 한쪽만 고치면
# 쿼리는 그대로 동작하고 느려지기만 해서 알아채기 어렵다.
#
# 클래스 안 `__table_args__` 가 아니라 여기 있는 건, 거기서는 `name` 컬럼이 아직
# 정의되기 전이라 참조할 수 없기 때문이다.
Index(
    "ix_food_refs_name_normalized",
    func.replace(func.replace(FoodRef.name, " ", ""), "_", ""),
)
