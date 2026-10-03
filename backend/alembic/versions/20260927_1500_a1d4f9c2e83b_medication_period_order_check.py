"""medication_records period order check

Revision ID: a1d4f9c2e83b
Revises: c5e1a37b92d4
Create Date: 2026-09-27 15:00:00.000000

`effective_from > effective_to` 인 행은 정상 경로로는 만들어지지 않는다 —
`register` 는 `effective_to = change_date - 1` 로만 닫고, `correct` 는 상한을 검사한다.

그래도 DB 에 거는 건 **마지막 방어선**이라서다. 동시 요청이 서로 다른 컬럼을 쓰면
둘 다 반영된 합성 행이 남을 수 있고(SQLAlchemy 의 UPDATE 는 바뀐 컬럼만 쓴다),
뒤집힌 행이 한 번 생기면 `get_previous` 가 그 행을 못 찾아 이후 모든 구간 검증이
깨진 값 위에서 돈다. 500 이 아니라 조용한 영구 손상이 된다.

기존 행은 전부 `close_current` 가 만든 것이라 이 조건을 이미 만족한다.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "a1d4f9c2e83b"
down_revision: Union[str, Sequence[str], None] = "c5e1a37b92d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_medication_records_period_order",
        "medication_records",
        "effective_to IS NULL OR effective_from <= effective_to",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_medication_records_period_order",
        "medication_records",
        type_="check",
    )
