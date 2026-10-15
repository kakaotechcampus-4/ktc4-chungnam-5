"""add chat tables

Revision ID: b7e2c4a9d1f3
Revises: 9a1c4e7b2d3f
Create Date: 2026-10-15 14:00:00.000000

챗봇(AI 코치) 대화 · 메시지 · 추천 음식 카드 (`contracts/ChatbotAPI.md`).
새 테이블뿐이라 기존 데이터 이전은 없다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7e2c4a9d1f3'
down_revision: Union[str, Sequence[str], None] = '9a1c4e7b2d3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 새 ENUM 은 테이블보다 먼저 만들고, 테이블을 지운 뒤에 지운다 (init_schema 와 같은 방식).
CHAT_MESSAGE_ROLE = postgresql.ENUM('USER', 'ASSISTANT', name='chat_message_role', create_type=False)
CHAT_MESSAGE_STATUS = postgresql.ENUM(
    'GENERATING', 'READY', 'FAILED', name='chat_message_status', create_type=False
)
# 이미 있는 타입이다 — 만들지도 지우지도 않는다.
SAFETY_STATUS = postgresql.ENUM('SAFE', 'BLOCKED', 'REVIEW_REQUIRED', name='safety_status', create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    CHAT_MESSAGE_ROLE.create(bind, checkfirst=True)
    CHAT_MESSAGE_STATUS.create(bind, checkfirst=True)

    op.create_table(
        'chat_conversations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('meal_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name='fk_chat_conversations_user_id_users', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['meal_id'], ['meals.id'], name='fk_chat_conversations_meal_id_meals', ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id', name='pk_chat_conversations'),
    )
    op.create_index(
        'uq_chat_conversations_open',
        'chat_conversations',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text('closed_at IS NULL'),
    )

    op.create_table(
        'chat_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('conversation_id', sa.Uuid(), nullable=False),
        sa.Column('role', CHAT_MESSAGE_ROLE, nullable=False),
        sa.Column('status', CHAT_MESSAGE_STATUS, nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('safety_status', SAFETY_STATUS, nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['conversation_id'],
            ['chat_conversations.id'],
            name='fk_chat_messages_conversation_id_chat_conversations',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_chat_messages'),
    )
    op.create_index(
        'ix_chat_messages_conversation_id_created_at',
        'chat_messages',
        ['conversation_id', 'created_at'],
    )

    op.create_table(
        'chat_food_cards',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('message_id', sa.Uuid(), nullable=False),
        sa.Column('position', sa.SmallInteger(), nullable=False),
        sa.Column('food_name', sa.String(length=255), nullable=False),
        sa.Column('advice', sa.Text(), nullable=False),
        # FK 없음 — AI 가 준 id 라 food_refs 에 없을 수 있다 (models/chat.py).
        sa.Column('food_ref_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ['message_id'],
            ['chat_messages.id'],
            name='fk_chat_food_cards_message_id_chat_messages',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_chat_food_cards'),
        # 이 UNIQUE 의 인덱스가 message_id 로 시작하므로 message_id 단독 인덱스는 두지 않는다.
        sa.UniqueConstraint('message_id', 'position', name='uq_chat_food_cards_message_id_position'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('chat_food_cards')
    op.drop_index('ix_chat_messages_conversation_id_created_at', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index('uq_chat_conversations_open', table_name='chat_conversations')
    op.drop_table('chat_conversations')

    bind = op.get_bind()
    CHAT_MESSAGE_STATUS.drop(bind, checkfirst=True)
    CHAT_MESSAGE_ROLE.drop(bind, checkfirst=True)
