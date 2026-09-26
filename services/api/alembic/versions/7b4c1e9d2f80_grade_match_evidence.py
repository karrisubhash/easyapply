"""grade match evidence

matching_v1 recorded evidence as a flat list: a row, a label, a detail. The
real-data benchmark showed that losing two things costs verdicts in both
directions — how STRONG a piece of evidence is, and how much the requirement
demanded of it. A skills-list entry nobody corroborated and six years of daily
use described in a current role were the same kind of thing to the schema, so
they were the same kind of thing to the rules.

Four columns on `match_evidence` and one on `match_requirement_results`:

    tier              declared_corroborated | employment_described |
                      declared_only | mentioned. Nullable, because education
                      and certification evidence is not graded — there is no
                      stronger or weaker way to hold a degree.
    qualifies         whether THIS item met what the requirement asked for.
                      False is the interesting value: it is how an UNCERTAIN
                      shows what was found and says why it fell short.
    note              why it qualifies, or why it does not, in our own words.
    quote             the clause the token was found in. The provenance
                      guarantee applied to the profile side — a reader can
                      judge "Evaluated React Native, but the project was
                      cancelled" instead of trusting the tier we assigned.

    evidence_standard what the POSTING demanded, read from its own wording.

Additive, with server defaults, so rows written under matching_v1 stay valid
and stay readable: an old row has no tier, qualifies=true and no note, which
is exactly what it meant when it was written. Nothing is backfilled, because
inventing a tier for a judgment made before tiers existed would be a guess
recorded as a fact — the same reason `date_precision` exists elsewhere in this
schema.

Revision ID: 7b4c1e9d2f80
Revises: e0af02dfa2d8
Create Date: 2026-09-19 23:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b4c1e9d2f80"
down_revision: Union[str, Sequence[str], None] = "e0af02dfa2d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TIERS = ("declared_corroborated", "employment_described", "declared_only", "mentioned")
_STANDARDS = ("any_evidence", "professional_use")

_TIER_SQL = ", ".join(f"'{value}'" for value in _TIERS)
_STANDARD_SQL = ", ".join(f"'{value}'" for value in _STANDARDS)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("match_evidence", sa.Column("tier", sa.String(length=32), nullable=True))
    op.add_column(
        "match_evidence",
        sa.Column(
            "qualifies", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.add_column("match_evidence", sa.Column("note", sa.String(length=300), nullable=True))
    op.add_column("match_evidence", sa.Column("quote", sa.String(length=300), nullable=True))
    op.create_check_constraint(
        "ck_match_evidence_tier",
        "match_evidence",
        f"tier IS NULL OR tier IN ({_TIER_SQL})",
    )
    # "Which requirements did a bare claim fail to answer" is one predicate,
    # and it is the most actionable question this table can be asked.
    op.create_index("ix_match_evidence_tier", "match_evidence", ["tier"])

    op.add_column(
        "match_requirement_results",
        sa.Column(
            "evidence_standard",
            sa.String(length=32),
            nullable=False,
            server_default="any_evidence",
        ),
    )
    op.create_check_constraint(
        "ck_match_requirement_results_evidence_standard",
        "match_requirement_results",
        f"evidence_standard IN ({_STANDARD_SQL})",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_match_requirement_results_evidence_standard",
        "match_requirement_results",
        type_="check",
    )
    op.drop_column("match_requirement_results", "evidence_standard")

    op.drop_index("ix_match_evidence_tier", table_name="match_evidence")
    op.drop_constraint("ck_match_evidence_tier", "match_evidence", type_="check")
    op.drop_column("match_evidence", "quote")
    op.drop_column("match_evidence", "note")
    op.drop_column("match_evidence", "qualifies")
    op.drop_column("match_evidence", "tier")
