"""run job claim ids

Revision ID: 8e4a0c6f2d17
Revises: 5b1d7e3a9c42
Create Date: 2026-11-02 01:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8e4a0c6f2d17'
down_revision: Union[str, Sequence[str], None] = '5b1d7e3a9c42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'run_jobs',
        sa.Column('claim_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    )
    # jobs created before: one claim each
    op.execute("UPDATE run_jobs SET claim_ids = jsonb_build_array(claim_id::text)")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('run_jobs', 'claim_ids')
