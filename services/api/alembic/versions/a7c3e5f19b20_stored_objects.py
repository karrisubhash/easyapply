"""stored objects

A place for the database storage backend (app/services/storage.py
DatabaseStorage, STORAGE_BACKEND=database) to keep an object's bytes, for a
deployment with no persistent disk and no object store — the free-tier staging
environment (docs/staging-deployment.md), whose container filesystem is wiped
on every restart.

    stored_objects    key (the storage key, primary key), content_type, data,
                      size_bytes (checked against the data), created_at

Additive: no existing table or row changes, and with the default filesystem
backend the table simply stays empty.

Downgrade refuses while the table holds any object — dropping it would delete
uploaded resumes and generated documents that other rows still reference by key.

Revision ID: a7c3e5f19b20
Revises: f1a9d3c5b7e2
Create Date: 2026-09-26 19:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e5f19b20"
down_revision: Union[str, Sequence[str], None] = "f1a9d3c5b7e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stored_objects",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_stored_objects"),
        sa.CheckConstraint("length(key) > 0", name="ck_stored_objects_key_non_empty"),
        sa.CheckConstraint("size_bytes = octet_length(data)", name="ck_stored_objects_size_matches"),
    )


def downgrade() -> None:
    held = op.get_bind().execute(sa.text("SELECT count(*) FROM stored_objects")).scalar_one()
    if held:
        raise RuntimeError(
            f"Cannot downgrade past a7c3e5f19b20: stored_objects holds {held} object(s) — uploaded "
            "resumes and generated documents other rows reference by key. Move them to another "
            "storage backend deliberately first; this revision will not delete them."
        )
    op.drop_table("stored_objects")
