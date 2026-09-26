"""add retry backoff schedule

Revision ID: bc392d2921e1
Revises: e2ff16dd2e81
Create Date: 2026-09-18 22:50:32.501684

Backoff, as a column.

A transient failure is usually something nearby having a bad minute, and
retrying into that minute turns one rate limit into a rate-limit loop. The wait
between attempts lives here, on the successor run, because a delay that is a row
survives a restart, is visible to every process at once, and cannot be lost with
a message the way an in-memory timer or a delayed queue entry can.

No new processing status came with it. A run that is `uploaded` with a
next_retry_at in the future is already exactly "queued, not yet due" — inventing
a state to say so again would put the same fact in two places.

Deliberately NOT added: a CHECK that next_retry_at >= created_at. It would
compare an application clock against this table's own server_default, so
ordinary skew between the two would reject the INSERT and destroy the retry. The
case it would forbid is harmless anyway — a time already past simply reads as
due now.

No backfill. Existing runs get NULL, which means "nothing is holding this back"
— the correct answer for every run that predates backoff.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bc392d2921e1'
down_revision: Union[str, Sequence[str], None] = 'e2ff16dd2e81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'resume_processing_runs',
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
    )
    # "Which scheduled retries have come due?" — asked on every reaper sweep,
    # against a table where almost every row is terminal and almost every
    # non-terminal row has no retry time at all. Partial on both, so the index
    # holds only the handful of rows that are actually waiting.
    op.create_index(
        'ix_resume_processing_runs_due_retry',
        'resume_processing_runs',
        ['next_retry_at'],
        unique=False,
        postgresql_where=sa.text(
            "status NOT IN ('completed', 'failed') AND next_retry_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_resume_processing_runs_due_retry',
        table_name='resume_processing_runs',
        postgresql_where=sa.text(
            "status NOT IN ('completed', 'failed') AND next_retry_at IS NOT NULL"
        ),
    )
    op.drop_column('resume_processing_runs', 'next_retry_at')
