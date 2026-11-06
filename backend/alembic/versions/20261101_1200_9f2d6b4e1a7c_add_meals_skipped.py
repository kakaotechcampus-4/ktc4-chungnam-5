"""add meals.skipped

Revision ID: 9f2d6b4e1a7c
Revises: c5a8e1f3b7d2
Create Date: 2026-11-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f2d6b4e1a7c'
down_revision: Union[str, Sequence[str], None] = 'c5a8e1f3b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # "식사 안 함" 끼니. 사진도 음식도 없으므로 `input_present` 를 이 값으로도 통과시킨다.
    op.add_column(
        'meals',
        sa.Column('skipped', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.drop_constraint(op.f('ck_meals_input_present'), 'meals', type_='check')
    op.create_check_constraint(
        op.f('ck_meals_input_present'),
        'meals',
        'image_key IS NOT NULL OR raw_text IS NOT NULL OR skipped',
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 식사 안 함 끼니는 옛 제약을 통과하지 못한다 — 되돌리기 전에 지운다.
    op.execute('DELETE FROM meals WHERE skipped')
    op.drop_constraint(op.f('ck_meals_input_present'), 'meals', type_='check')
    op.create_check_constraint(
        op.f('ck_meals_input_present'),
        'meals',
        'image_key IS NOT NULL OR raw_text IS NOT NULL',
    )
    op.drop_column('meals', 'skipped')
