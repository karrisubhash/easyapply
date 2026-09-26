from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

PROFICIENCY_LEVELS = (
    "beginner",
    "intermediate",
    "advanced",
    "expert",
)


class Skill(Base):
    """A single skill, as confirmed in one profile version.

    One row per skill rather than a comma-separated string, because matching a
    profile against a job means querying these individually.
    """

    __tablename__ = "skills"

    __table_args__ = (
        # "Python" and "python" are the same skill. Case-insensitive uniqueness
        # needs an expression index; a UNIQUE table constraint cannot call
        # lower().
        Index(
            "uq_skills_profile_version_id_name_lower",
            "profile_version_id",
            text("lower(name)"),
            unique=True,
        ),
        CheckConstraint(
            "proficiency IS NULL OR proficiency IN ("
            + ", ".join(f"'{value}'" for value in PROFICIENCY_LEVELS)
            + ")",
            name="ck_skills_proficiency",
        ),
        CheckConstraint(
            "years_of_experience IS NULL OR years_of_experience >= 0",
            name="ck_skills_years_of_experience_not_negative",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    # The unique index above leads with this column, so it also serves lookups
    # and cascades on the foreign key.
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey(
            "profile_versions.id",
            ondelete="CASCADE",
            name="fk_skills_profile_version_id",
        ),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)

    category: Mapped[str | None] = mapped_column(String(64), nullable=True)

    proficiency: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Numeric, not integer: eighteen months of Django is 1.5, not 1 or 2.
    years_of_experience: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1),
        nullable=True,
    )

    profile_version: Mapped["ProfileVersion"] = relationship(  # noqa: F821
        back_populates="skills",
    )

    def __repr__(self) -> str:
        return f"<Skill id={self.id} name={self.name!r} proficiency={self.proficiency!r}>"
