"""sign-in links: agents create accounts and give people a one-time link

Revision ID: e7a2c4d19b30
Revises: d0cb85493891
Create Date: 2026-11-03 06:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e7a2c4d19b30'
down_revision: Union[str, Sequence[str], None] = 'd0cb85493891'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('sign_in_links',
    sa.Column('operator_id', sa.Uuid(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['operator_id'], ['operators.id'], name=op.f('fk_sign_in_links_operator_id_operators')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sign_in_links')),
    sa.UniqueConstraint('code_hash', name=op.f('uq_sign_in_links_code_hash'))
    )
    op.create_index(op.f('ix_sign_in_links_operator_id'), 'sign_in_links', ['operator_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_sign_in_links_operator_id'), table_name='sign_in_links')
    op.drop_table('sign_in_links')
