"""add satiety_checkins

Revision ID: e7a3f915c26d
Revises: c4e81b52f7a9
Create Date: 2026-09-23 10:30:00.000000

`satiety_logs` 는 식사당 1행이라 "식전 · 식후" 두 값만 담는다. 명세의
`POST /meals/{mealId}/satiety-checkins` 는 식후 몇 시간 뒤 포만감을 **여러 번** 받고,
`GET /meals/{mealId}` 가 그걸 `checkins[]` 배열로 돌려준다. 담을 곳이 없었다.

`hungerReturnMinutes` · `comment` 는 여기 없다 — `satiety_logs` 에 이미 있고, 명세도
그 둘을 `checkins[]` 바깥에 둔다 (식사당 하나).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a3f915c26d'
down_revision: Union[str, Sequence[str], None] = 'c4e81b52f7a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "satiety_checkins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("meal_id", sa.Uuid(), nullable=False),
        sa.Column("checkin_offset_hours", sa.Integer(), nullable=False),
        sa.Column("satiety_pct", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # 같은 시점 재전송을 덮어쓰기로 만든다. "식후 3시간 포만감" 은 하나이고,
        # 더블탭이 그래프에 점 두 개를 만들면 안 된다. upsert 의 충돌 대상이다.
        sa.UniqueConstraint(
            "meal_id", "checkin_offset_hours", name="uq_satiety_checkins_meal_offset"
        ),
        sa.CheckConstraint(
            "checkin_offset_hours BETWEEN 0 AND 48",
            name="satiety_checkins_offset_range",
        ),
        sa.CheckConstraint(
            "satiety_pct BETWEEN 0 AND 100", name="satiety_checkins_pct_range"
        ),
    )
    # meal_id 단독 인덱스는 두지 않는다 — 위 UNIQUE (meal_id, checkin_offset_hours)
    # 의 인덱스가 meal_id 를 선두로 가져서 그 조회도 같은 인덱스를 탄다.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("satiety_checkins")
