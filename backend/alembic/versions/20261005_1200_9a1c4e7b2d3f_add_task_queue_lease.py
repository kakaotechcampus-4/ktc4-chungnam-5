"""add task_queue lease

Revision ID: 9a1c4e7b2d3f
Revises: 05ef77bf2a6a
Create Date: 2026-10-05 12:00:00.000000

워커가 AI 를 기다리는 동안 트랜잭션·행 잠금을 쥐지 않도록 lease 방식으로 바꾼다
(docs/superpowers/specs/2026-10-05-queue-lease-design.md). 지금 구조에서는 PROCESSING
행이 존재할 수 없으므로 데이터 이전은 없다.

**배포 순서**: 워커를 내린다 → 이 마이그레이션 → API 교체 → 새 워커를 올린다.
옛 API 는 PROCESSING 을 모른다(네이티브 enum 이라 그 행을 읽으면 LookupError) — 새 워커
보다 먼저 바꾼다. 옛 워커가 행 잠금을 쥔 채면 ADD COLUMN(ACCESS EXCLUSIVE)이 막힌다 —
먼저 내린다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9a1c4e7b2d3f'
down_revision: Union[str, Sequence[str], None] = '05ef77bf2a6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 새 ENUM 값은 그 값을 추가한 트랜잭션 안에서 쓸 수 없다("unsafe use of new value").
    # 바로 아래 부분 인덱스의 WHERE 가 이 값을 쓰므로 먼저 따로 커밋한다.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'PROCESSING' AFTER 'PENDING'")

    op.add_column('task_queue', sa.Column('lease_token', sa.Uuid(), nullable=True))
    op.add_column(
        'task_queue',
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_task_queue_processing',
        'task_queue',
        ['lease_expires_at'],
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )


def downgrade() -> None:
    """Downgrade schema.

    ENUM 값은 Postgres 가 지울 수 없어 남긴다. 쓰는 행만 PENDING 으로 되돌린다.
    PROCESSING 이던 행은 claim 때 올라간 `attempts + 1` 을 그대로 가진다(시도 한 번이
    소진된다) — 롤백으로는 받아들일 만하다.
    """
    op.execute("UPDATE task_queue SET status = 'PENDING' WHERE status = 'PROCESSING'")
    op.drop_index('ix_task_queue_processing', table_name='task_queue')
    op.drop_column('task_queue', 'lease_expires_at')
    op.drop_column('task_queue', 'lease_token')
