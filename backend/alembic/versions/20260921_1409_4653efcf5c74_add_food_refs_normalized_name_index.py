"""add food_refs normalized name index

Revision ID: 4653efcf5c74
Revises: a3c71e08d5b2
Create Date: 2026-09-21 14:09:13.904004

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4653efcf5c74'
down_revision: Union[str, Sequence[str], None] = 'a3c71e08d5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 이름 매칭은 공백·밑줄을 지운 뒤 비교한다(`crud.food.normalize_name`).
    # 이 인덱스가 없으면 그 비교가 33만건 순차 스캔이 된다.
    # 식은 모델(`models/food.py` 끝)·쿼리(`crud/food._normalized_name`)와 동일해야 한다.
    op.create_index('ix_food_refs_name_normalized', 'food_refs', [sa.literal_column("replace(replace(name, ' ', ''), '_', '')")], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_food_refs_name_normalized', table_name='food_refs')
