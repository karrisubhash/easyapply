"""add run lease and structured result audit

Revision ID: e2ff16dd2e81
Revises: 3c1111315a6b
Create Date: 2026-09-18 22:09:10.434856

Two things arrive together here because they are the two halves of moving
processing off the request:

The lease makes resume_processing_runs the work queue. A run that is not
terminal and not leased is outstanding work; a leased one is being worked on by
whoever the row names, until the moment the row says. Both facts have to be in
the database because the process that asks is never the process that answers —
a reaper asking about a worker that no longer exists.

The audit columns make an AI reading accountable. Once a model rather than a
heuristic produces the payload, "what produced this?" needs provider, model,
prompt and schema, or an old result is a claim with no provenance of its own.

No backfill. Existing runs get NULL leases, which reads as "not leased" — the
correct answer for runs that completed before leases existed. Existing results
get NULL provider and versions, which reads as "produced before we recorded
this", and inventing a version for them would be worse than admitting it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e2ff16dd2e81'
down_revision: Union[str, Sequence[str], None] = '3c1111315a6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- the lease -----------------------------------------------------------
    op.add_column('resume_processing_runs', sa.Column('lease_owner', sa.String(length=128), nullable=True))
    op.add_column('resume_processing_runs', sa.Column('leased_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('resume_processing_runs', sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True))

    # A lease is an owner and an expiry. Half of one is a row nothing can
    # reason about: an owner with no expiry never lapses, and an expiry with no
    # owner names nobody to take it from.
    op.create_check_constraint(
        'ck_resume_processing_runs_lease_paired',
        'resume_processing_runs',
        '(lease_owner IS NULL) = (leased_until IS NULL)',
    )

    # The claim query and the reaper both read "not terminal, oldest lease
    # first". Partial on the same predicate, because terminal runs are the bulk
    # of this table forever and are never claimable.
    op.create_index(
        'ix_resume_processing_runs_claimable',
        'resume_processing_runs',
        ['leased_until', 'id'],
        unique=False,
        postgresql_where=sa.text("status NOT IN ('completed', 'failed')"),
    )

    # --- what produced a reading ---------------------------------------------
    op.add_column('resume_structured_results', sa.Column('provider', sa.String(length=64), nullable=True))
    op.add_column('resume_structured_results', sa.Column('schema_version', sa.String(length=32), nullable=True))
    op.add_column('resume_structured_results', sa.Column('prompt_version', sa.String(length=32), nullable=True))
    # Counts, ids and timings from the provider. Never the prompt, never the
    # completion, never a credential — this column is read by anyone auditing a
    # result, and it is the obvious place for a secret to end up by accident.
    op.add_column('resume_structured_results', sa.Column('provider_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('resume_structured_results', 'provider_metadata')
    op.drop_column('resume_structured_results', 'prompt_version')
    op.drop_column('resume_structured_results', 'schema_version')
    op.drop_column('resume_structured_results', 'provider')

    op.drop_index('ix_resume_processing_runs_claimable', table_name='resume_processing_runs', postgresql_where=sa.text("status NOT IN ('completed', 'failed')"))
    op.drop_constraint('ck_resume_processing_runs_lease_paired', 'resume_processing_runs', type_='check')
    op.drop_column('resume_processing_runs', 'heartbeat_at')
    op.drop_column('resume_processing_runs', 'leased_until')
    op.drop_column('resume_processing_runs', 'lease_owner')
