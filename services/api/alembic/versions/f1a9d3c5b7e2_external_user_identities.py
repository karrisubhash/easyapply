"""external user identities

Real sign-in (Microsoft Entra External ID) identifies a person by who
authenticated them — the token issuer and the user's immutable object id —
never by email. This revision adds that mapping and stops requiring an email on
`users`:

    user_identities   (issuer, subject) -> users.id, UNIQUE (issuer, subject),
                      email / display_name as display metadata only.
    users.email       NULLABLE. It stays what it has always been — the
                      development identity's key (X-Dev-User-Email), still
                      unique — and is NULL for a user provisioned from an
                      external identity. Several NULLs never collide under a
                      unique index in PostgreSQL.

No existing row changes: every current user keeps its email and its data, and
no external identity is linked to any of them (linking by email would let
whoever controls an address at the provider take over the account holding it).

Downgrade refuses while any user has no email — those people exist only
through an external identity, and restoring NOT NULL would need an email this
database does not have. Otherwise it drops the table and restores NOT NULL.

Revision ID: f1a9d3c5b7e2
Revises: d4a7c2e9b815
Create Date: 2026-09-26 13:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a9d3c5b7e2"
down_revision: Union[str, Sequence[str], None] = "d4a7c2e9b815"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)

    op.create_table(
        "user_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_identities"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_user_identities_user_id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_user_identities_issuer_subject"),
        sa.CheckConstraint("length(issuer) > 0 AND length(subject) > 0", name="ck_user_identities_non_empty"),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])


def downgrade() -> None:
    without_email = op.get_bind().execute(sa.text("SELECT count(*) FROM users WHERE email IS NULL")).scalar_one()
    if without_email:
        raise RuntimeError(
            f"Cannot downgrade past f1a9d3c5b7e2: {without_email} user(s) were provisioned from an "
            "external identity and have no email, which the previous schema requires. Remove or "
            "migrate them deliberately first; this revision will not invent emails or delete accounts."
        )
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)
