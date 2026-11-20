"""problem variant: the Lean theorem name moves out of the title

Revision ID: f1b83d5c7a24
Revises: e7a2c4d19b30
Create Date: 2026-11-11 02:10:00.000000

"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f1b83d5c7a24'
down_revision: Union[str, Sequence[str], None] = 'e7a2c4d19b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DECLARED = re.compile(r"^(?:theorem|lemma)\s+([^\s(:{\[]+)")
FTS_OLD = "to_tsvector('simple', title || ' ' || statement_md)"
FTS_NEW = "to_tsvector('simple', title || ' ' || coalesce(variant, '') || ' ' || statement_md)"


def split_title(title: str, lean_statement: str | None) -> tuple[str, str] | None:
    """'Erdős Problem 357: erdos_357.parts.i' -> ('Erdős Problem 357', 'erdos_357.parts.i') when
    the tail is the name the Lean statement declares (imported titles were built that way; a
    file name in brackets added to tell two such names apart stays with the variant)."""
    declared = _DECLARED.match(lean_statement or "")
    if declared is None:
        return None
    at = title.rfind(": " + declared.group(1))
    if at <= 0:
        return None
    return title[:at], title[at + 2 :][:200]


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('problems', sa.Column('variant', sa.String(length=200), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, title, lean_statement FROM problems WHERE lean_statement IS NOT NULL")
    ).all()
    for problem_id, title, lean_statement in rows:
        parts = split_title(title, lean_statement)
        if parts is not None:
            conn.execute(
                sa.text("UPDATE problems SET title = :title, variant = :variant WHERE id = :id"),
                {"title": parts[0], "variant": parts[1], "id": problem_id},
            )
    op.execute("DROP INDEX IF EXISTS ix_problems_fts")
    op.execute(f"CREATE INDEX ix_problems_fts ON problems USING gin ({FTS_NEW})")
    op.execute("CREATE INDEX IF NOT EXISTS ix_problems_variant_trgm ON problems USING gin (variant gin_trgm_ops)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_problems_variant_trgm")
    op.execute("DROP INDEX IF EXISTS ix_problems_fts")
    op.execute("UPDATE problems SET title = left(title || ': ' || variant, 300) WHERE variant IS NOT NULL")
    op.drop_column('problems', 'variant')
    op.execute(f"CREATE INDEX ix_problems_fts ON problems USING gin ({FTS_OLD})")
