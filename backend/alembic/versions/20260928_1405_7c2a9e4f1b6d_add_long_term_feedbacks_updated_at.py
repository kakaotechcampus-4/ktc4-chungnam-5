"""add long_term_feedbacks.updated_at

Revision ID: 7c2a9e4f1b6d
Revises: 4f8b1c2d9a3e
Create Date: 2026-09-28 14:05:00.000000

`GET /insights/long-term`의 `generatedAt`은 이 행이 "언제 생성됐는가"가 아니라
"언제 마지막으로 생성/재생성됐는가"를 뜻해야 한다. `created_at`은 최초 INSERT
시각에 고정되고 재확정(upsert)때 안 바뀌므로, 별도의 `updated_at`을 둔다
(PR #46 리뷰).

**주의**: 이 컬럼도 `onupdate`가 SQLAlchemy Core 의 `insert().on_conflict_do_update()`
경로에서는 자동으로 안 걸린다 — `worker/jobs/feedback_long.py`를 구현할 때
`ON CONFLICT DO UPDATE`의 SET 목록에 `updated_at=func.now()`를 직접 넣어야 한다
(`crud/evaluation.py::upsert`가 `computed_at`을 빠뜨린 것과 같은 함정).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7c2a9e4f1b6d'
down_revision: Union[str, Sequence[str], None] = '4f8b1c2d9a3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'long_term_feedbacks',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('long_term_feedbacks', 'updated_at')
