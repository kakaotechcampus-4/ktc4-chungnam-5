"""add meals.deleted_at for soft delete

Revision ID: fb9324353300
Revises: 66b5d91e4b8a
Create Date: 2026-09-15 19:55:29.758724

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb9324353300'
down_revision: Union[str, Sequence[str], None] = '66b5d91e4b8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('meals', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))

    # autogenerate 가 기존 인덱스의 WHERE 조건 변경은 못 잡아서 수동으로 추가.
    # 삭제 안 된 식사만 인덱스에 넣도록 partial index 로 좁힌다.
    op.drop_index('ix_meals_user_id_eaten_at', table_name='meals')
    op.create_index(
        'ix_meals_user_id_eaten_at',
        'meals',
        ['user_id', sa.text('eaten_at DESC')],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_meals_user_id_eaten_at',
        table_name='meals',
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.create_index(
        'ix_meals_user_id_eaten_at',
        'meals',
        ['user_id', sa.text('eaten_at DESC')],
        unique=False,
    )
    op.drop_column('meals', 'deleted_at')
