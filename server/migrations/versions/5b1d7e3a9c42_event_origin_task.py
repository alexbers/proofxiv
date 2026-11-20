"""external event origin task

Revision ID: 5b1d7e3a9c42
Revises: 32310dc091cd
Create Date: 2026-11-01 23:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b1d7e3a9c42'
down_revision: Union[str, Sequence[str], None] = '32310dc091cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('external_events', sa.Column('origin_task_id', sa.Uuid(), nullable=True))
    op.create_index(
        op.f('ix_external_events_origin_task_id'), 'external_events', ['origin_task_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_external_events_origin_task_id'), table_name='external_events')
    op.drop_column('external_events', 'origin_task_id')
