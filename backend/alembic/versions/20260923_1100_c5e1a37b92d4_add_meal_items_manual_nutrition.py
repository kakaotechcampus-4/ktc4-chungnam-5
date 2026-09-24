"""add meal_items manual nutrition

Revision ID: c5e1a37b92d4
Revises: 817327c34a0e
Create Date: 2026-09-23 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e1a37b92d4'
down_revision: Union[str, Sequence[str], None] = '817327c34a0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# `food_refs` 와 같은 타입·정밀도다. 두 출처(공공 DB 환산값 · 사용자 직접 입력)를
# 나중에 Q/Q/S 채점기가 한 규칙으로 읽어야 하므로 모양이 달라지면 안 된다.
_COLUMNS = (
    'manual_kcal',
    'manual_protein_g',
    'manual_fat_g',
    'manual_carb_g',
    'manual_fiber_g',
    'manual_sodium_mg',
)


def upgrade() -> None:
    """Upgrade schema."""
    # 사용자가 `PUT /meals/{mealId}/items/{itemId}/nutrition` 의 `manual` 로 직접
    # 적어 넣은 영양성분. **섭취량 기준 총량**이지 기준량(`food_refs.serving_size`)
    # 기준이 아니다 — 그래서 읽을 때 환산하지 않는다.
    #
    # JSONB 한 컬럼이 아니라 전용 컬럼인 이유: 키 오타를 DB 가 막아주고,
    # `food_refs` 와 같은 자리에서 합산된다.
    for name in _COLUMNS:
        op.add_column(
            'meal_items',
            sa.Column(name, sa.Numeric(precision=10, scale=3), nullable=True),
        )

    # 음수 영양성분은 입력 실수다. 스키마(`ManualNutrition`)가 먼저 막지만, 워커나
    # 배치가 API 를 거치지 않고 쓰는 경로가 생겨도 값이 새지 않게 DB 에도 건다.
    op.create_check_constraint(
        'manual_nutrition_non_negative',
        'meal_items',
        ' AND '.join(f'({name} IS NULL OR {name} >= 0)' for name in _COLUMNS),
    )

    # 백필하지 않는다. 기존 행은 전부 직접 입력한 적이 없으므로 NULL 이 맞다.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('manual_nutrition_non_negative', 'meal_items', type_='check')
    for name in reversed(_COLUMNS):
        op.drop_column('meal_items', name)
