"""meal_feedbacks.suggestions to jsonb

Revision ID: c4e81b52f7a9
Revises: 817327c34a0e
Create Date: 2026-09-23 10:20:00.000000

명세의 `feedback.suggestions` 는 `{foodName, nutrients, advice}` 객체 배열이고 AI 도
배열로 준다(`ai-stub/schemas.py::ShortFeedbackResponse.suggestions`). 컬럼만 TEXT 라
그 값을 담을 수 없었다.

**지금 옮기는 이유**: 채우는 워커(`worker/jobs/feedback_meal.py`)가 아직 스텁이라
`meal_feedbacks` 에 행이 0건이다. 데이터가 생긴 뒤에 바꾸면 `USING` 캐스팅이 기존
문자열에서 실패할 수 있다.

**`nutrients` 는 저장하지 않는다.** AI 계약이 "BE 가 food_refs 에서 채운다" 고 적어
두었고, 저장하면 `food_refs` 갱신 시 낡는다. `candidateFoodRefId` 만 담고 읽을 때
조회한다.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c4e81b52f7a9'
down_revision: Union[str, Sequence[str], None] = 'c5e1a37b92d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 행이 0건이라 캐스팅이 실패할 데이터가 없다. 그래도 USING 을 명시하는 건
    # 로컬에 손으로 넣어 본 행이 있을 수 있어서다 — 그 경우 유효한 JSON 이 아니면
    # 여기서 시끄럽게 죽는 게 낫다.
    op.alter_column(
        "meal_feedbacks",
        "suggestions",
        existing_type=sa.Text(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="suggestions::jsonb",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "meal_feedbacks",
        "suggestions",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.Text(),
        existing_nullable=True,
        postgresql_using="suggestions::text",
    )
