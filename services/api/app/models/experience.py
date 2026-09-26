from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.date_precision import (
    PRECISION_DAY,
    precision_pairing_check,
    precision_values_check,
)

EMPLOYMENT_TYPES = (
    "full_time",
    "part_time",
    "contract",
    "internship",
    "freelance",
    "temporary",
)


class Experience(Base):
    """A position held, as confirmed in one profile version."""

    __tablename__ = "experiences"

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_experiences_end_date_after_start_date",
        ),
        CheckConstraint(
            precision_values_check("start_date_precision"),
            name="ck_experiences_start_date_precision",
        ),
        CheckConstraint(
            precision_values_check("end_date_precision"),
            name="ck_experiences_end_date_precision",
        ),
        CheckConstraint(
            precision_pairing_check("end_date", "end_date_precision"),
            name="ck_experiences_end_date_precision_paired",
        ),
        CheckConstraint(
            "employment_type IS NULL OR employment_type IN ("
            + ", ".join(f"'{value}'" for value in EMPLOYMENT_TYPES)
            + ")",
            name="ck_experiences_employment_type",
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
            name="fk_experiences_profile_version_id",
        ),
        nullable=False,
        # Postgres indexes primary keys and unique constraints automatically,
        # but never foreign keys. Without this, loading a version's experiences
        # and cascading its deletion both scan the whole table.
        index=True,
    )

    company: Mapped[str] = mapped_column(String(255), nullable=False)

    job_title: Mapped[str] = mapped_column(String(255), nullable=False)

    employment_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)

    start_date_precision: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=PRECISION_DAY,
    )

    # NULL means the position is current. A separate is_current flag would be a
    # second copy of the same fact, free to drift away from this one.
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    end_date_precision: Mapped[str | None] = mapped_column(String(8), nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile_version: Mapped["ProfileVersion"] = relationship(  # noqa: F821
        back_populates="experiences",
    )

    @property
    def is_current(self) -> bool:
        return self.end_date is None

    def __repr__(self) -> str:
        return f"<Experience id={self.id} company={self.company!r} title={self.job_title!r}>"
