"""tailoring run provider metadata

`tailoring_runs` recorded which MODEL wrote a document and under which prompt
version, but not which provider adapter answered or what the call cost. The
provider's own metadata — token counts, latency, the response id, the stop
reason — reached `GenerationResult.metadata` and was dropped there, so an AI
run's cost and the reason a completion stopped could not be read back from
anything but a log line.

Adds the same two columns, with the same types, that `job_requirements` and
`resume_structured_results` already carry for their own provider calls:

    provider           the adapter's stable name ("anthropic", "openrouter");
                       NULL for the deterministic generator, which calls nothing
    provider_metadata  counts, identifiers and timings only — never the prompt,
                       never the completion, never a credential

Both nullable, with no default and no backfill: a run written before this
revision genuinely has no record of either, and inventing one would be a
provenance claim nobody made.

`trg_tailoring_runs_transition` needs no change. It freezes a terminal run's
outcome columns by name, and these are not outcome columns.

Revision ID: d4a7c2e9b815
Revises: c3d9e1f47a20
Create Date: 2026-09-25 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4a7c2e9b815"
down_revision: Union[str, Sequence[str], None] = "c3d9e1f47a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tailoring_runs",
        sa.Column("provider", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tailoring_runs",
        sa.Column(
            "provider_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tailoring_runs", "provider_metadata")
    op.drop_column("tailoring_runs", "provider")
