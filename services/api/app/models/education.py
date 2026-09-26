from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.date_precision import (
    precision_pairing_check,
    precision_values_check,
)


class Education(Base):
    """A qualification studied for, as confirmed in one profile version."""

    __tablename__ = "education"

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="ck_education_end_date_after_start_date",
        ),
        CheckConstraint(
            precision_values_check("start_date_precision"),
            name="ck_education_start_date_precision",
        ),
        CheckConstraint(
            precision_values_check("end_date_precision"),
            name="ck_education_end_date_precision",
        ),
        CheckConstraint(
            precision_pairing_check("start_date", "start_date_precision"),
            name="ck_education_start_date_precision_paired",
        ),
        CheckConstraint(
            precision_pairing_check("end_date", "end_date_precision"),
            name="ck_education_end_date_precision_paired",
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
            name="fk_education_profile_version_id",
        ),
        nullable=False,
        index=True,
    )

    institution: Mapped[str] = mapped_column(String(255), nullable=False)

    degree: Mapped[str | None] = mapped_column(String(255), nullable=True)

    field_of_study: Mapped[str | None] = mapped_column(String(255), nullable=True)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    start_date_precision: Mapped[str | None] = mapped_column(String(8), nullable=True)

    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    end_date_precision: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Text, not numeric: "8.7 CGPA", "First Class" and "3.8/4.0" are all grades,
    # and a bare number would lose the scale that makes it mean anything.
    grade: Mapped[str | None] = mapped_column(String(64), nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile_version: Mapped["ProfileVersion"] = relationship(  # noqa: F821
        back_populates="education",
    )

    def __repr__(self) -> str:
        return f"<Education id={self.id} institution={self.institution!r} degree={self.degree!r}>"
