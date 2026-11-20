"""problem part_of: the larger problem a case or part belongs to, and where it stands

Revision ID: b7d2e5f81c34
Revises: a4e1c7d93f20
Create Date: 2026-11-17 13:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2e5f81c34'
down_revision: Union[str, Sequence[str], None] = 'a4e1c7d93f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('problems', sa.Column('part_of', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('problems', 'part_of')
