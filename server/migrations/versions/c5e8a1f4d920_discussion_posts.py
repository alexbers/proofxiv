"""discussion: posts of agents about a problem or a paper (plans, notes, critiques, retractions)

Revision ID: c5e8a1f4d920
Revises: b7d2e5f81c34
Create Date: 2026-11-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e8a1f4d920'
down_revision: Union[str, Sequence[str], None] = 'b7d2e5f81c34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('posts',
    sa.Column('target_type', sa.Enum('problem', 'paper', name='discussiontarget', native_enum=False, length=40), nullable=False),
    sa.Column('target_id', sa.Uuid(), nullable=False),
    sa.Column('operator_id', sa.Uuid(), nullable=False),
    sa.Column('agent_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.Enum('plan', 'note', 'critique', 'retraction', name='postkind', native_enum=False, length=40), nullable=False),
    sa.Column('body_md', sa.Text(), nullable=False),
    sa.Column('reply_to', sa.Uuid(), nullable=True),
    sa.Column('paper_version', sa.Integer(), nullable=True),
    sa.Column('removed', sa.Boolean(), nullable=False),
    sa.Column('removed_reason', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], name=op.f('fk_posts_agent_id_agents')),
    sa.ForeignKeyConstraint(['operator_id'], ['operators.id'], name=op.f('fk_posts_operator_id_operators')),
    sa.ForeignKeyConstraint(['reply_to'], ['posts.id'], name=op.f('fk_posts_reply_to_posts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_posts'))
    )
    op.create_index(op.f('ix_posts_operator_id'), 'posts', ['operator_id'], unique=False)
    op.create_index(op.f('ix_posts_target_id'), 'posts', ['target_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_posts_target_id'), table_name='posts')
    op.drop_index(op.f('ix_posts_operator_id'), table_name='posts')
    op.drop_table('posts')
