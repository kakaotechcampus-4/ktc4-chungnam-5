"""add REDUCED to medication_stage

Revision ID: 817327c34a0e
Revises: d2a7c5f41e08
Create Date: 2026-09-20 22:20:00.000000

단계를 5개로 확정한 팀 결정을 반영한다.

    명세  INITIAL | TITRATION | MAINTENANCE          3개
    DB    + PRE_DOSE                                 4개  ← 여기까지가 기존
    확정  + REDUCED                                  5개

`PRE_DOSE` 는 init_schema 부터 DB 에 있었다. 실제로 추가되는 건 `REDUCED` 하나다.

**`down_revision` 을 develop 의 head 에 맞춰 다시 잡았다.** 처음엔 `fb9324353300`
이었는데 그 사이 `task_queue`(#27) · `is_recalculation`(#25) · 양 컬럼 3건(#31·#33)이
develop 에 들어가 체인이 길어졌다. 옛 값을 두면 head 가 둘로 갈려
`alembic upgrade head` 가 *Multiple head revisions are present* 로 죽는다.

**git 충돌로는 안 잡힌다.** 머지 버튼까지 그냥 통과하고 팀원이 DB 를 올릴 때 터진다.
머지 직전에 `origin/develop` 의 head 를 다시 확인할 것 — 이 값은 develop 이 움직일
때마다 낡는다.

순서를 옮겨도 안전하다. 이 리비전은 `ALTER TYPE ... ADD VALUE` 하나뿐이라 앞선
리비전들이 만드는 테이블·컬럼과 겹치지 않는다.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '817327c34a0e'
down_revision: Union[str, Sequence[str], None] = 'd2a7c5f41e08'
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
