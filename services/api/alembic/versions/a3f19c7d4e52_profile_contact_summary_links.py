"""profile contact, summary and links

A resume's first six lines are its name, location, phone, email and links, and
its next paragraph is the summary. None of that had anywhere to go: the profile
snapshot modelled only experiences, education, skills, projects and
certifications, so every one of those facts was read off the page and then
dropped on the way to the profile. The loss was silent — the pipeline reported
`completed` — and it was not recoverable downstream, because a fact absent from
the schema cannot be put back by the UI.

Five nullable columns on `profile_versions`:

    full_name, email, phone, location    one each per person, so columns rather
                                         than a 1:1 table that buys a join and
                                         nothing else.
    summary                              the candidate's own paragraph, as
                                         written. Never composed by us.

and one table:

    profile_links   (label, url) per version. A table rather than a
                    `linkedin_url`/`github_url` pair, which could not hold a
                    second repository or a personal site, and which every resume
                    that puts a link on each project would overflow immediately.

All nullable and additive, so every version written before this migration stays
valid and keeps its meaning: no contact details recorded, rather than blank ones.
Nothing is backfilled — the source documents would have to be re-read to know,
and inventing a name for a version that never had one is the fabrication this
domain exists to prevent. Re-running a resume through the pipeline produces a
new draft version that does carry them.

Revision ID: a3f19c7d4e52
Revises: cf65244be849
Create Date: 2026-09-22 10:15:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f19c7d4e52"
down_revision: Union[str, Sequence[str], None] = "cf65244be849"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "profile_versions", sa.Column("full_name", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "profile_versions", sa.Column("email", sa.String(length=320), nullable=True)
    )
    op.add_column(
        "profile_versions", sa.Column("phone", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "profile_versions", sa.Column("location", sa.String(length=255), nullable=True)
    )
    op.add_column("profile_versions", sa.Column("summary", sa.Text(), nullable=True))

    op.create_table(
        "profile_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_version_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.CheckConstraint("length(url) > 0", name="ck_profile_links_url_not_empty"),
        sa.ForeignKeyConstraint(
            ["profile_version_id"],
            ["profile_versions.id"],
            name="fk_profile_links_profile_version_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_profile_links"),
    )
    op.create_index(
        "ix_profile_links_profile_version_id",
        "profile_links",
        ["profile_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_profile_links_profile_version_id", table_name="profile_links")
    op.drop_table("profile_links")
    op.drop_column("profile_versions", "summary")
    op.drop_column("profile_versions", "location")
    op.drop_column("profile_versions", "phone")
    op.drop_column("profile_versions", "email")
    op.drop_column("profile_versions", "full_name")
