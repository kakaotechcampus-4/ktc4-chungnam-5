"""add daily_feedbacks.updated_at

Revision ID: 05ef77bf2a6a
Revises: f3b8d21ca649
Create Date: 2026-09-30 11:00:00.000000

하루 피드백도 장기 피드백(`7c2a9e4f1b6d`)과 같게 "마지막으로 생성/재생성된 시각"을
`updated_at` 에 둔다. `created_at` 은 최초 INSERT 시각에 고정하고, 재생성(upsert)
때는 `updated_at` 만 갱신한다 (PR #46 리뷰 결론을 맞춤).

**주의**: `onupdate` 는 `insert().on_conflict_do_update()` 경로에서 자동으로 안 걸린다
— `crud/daily_feedback.py::upsert` 의 SET 목록에 `updated_at=func.now()` 를 직접 넣어야 한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '05ef77bf2a6a'
down_revision: Union[str, Sequence[str], None] = 'f3b8d21ca649'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'daily_feedbacks',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('daily_feedbacks', 'updated_at')
