"""add qqs_evaluations.intake_ratio_pct

Revision ID: 3a9c7e5b2d41
Revises: 9f2d6b4e1a7c
Create Date: 2026-11-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a9c7e5b2d41'
down_revision: Union[str, Sequence[str], None] = '9f2d6b4e1a7c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 평소 한 끼 대비 섭취 비율(%). 이미 매긴 행은 비워 둔다 — 그때의 평소 섭취량을 모른다.
    op.add_column('qqs_evaluations', sa.Column('intake_ratio_pct', sa.Numeric(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('qqs_evaluations', 'intake_ratio_pct')
