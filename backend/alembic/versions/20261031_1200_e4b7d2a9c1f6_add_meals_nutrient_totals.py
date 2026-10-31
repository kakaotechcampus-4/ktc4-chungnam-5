"""add meals nutrient totals

Revision ID: e4b7d2a9c1f6
Revises: 8d3f6a1c5e27
Create Date: 2026-10-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4b7d2a9c1f6'
down_revision: Union[str, Sequence[str], None] = '8d3f6a1c5e27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# `meal_items.manual_*` 와 같은 여섯 성분이다. 정밀도는 (12, 3) — 합계라 항목 값보다
# 넓어야 한다(`crud/evaluation.py::_scaled` 의 cast 와 같다).
_COLUMNS = (
    'total_kcal',
    'total_protein_g',
    'total_fat_g',
    'total_carb_g',
    'total_fiber_g',
    'total_sodium_mg',
)


def upgrade() -> None:
    """Upgrade schema."""
    # 한 끼 영양 합계. AI 입력용 스냅샷으로, 확정 시점에 채우고 음식이 바뀌면 비운다.
    # 비어 있는 영양정보는 0 으로 더하므로 NULL 은 "아직 확정 전" 뿐이다.
    for name in _COLUMNS:
        op.add_column(
            'meals',
            sa.Column(name, sa.Numeric(precision=12, scale=3), nullable=True),
        )

    # 백필하지 않는다. 합계 규칙(직접 입력 → 공공 DB 환산)은 `services.meal` 한 곳에만
    # 있어야 하고, SQL 로 다시 쓰면 두 벌이 된다. 이미 확정된 식사는 다시 확정할 때 채워진다.
    #
    # CHECK(>= 0) 도 걸지 않는다. 사용자 입력이 아니라 유도값이라 막을 실수가 없고,
    # 공공 DB 의 이상한 행 하나가 확정을 500 으로 죽이게 된다.


def downgrade() -> None:
    """Downgrade schema."""
    for name in reversed(_COLUMNS):
        op.drop_column('meals', name)
