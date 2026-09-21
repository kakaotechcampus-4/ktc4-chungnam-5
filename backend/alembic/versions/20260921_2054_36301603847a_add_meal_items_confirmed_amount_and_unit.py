"""add meal_items confirmed amount and unit

Revision ID: 36301603847a
Revises: 4653efcf5c74
Create Date: 2026-09-21 20:54:28.383542

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36301603847a'
down_revision: Union[str, Sequence[str], None] = '4653efcf5c74'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 사용자가 입력한 양의 원본(숫자 + 단위). `confirmed_amount_g` 는 이걸 g 으로
    # 환산한 결과이고, "2개" 처럼 환산 근거가 없으면 그쪽만 NULL 로 남는다.
    #
    # 백필하지 않는다. 기존 행은 전부 사용자 확인 전이라 NULL 이 맞고, 그건
    # `confirmed_amount_g` 가 이미 쓰고 있는 규약과 같다.
    op.add_column(
        'meal_items',
        sa.Column('confirmed_amount', sa.Numeric(precision=8, scale=2), nullable=True),
    )
    op.add_column(
        'meal_items',
        sa.Column('confirmed_unit', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('meal_items', 'confirmed_unit')
    op.drop_column('meal_items', 'confirmed_amount')
