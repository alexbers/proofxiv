"""watch events: the summary says whether the weekly run confirmed or rejected the event

Revision ID: a4e1c7d93f20
Revises: cdf4df118d67
Create Date: 2026-11-17 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a4e1c7d93f20'
down_revision: Union[str, Sequence[str], None] = 'cdf4df118d67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNCHECKED = "Found by the automatic watch, not checked yet: "
DECIDED = {
    "true": "Found by the automatic watch, confirmed by the weekly run: ",
    "false": "Found by the automatic watch, rejected by the weekly run: ",
}


def upgrade() -> None:
    """Events decided before the summary was rewritten on decision."""
    for confirmed, prefix in DECIDED.items():
        op.execute(
            f"UPDATE external_events SET summary = '{prefix}' || substr(summary, {len(UNCHECKED) + 1}) "
            f"WHERE auto_source IS NOT NULL AND confirmed = {confirmed} AND starts_with(summary, '{UNCHECKED}')"
        )


def downgrade() -> None:
    for prefix in DECIDED.values():
        op.execute(
            f"UPDATE external_events SET summary = '{UNCHECKED}' || substr(summary, {len(prefix) + 1}) "
            f"WHERE auto_source IS NOT NULL AND starts_with(summary, '{prefix}')"
        )
