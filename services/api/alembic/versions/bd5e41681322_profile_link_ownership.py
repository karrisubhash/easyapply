"""profile link ownership

A resume's own hyperlink annotations are read wherever they sit on the page —
the header's LinkedIn/GitHub/portfolio row, but just as much a project's own
Demo/GitHub links three sections further down the SAME page. `profile_links`
had no way to say which was which, so every one of them was read onto the
version's contact block: a project's own repository link, presented as the
candidate's personal one.

Two columns:

    owner_type   'personal' or 'project'. Which kind of link this row is.
    project_id   which `Project` a project-owned link belongs to. Null for a
                 personal link — checked below, not merely documented.

Existing rows predate ownership entirely and are backfilled as personal: every
one of them was read before the deterministic structurer's project-link
extraction existed, so every one of them really was a header link at the time,
and 'personal' is not a guess about them, it is what they already were.

Revision ID: bd5e41681322
Revises: a3f19c7d4e52
Create Date: 2026-09-23 09:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bd5e41681322"
down_revision: Union[str, Sequence[str], None] = "a3f19c7d4e52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "profile_links",
        sa.Column(
            "owner_type",
            sa.String(length=16),
            nullable=False,
            server_default="personal",
        ),
    )
    op.add_column(
        "profile_links", sa.Column("project_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_profile_links_project_id",
        "profile_links",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_profile_links_project_id", "profile_links", ["project_id"]
    )
    op.create_check_constraint(
        "ck_profile_links_owner_type",
        "profile_links",
        "owner_type IN ('personal', 'project')",
    )
    op.create_check_constraint(
        "ck_profile_links_project_id_matches_owner_type",
        "profile_links",
        "(owner_type = 'personal' AND project_id IS NULL) OR "
        "(owner_type = 'project' AND project_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_profile_links_project_id_matches_owner_type",
        "profile_links",
        type_="check",
    )
    op.drop_constraint(
        "ck_profile_links_owner_type", "profile_links", type_="check"
    )
    op.drop_index("ix_profile_links_project_id", table_name="profile_links")
    op.drop_constraint(
        "fk_profile_links_project_id", "profile_links", type_="foreignkey"
    )
    op.drop_column("profile_links", "project_id")
    op.drop_column("profile_links", "owner_type")
