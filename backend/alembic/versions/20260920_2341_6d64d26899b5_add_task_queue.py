"""add task_queue

Revision ID: 6d64d26899b5
Revises: fb9324353300
Create Date: 2026-09-20 23:41:52.389774

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '6d64d26899b5'
down_revision: Union[str, Sequence[str], None] = 'fb9324353300'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ENUM 은 테이블보다 먼저 만들고 테이블을 지운 뒤에 지운다 (초기 마이그레이션과 같은 방식).
TASK_STATUS = postgresql.ENUM(
    'PENDING', 'DONE', 'FAILED', name='task_status', create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    TASK_STATUS.create(bind, checkfirst=True)

    op.create_table(
        'task_queue',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', TASK_STATUS, server_default='PENDING', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'next_run_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_queue')),
    )

    # 집는 쿼리 전용 부분 인덱스. autogenerate 는 WHERE 절을 만들지 못한다.
    op.create_index(
        'ix_task_queue_pending',
        'task_queue',
        ['next_run_at', 'created_at'],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_task_queue_pending',
        table_name='task_queue',
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_table('task_queue')
    TASK_STATUS.drop(op.get_bind(), checkfirst=True)
