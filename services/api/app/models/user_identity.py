"""An external identity a user signs in with — the account's real key.

A person's EasyApply account is found by WHO AUTHENTICATED THEM, never by an
address they typed or an email a provider reported:

    (issuer, subject)  ->  user_identities  ->  users.id  ->  everything they own

For Microsoft Entra External ID the issuer column holds a canonical provider
key, `entra:<tenant GUID>` (lower case) — not the issuer URL, which is
configuration and can change (custom domains, a corrected ENTRA_ISSUER) —
and the subject is the lower-cased `oid` claim: the immutable user object id,
the same for every application in the tenant (unlike `sub`, which is pairwise
per application). The issuer URL is still checked on every assertion; it is
validation, not the key. Microsoft's own
guidance is that email, `preferred_username` and `name` are mutable and must
never be used to identify a user or store their data, so `email` and
`display_name` here are metadata for display, refreshed when the provider says
they changed, and never looked up by.

The unique constraint on (issuer, subject) is what makes provisioning
race-safe: two first sign-ins of one person at the same moment can create at
most one mapping, and the loser reads the winner's row
(app/api/assertion_identity.py).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserIdentity(Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_user_identities_issuer_subject"),
        CheckConstraint("length(issuer) > 0 AND length(subject) > 0", name="ck_user_identities_non_empty"),
        Index("ix_user_identities_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # RESTRICT, like every other reference to users: erasing a person is a
    # deliberate procedure, not a side effect.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_user_identities_user_id"),
        nullable=False,
    )

    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)

    # Display metadata only — see the module docstring.
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship()  # noqa: F821
