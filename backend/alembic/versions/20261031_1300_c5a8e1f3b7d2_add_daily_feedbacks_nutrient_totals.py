"""add daily_feedbacks nutrient totals

Revision ID: c5a8e1f3b7d2
Revises: e4b7d2a9c1f6
Create Date: 2026-10-31 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5a8e1f3b7d2'
down_revision: Union[str, Sequence[str], None] = 'e4b7d2a9c1f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# `meals.total_*` 와 같은 여섯 성분 · 같은 정밀도다 — 그 값을 그날 끼니만큼 더한다.
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
    # 그날 영양 합계. `feedback.meal` 이 끼니마다 그날 전체를 다시 더해 덮는다.
    for name in _COLUMNS:
        op.add_column(
            'daily_feedbacks',
            sa.Column(name, sa.Numeric(precision=12, scale=3), nullable=True),
        )

    # 백필하지 않는다. 재료인 `meals.total_*` 도 백필하지 않아 이미 확정된 끼니는 비어
    # 있다 — 지나간 날의 행은 그날 끼니가 다시 확정될 때 채워진다.


def downgrade() -> None:
    """Downgrade schema."""
    for name in reversed(_COLUMNS):
        op.drop_column('daily_feedbacks', name)
