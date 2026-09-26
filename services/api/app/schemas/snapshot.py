"""The snapshot document.

One class per entity serving both directions rather than a Write/Read pair:
the snapshot is submitted and returned in the same shape, and two near-identical
classes would be two places to keep in step.

These schemas own SHAPE — types, required-ness, allowed literals, lengths.
They deliberately do NOT re-implement the database's CHECK constraints (the
date/precision pairing, the date ordering, case-insensitive skill uniqueness).
Those are predicates over state, and restating them in Python is exactly the
"duplicating the invariant inconsistently" we are trying to avoid; the database
raises and app/api/errors.py turns it into a 422.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Mirrors of the value sets the database CHECK constraints allow. The values are
# mirrored, never the predicates; tests assert these stay in step with the model
# constants so they cannot drift.
DatePrecision = Literal["year", "month", "day"]
EmploymentType = Literal[
    "full_time",
    "part_time",
    "contract",
    "internship",
    "freelance",
    "temporary",
]
Proficiency = Literal["beginner", "intermediate", "advanced", "expert"]


class SnapshotItem(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ContactItem(SnapshotItem):
    """How to reach the person this profile describes.

    Every field is optional: a resume that gives a name and nothing else is a
    resume, and refusing it would fail the whole snapshot over a missing phone
    number.
    """

    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=255)


class LinkItem(SnapshotItem):
    url: str = Field(max_length=2048)
    label: str | None = Field(default=None, max_length=100)


class ExperienceItem(SnapshotItem):
    company: str = Field(max_length=255)
    job_title: str = Field(max_length=255)
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, max_length=255)
    start_date: date
    start_date_precision: DatePrecision | None = None
    end_date: date | None = None
    end_date_precision: DatePrecision | None = None
    description: str | None = None


class EducationItem(SnapshotItem):
    institution: str = Field(max_length=255)
    degree: str | None = Field(default=None, max_length=255)
    field_of_study: str | None = Field(default=None, max_length=255)
    start_date: date | None = None
    start_date_precision: DatePrecision | None = None
    end_date: date | None = None
    end_date_precision: DatePrecision | None = None
    grade: str | None = Field(default=None, max_length=64)
    description: str | None = None


class SkillItem(SnapshotItem):
    name: str = Field(max_length=128)
    category: str | None = Field(default=None, max_length=64)
    proficiency: Proficiency | None = None
    years_of_experience: Decimal | None = Field(default=None, ge=0, max_digits=4, decimal_places=1)


class ProjectItem(SnapshotItem):
    name: str = Field(max_length=255)
    description: str | None = None
    role: str | None = Field(default=None, max_length=255)
    start_date: date | None = None
    start_date_precision: DatePrecision | None = None
    end_date: date | None = None
    end_date_precision: DatePrecision | None = None
    project_url: str | None = None
    # Flattens the project_technologies child table, which holds nothing but
    # (project_id, name). Each name is bounded by that column's String(128):
    # unbounded, a longer one passed validation and failed at INSERT, which
    # cost the whole snapshot rather than one field and surfaced as a 500 (or,
    # from resume ingestion, a draft_creation_failed retried to exhaustion).
    technologies: list[Annotated[str, Field(max_length=128)]] = Field(default_factory=list)
    # This project's own links — a demo, a repository — as distinct from
    # `Snapshot.links`, which is the person's own. `ProfileLink.owner_type` is
    # what keeps the two apart in the database; this is the same distinction on
    # the wire. See app/models/profile_link.py.
    links: list[LinkItem] = Field(default_factory=list)

    @field_validator("technologies", mode="before")
    @classmethod
    def _flatten(cls, value):
        """Accept either submitted strings or ProjectTechnology rows."""
        return [item if isinstance(item, str) else item.name for item in value]


class CertificationItem(SnapshotItem):
    name: str = Field(max_length=255)
    issuing_organization: str | None = Field(default=None, max_length=255)
    issue_date: date | None = None
    expiration_date: date | None = None
    credential_id: str | None = Field(default=None, max_length=255)
    credential_url: str | None = None


class Snapshot(BaseModel):
    """The complete confirmed-career-data document for one version."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    contact: ContactItem | None = None
    summary: str | None = None
    links: list[LinkItem] = Field(default_factory=list)
    experiences: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[SkillItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)


class ProjectItemRead(ProjectItem):
    """A project as returned. `technologies` and `links` are always present on
    the way out."""

    technologies: list[str]
    links: list[LinkItem]


class SnapshotRead(BaseModel):
    """The snapshot as returned.

    Separate from `Snapshot` because the two directions genuinely differ: on the
    way in every collection is optional and defaults to empty, on the way out
    every collection is always present. Collapsing them would force generated
    clients to null-check five arrays that can never be null.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    # Contact and summary stay nullable on the way out: unlike the collections,
    # "absent" is a real state a profile can be in, not an empty list.
    contact: ContactItem | None = None
    summary: str | None = None
    # Reads `ProfileVersion.personal_links`, never `.links` — a version's raw
    # link relationship holds every link belonging to it, project-owned rows
    # included, and this field is specifically the contact block's own. The
    # alias is what keeps a project's Demo/GitHub link from resurfacing here
    # merely because a caller validated straight off the ORM row. See
    # `ProfileVersion.personal_links`.
    links: list[LinkItem] = Field(validation_alias="personal_links")
    experiences: list[ExperienceItem]
    education: list[EducationItem]
    skills: list[SkillItem]
    projects: list[ProjectItemRead]
    certifications: list[CertificationItem]
