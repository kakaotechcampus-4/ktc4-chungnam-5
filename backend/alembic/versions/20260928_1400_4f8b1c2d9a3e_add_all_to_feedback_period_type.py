"""add ALL to feedback_period_type

Revision ID: 4f8b1c2d9a3e
Revises: c5e1a37b92d4
Create Date: 2026-09-28 14:00:00.000000

GET /insights/long-term · POST /insights/long-term/refresh 의 `period=all` 을
받으려면 `long_term_feedbacks.period_type` 에 `ALL` 값이 있어야 한다(PR #46 리뷰).

`period_type=ALL` 행의 `period_start` 가 무엇을 뜻하는지(가입일? 첫 식사일?)는
이 마이그레이션의 범위가 아니다 — 그 값을 실제로 채우는 쪽은 아직 스켈레톤인
`worker/jobs/feedback_long.py` 라, 그 구현이 정해질 때 같이 정한다.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4f8b1c2d9a3e'
down_revision: Union[str, Sequence[str], None] = 'c5e1a37b92d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ALTER TYPE ... ADD VALUE 는 추가된 값을 같은 트랜잭션 안에서 쓸 수 없다.
    # autocommit_block 으로 빼내야 뒤이은 마이그레이션이 ALL 을 바로 쓸 수 있다
    # (add_reduced_to_medication_stage 와 같은 패턴).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE feedback_period_type ADD VALUE IF NOT EXISTS 'ALL'")


def downgrade() -> None:
    """Downgrade schema.

    PostgreSQL 은 ENUM 값 삭제를 지원하지 않는다. 타입을 새로 만들어 컬럼을
    캐스팅하고 옛 타입을 버리는 것 말고는 방법이 없다.

    **`ALL` 인 행이 하나라도 있으면 캐스팅에서 실패한다.** 그 행을 어떻게 할지는
    데이터 판단이라 여기서 임의로 정하지 않는다 — 실패하면 직접 정리하고 다시 돌린다.
    """
    op.execute("ALTER TYPE feedback_period_type RENAME TO feedback_period_type_old")
    op.execute("CREATE TYPE feedback_period_type AS ENUM ('WEEKLY', 'MONTHLY')")
    op.execute(
        "ALTER TABLE long_term_feedbacks ALTER COLUMN period_type "
        "TYPE feedback_period_type USING period_type::text::feedback_period_type"
    )
    op.execute("DROP TYPE feedback_period_type_old")
