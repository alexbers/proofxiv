"""federation: outbox, peers and their keys, federated papers, origin of papers and operators

Revision ID: d8f3b6a2c517
Revises: c5e8a1f4d920
Create Date: 2026-11-20 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd8f3b6a2c517'
down_revision: Union[str, Sequence[str], None] = 'c5e8a1f4d920'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES = ('reviewer', 'runner', 'federation', 'canary', 'juror')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('federation_outbox',
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('seq', name=op.f('pk_federation_outbox'))
    )
    op.create_table('peers',
    sa.Column('base_url', sa.String(length=300), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('owner_key', sa.String(length=64), nullable=False),
    sa.Column('trust', sa.Enum('index', 'review', 'full', name='peertrust', native_enum=False, length=40), nullable=False),
    sa.Column('cursor', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_pulled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_peers')),
    sa.UniqueConstraint('base_url', name=op.f('uq_peers_base_url'))
    )
    op.create_table('peer_keys',
    sa.Column('peer_id', sa.Uuid(), nullable=False),
    sa.Column('public_key', sa.String(length=64), nullable=False),
    sa.Column('fingerprint', sa.String(length=32), nullable=False),
    sa.Column('role', sa.Enum(*ROLES, name='role', native_enum=False, length=40), nullable=False),
    sa.Column('operator_login', sa.String(length=64), nullable=True),
    sa.Column('attested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoke_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['peer_id'], ['peers.id'], name=op.f('fk_peer_keys_peer_id_peers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_peer_keys')),
    sa.UniqueConstraint('peer_id', 'fingerprint', 'role', name=op.f('uq_peer_keys_peer_id'))
    )
    op.create_index(op.f('ix_peer_keys_peer_id'), 'peer_keys', ['peer_id'], unique=False)
    op.add_column('papers', sa.Column('origin_peer_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_papers_origin_peer_id'), 'papers', ['origin_peer_id'], unique=False)
    op.create_foreign_key(op.f('fk_papers_origin_peer_id_peers'), 'papers', 'peers', ['origin_peer_id'], ['id'])
    op.add_column('operators', sa.Column('peer_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_operators_peer_id_peers'), 'operators', 'peers', ['peer_id'], ['id'])
    op.add_column('decisive_tasks', sa.Column('materials', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table('federated_papers',
    sa.Column('peer_id', sa.Uuid(), nullable=False),
    sa.Column('remote_id', sa.String(length=64), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('abstract', sa.Text(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('level', sa.String(length=20), nullable=True),
    sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('files', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('problems', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('problem_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('withdrawn', sa.Boolean(), nullable=False),
    sa.Column('paper_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['papers.id'], name=op.f('fk_federated_papers_paper_id_papers')),
    sa.ForeignKeyConstraint(['peer_id'], ['peers.id'], name=op.f('fk_federated_papers_peer_id_peers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_federated_papers')),
    sa.UniqueConstraint('peer_id', 'remote_id', name=op.f('uq_federated_papers_peer_id'))
    )
    op.create_index(op.f('ix_federated_papers_paper_id'), 'federated_papers', ['paper_id'], unique=False)
    op.create_index(op.f('ix_federated_papers_peer_id'), 'federated_papers', ['peer_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_federated_papers_peer_id'), table_name='federated_papers')
    op.drop_index(op.f('ix_federated_papers_paper_id'), table_name='federated_papers')
    op.drop_table('federated_papers')
    op.drop_column('decisive_tasks', 'materials')
    op.drop_constraint(op.f('fk_operators_peer_id_peers'), 'operators', type_='foreignkey')
    op.drop_column('operators', 'peer_id')
    op.drop_constraint(op.f('fk_papers_origin_peer_id_peers'), 'papers', type_='foreignkey')
    op.drop_index(op.f('ix_papers_origin_peer_id'), table_name='papers')
    op.drop_column('papers', 'origin_peer_id')
    op.drop_index(op.f('ix_peer_keys_peer_id'), table_name='peer_keys')
    op.drop_table('peer_keys')
    op.drop_table('peers')
    op.drop_table('federation_outbox')
