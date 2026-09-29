"""add meal_items estimated amount and unit

Revision ID: b8f4a2d19c33
Revises: 36301603847a
Create Date: 2026-09-22 10:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8f4a2d19c33'
down_revision: Union[str, Sequence[str], None] = '36301603847a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # AI 가 추정한 양의 원본(숫자 + 단위). `estimated_amount_g` 는 이걸 g 으로 환산한
    # 결과이고, "2개" 처럼 환산 근거가 없으면 그쪽만 NULL 로 남는다.
    #
    # `confirmed_amount` · `confirmed_unit` 과 같은 모양이다 — 읽는 쪽이 "확인됐으면
    # confirmed_*, 아니면 estimated_*" 한 규칙으로 양을 얻게 하려는 것이다. 그전에는
    # 환산 불가한 AI 추정 양이 `raw_ai_result` JSONB 안에만 있어서, 읽는 쪽이 JSON 키
    # 모양까지 알아야 했다.
    #
    # 백필하지 않는다. 이 컬럼을 채우는 주체는 워커(`worker/jobs/analyze_meal.py`)인데
    # 아직 구현 전이라 규약을 따르는 행이 하나도 없다. 워커가 붙은 뒤에 이 구조를
    # 바꾸려면 JSONB 를 파싱하는 백필이 필요해지므로 지금 넣는다.
    op.add_column(
        'meal_items',
        sa.Column('estimated_amount', sa.Numeric(precision=8, scale=2), nullable=True),
    )
    op.add_column(
        'meal_items',
        sa.Column('estimated_unit', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('meal_items', 'estimated_unit')
    op.drop_column('meal_items', 'estimated_amount')
