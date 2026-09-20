"""add meals.is_recalculation

Revision ID: a3c71e08d5b2
Revises: fb9324353300
Create Date: 2026-09-20 11:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c71e08d5b2'
down_revision: Union[str, Sequence[str], None] = 'fb9324353300'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 기존 행은 전부 "최초 분석" 이다 — 재분석된 적이 없으므로 false 가 맞다.
    op.add_column(
        'meals',
        sa.Column(
            'is_recalculation',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('meals', 'is_recalculation')
