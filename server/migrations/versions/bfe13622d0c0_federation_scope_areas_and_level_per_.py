"""federation scope: areas and level per followed lab

Revision ID: bfe13622d0c0
Revises: d8f3b6a2c517
Create Date: 2026-11-21 01:08:51.291763

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bfe13622d0c0'
down_revision: Union[str, Sequence[str], None] = 'd8f3b6a2c517'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # existing rows: no filter, everything listed so far stays in scope
    op.add_column('federated_papers', sa.Column('in_scope', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column(
        'federated_papers',
        sa.Column('sender', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column('federated_papers', sa.Column('import_error', sa.Text(), nullable=True))
    op.add_column('federated_papers', sa.Column('imported_version', sa.Integer(), nullable=True))
    # a local copy so far always had the version listed
    op.execute("UPDATE federated_papers SET imported_version = version WHERE paper_id IS NOT NULL")
    op.add_column(
        'peers',
        sa.Column('domains', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column('peers', sa.Column('min_level', sa.String(length=4), nullable=True))


def downgrade() -> None:
    op.drop_column('peers', 'min_level')
    op.drop_column('peers', 'domains')
    op.drop_column('federated_papers', 'imported_version')
    op.drop_column('federated_papers', 'import_error')
    op.drop_column('federated_papers', 'sender')
    op.drop_column('federated_papers', 'in_scope')
