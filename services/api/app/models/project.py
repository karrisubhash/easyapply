from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.date_precision import (
    precision_pairing_check,
    precision_values_check,
)


class Project(Base):
    """A project built, as confirmed in one profile version."""

    __tablename__ = "projects"

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="ck_projects_end_date_after_start_date",
        ),
        CheckConstraint(
            precision_values_check("start_date_precision"),
            name="ck_projects_start_date_precision",
        ),
        CheckConstraint(
            precision_values_check("end_date_precision"),
            name="ck_projects_end_date_precision",
        ),
        CheckConstraint(
            precision_pairing_check("start_date", "start_date_precision"),
            name="ck_projects_start_date_precision_paired",
        ),
        CheckConstraint(
            precision_pairing_check("end_date", "end_date_precision"),
            name="ck_projects_end_date_precision_paired",
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
            name="fk_projects_profile_version_id",
        ),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    role: Mapped[str | None] = mapped_column(String(255), nullable=True)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    start_date_precision: Mapped[str | None] = mapped_column(String(8), nullable=True)

    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    end_date_precision: Mapped[str | None] = mapped_column(String(8), nullable=True)

    project_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile_version: Mapped["ProfileVersion"] = relationship(  # noqa: F821
        back_populates="projects",
    )

    technologies: Mapped[list["ProjectTechnology"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectTechnology.name",
    )

    # This project's OWN links — a demo, a repository — never the person's.
    # `ProfileLink.owner_type` is 'project' for every row here; see that
    # model's docstring for why the same table also holds the contact block's
    # links and what keeps the two from mixing.
    links: Mapped[list["ProfileLink"]] = relationship(  # noqa: F821
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProfileLink.id",
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id} name={self.name!r}>"


class ProjectTechnology(Base):
    """One technology used on one project.

    A child table rather than a comma-separated string, so that a technology can
    later gain a foreign key into a skill taxonomy without reshaping the data.
    """

    __tablename__ = "project_technologies"

    project_id: Mapped[int] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_technologies_project_id",
        ),
        primary_key=True,
    )

    # The composite primary key is also the uniqueness rule: a technology cannot
    # be listed twice on the same project.
    name: Mapped[str] = mapped_column(String(128), primary_key=True)

    project: Mapped["Project"] = relationship(back_populates="technologies")

    def __repr__(self) -> str:
        return f"<ProjectTechnology project_id={self.project_id} name={self.name!r}>"
