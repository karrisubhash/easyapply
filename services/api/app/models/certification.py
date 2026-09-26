from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Certification(Base):
    """A credential earned, as confirmed in one profile version."""

    __tablename__ = "certifications"

    __table_args__ = (
        CheckConstraint(
            "expiration_date IS NULL OR issue_date IS NULL OR expiration_date >= issue_date",
            name="ck_certifications_expiration_after_issue",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "profile_versions.id",
            ondelete="CASCADE",
            name="fk_certifications_profile_version_id",
        ),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    issuing_organization: Mapped[str | None] = mapped_column(String(255), nullable=True)

    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Plenty of credentials never expire, so this stays nullable and carries no
    # default. credential_id is not unique: issuers only guarantee it within
    # their own namespace.
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    credential_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    credential_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile_version: Mapped["ProfileVersion"] = relationship(  # noqa: F821
        back_populates="certifications",
    )

    def __repr__(self) -> str:
        return f"<Certification id={self.id} name={self.name!r}>"
