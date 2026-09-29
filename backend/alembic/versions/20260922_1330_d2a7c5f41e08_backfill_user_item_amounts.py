"""backfill user item amounts

Revision ID: d2a7c5f41e08
Revises: b8f4a2d19c33
Create Date: 2026-09-22 13:30:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd2a7c5f41e08'
down_revision: Union[str, Sequence[str], None] = 'b8f4a2d19c33'
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
    # 구버전 `POST /meals/{mealId}/items` 가 만든 `source=USER` 행을 고친다. 그때는
    # 사용자 입력을 `confirmed_amount_g`(환산값)와 `raw_ai_result`(원본 숫자·단위)에
    # 나눠 썼다. 그 행은 `confirmed_amount IS NULL` 이라 읽기 규칙("확인됐으면
    # confirmed_*, 아니면 estimated_*")이 `estimated_*` 로 폴백하는데, USER 행은 그쪽이
    # 전부 NULL 이다 — `confirmed_amount_g` 가 멀쩡히 있는데도 양을 "모름" 으로 읽는다.
    #
    # **컬럼을 추가한 `36301603847a` 가 아니라 별도 리비전인 이유**: 그 리비전을 이미
    # 적용한 DB(먼저 브랜치를 받아 `alembic upgrade head` 를 돌린 팀원)는 그 파일을
    # 다시 실행하지 않는다. 백필을 거기 넣으면 정작 고쳐야 할 DB 에서만 안 돈다.
    #
    # `raw_ai_result` 는 지우지 않는다. `source=USER` 행은 NULL 이라는 규약의 예외로
    # 남지만 읽는 코드가 없어 무해하고, 지우면 되돌릴 근거가 사라진다.
    op.execute(BACKFILL_USER_AMOUNTS)


def downgrade() -> None:
    """Downgrade schema."""
    # 데이터 백필이라 되돌리지 않는다. 채워 넣은 값은 `raw_ai_result` 와 중복이라
    # 남아 있어도 해가 없고, NULL 로 되돌리면 그 사이에 사용자가 실제로 확인한 행까지
    # 구분 없이 지운다.
