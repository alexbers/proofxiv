"""paper significance

Revision ID: c3f19a7b5e60
Revises: 8e4a0c6f2d17
Create Date: 2026-11-02 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c3f19a7b5e60'
down_revision: Union[str, Sequence[str], None] = '8e4a0c6f2d17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('papers', sa.Column('significance', sa.Float(), server_default='0', nullable=False))
    op.add_column(
        'papers',
        sa.Column(
            'significance_notes',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='[]',
            nullable=False,
        ),
    )
    op.create_index(op.f('ix_papers_significance'), 'papers', ['significance'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_papers_significance'), table_name='papers')
    op.drop_column('papers', 'significance_notes')
    op.drop_column('papers', 'significance')
