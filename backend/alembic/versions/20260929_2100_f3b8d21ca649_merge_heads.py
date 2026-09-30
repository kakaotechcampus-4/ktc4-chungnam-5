"""merge heads

Revision ID: f3b8d21ca649
Revises: e7a3f915c26d, a1d4f9c2e83b, 7c2a9e4f1b6d
Create Date: 2026-09-29 21:00:00.000000

`c5e1a37b92d4`(add_meal_items_manual_nutrition) 에서 세 팀원이 동시에 각자
브랜치를 파서 head 가 셋으로 갈라졌다 — `meal_feedbacks_suggestions_to_jsonb`→
`add_satiety_checkins`, `medication_period_order_check`, `add_all_to_feedback_period_type`
→`add_long_term_feedbacks_updated_at`(이 브랜치가 만든 것). 이미 push 된 리비전들의
`down_revision`은 건드리지 않고, 표준 방식대로 병합 리비전 하나로 합친다.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'f3b8d21ca649'
down_revision: Union[str, Sequence[str], None] = (
    'e7a3f915c26d',
    'a1d4f9c2e83b',
    '7c2a9e4f1b6d',
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
