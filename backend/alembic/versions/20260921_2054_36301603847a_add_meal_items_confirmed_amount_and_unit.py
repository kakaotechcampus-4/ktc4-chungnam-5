"""add meal_items confirmed amount and unit

Revision ID: 36301603847a
Revises: 4653efcf5c74
Create Date: 2026-09-21 20:54:28.383542

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36301603847a'
down_revision: Union[str, Sequence[str], None] = '4653efcf5c74'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 백필 SQL. 테스트(`test_meal_items_migration.py`)가 이 상수를 그대로 실행해 검증한다 —
# 마이그레이션을 되감지 않고도 `?` 연산자와 캐스팅이 맞는지 확인할 수 있다.
BACKFILL_USER_AMOUNTS = """
    UPDATE meal_items
       SET confirmed_amount = (raw_ai_result->>'amount')::numeric,
           confirmed_unit   = raw_ai_result->>'unit'
     WHERE source = 'USER'
       AND confirmed_amount IS NULL
       AND raw_ai_result ? 'amount'
       AND raw_ai_result ? 'unit'
"""


def upgrade() -> None:
    """Upgrade schema."""
    # 사용자가 입력한 양의 원본(숫자 + 단위). `confirmed_amount_g` 는 이걸 g 으로
    # 환산한 결과이고, "2개" 처럼 환산 근거가 없으면 그쪽만 NULL 로 남는다.
    op.add_column(
        'meal_items',
        sa.Column('confirmed_amount', sa.Numeric(precision=8, scale=2), nullable=True),
    )
    op.add_column(
        'meal_items',
        sa.Column('confirmed_unit', sa.String(length=32), nullable=True),
    )

    # AI 가 인식한 항목은 백필하지 않는다 — 전부 사용자 확인 전이라 NULL 이 맞고,
    # 그건 `confirmed_amount_g` 가 이미 쓰고 있는 규약과 같다.
    #
    # **사용자가 직접 넣은 항목(`source=USER`)은 백필해야 한다.** 이 컬럼이 생기기
    # 전의 `POST /meals/{mealId}/items` 는 사용자 입력을 `confirmed_amount_g`(환산값)
    # 와 `raw_ai_result`(원본 숫자·단위) 두 곳에 나눠 썼다. 그 행은
    # `confirmed_amount IS NULL` 이라 새 읽기 규칙("확인됐으면 confirmed_*, 아니면
    # estimated_*")이 `estimated_*` 로 폴백하는데, USER 행은 그쪽이 전부 NULL 이다
    # — `confirmed_amount_g` 가 멀쩡히 있는데도 양을 "모름" 으로 읽는다.
    #
    # `raw_ai_result` 는 지우지 않는다. 지우면 이 마이그레이션의 downgrade 가 양을
    # 되돌릴 수 없다(컬럼이 사라지므로). 백필된 행에 한해 "USER 행의
    # `raw_ai_result` 는 NULL" 규약의 예외로 남지만, 읽는 코드가 없어 무해하다.
    op.execute(BACKFILL_USER_AMOUNTS)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('meal_items', 'confirmed_unit')
    op.drop_column('meal_items', 'confirmed_amount')
