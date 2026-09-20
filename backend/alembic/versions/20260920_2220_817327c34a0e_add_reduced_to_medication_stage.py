"""add REDUCED to medication_stage

Revision ID: 817327c34a0e
Revises: fb9324353300
Create Date: 2026-09-20 22:20:00.000000

단계를 5개로 확정한 팀 결정을 반영한다.

    명세  INITIAL | TITRATION | MAINTENANCE          3개
    DB    + PRE_DOSE                                 4개  ← 여기까지가 기존
    확정  + REDUCED                                  5개

`PRE_DOSE` 는 init_schema 부터 DB 에 있었다. 실제로 추가되는 건 `REDUCED` 하나다.

**머지 순서 주의.** 팀원 PR 에도 마이그레이션이 있으면 alembic head 가 갈라진다.
먼저 머지되는 쪽에 맞춰 `down_revision` 을 다시 잡아야 한다.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '817327c34a0e'
down_revision: Union[str, Sequence[str], None] = 'fb9324353300'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 이 ENUM 을 쓰는 컬럼 전부. downgrade 에서 타입을 갈아끼울 때 캐스팅 대상이다.
_STAGE_COLUMNS = (
    ('medication_records', 'stage'),
    ('medication_snapshots', 'stage'),
    ('qqs_evaluations', 'stage_at_evaluation'),
)

_BEFORE = ('PRE_DOSE', 'INITIAL', 'TITRATION', 'MAINTENANCE')


def upgrade() -> None:
    """Upgrade schema."""
    # ALTER TYPE ... ADD VALUE 는 추가된 값을 같은 트랜잭션 안에서 쓸 수 없다.
    # autocommit_block 으로 빼내야 뒤이은 마이그레이션이 REDUCED 를 바로 쓸 수 있다.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE medication_stage ADD VALUE IF NOT EXISTS 'REDUCED'")


def downgrade() -> None:
    """Downgrade schema.

    PostgreSQL 은 ENUM 값 삭제를 지원하지 않는다. 타입을 새로 만들어 컬럼 3개를
    캐스팅하고 옛 타입을 버리는 것 말고는 방법이 없다.

    **`REDUCED` 인 행이 하나라도 있으면 캐스팅에서 실패한다.** 그 행을 어느 단계로
    옮길지는 데이터 판단이라 여기서 임의로 정하지 않는다 — 실패하면 직접 정리하고
    다시 돌린다.
    """
    op.execute("ALTER TYPE medication_stage RENAME TO medication_stage_old")
    op.execute(
        "CREATE TYPE medication_stage AS ENUM (%s)"
        % ", ".join(f"'{value}'" for value in _BEFORE)
    )
    for table, column in _STAGE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"TYPE medication_stage USING {column}::text::medication_stage"
        )
    op.execute("DROP TYPE medication_stage_old")
