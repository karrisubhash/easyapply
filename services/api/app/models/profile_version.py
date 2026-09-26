from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.profile_link import OWNER_PERSONAL

# A version is a draft while the user is still correcting AI-extracted data.
# Confirming it makes it authoritative and immutable.
STATUS_DRAFT = "draft"
STATUS_CONFIRMED = "confirmed"

# Extraction creates drafts, so the creator is frequently not a person.
CREATED_BY_USER = "user"
CREATED_BY_AI = "ai"
CREATED_BY_SYSTEM = "system"


class ProfileVersion(Base):
    """An immutable snapshot of a profile's confirmed career data.

    Every downstream artifact records the version it was generated from, which
    is what lets us answer "what did EasyApply know about this user when it
    produced this?" long after the profile has moved on.
    """

    __tablename__ = "profile_versions"

    __table_args__ = (
        # Version numbers are unique per profile. The index this creates has
        # profile_id as its leading column, so it also serves every lookup
        # filtered on profile_id alone — no separate FK index is needed.
        UniqueConstraint(
            "profile_id",
            "version_number",
            name="uq_profile_versions_profile_id_version_number",
        ),
        # At most one current version per profile, enforced by the database
        # rather than by application convention.
        Index(
            "uq_profile_versions_current",
            "profile_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_profile_versions_version_number_positive",
        ),
        CheckConstraint(
            f"status IN ('{STATUS_DRAFT}', '{STATUS_CONFIRMED}')",
            name="ck_profile_versions_status",
        ),
        CheckConstraint(
            f"created_by_type IN ('{CREATED_BY_USER}', '{CREATED_BY_AI}', '{CREATED_BY_SYSTEM}')",
            name="ck_profile_versions_created_by_type",
        ),
        # confirmed_at is set if and only if the version is confirmed.
        CheckConstraint(
            f"(status = '{STATUS_CONFIRMED}') = (confirmed_at IS NOT NULL)",
            name="ck_profile_versions_confirmed_at_matches_status",
        ),
        # A draft is work in progress; it can never be the current version.
        CheckConstraint(
            f"NOT is_current OR status = '{STATUS_CONFIRMED}'",
            name="ck_profile_versions_current_is_confirmed",
        ),
        # If a person created it, say which person.
        CheckConstraint(
            f"created_by_type <> '{CREATED_BY_USER}' OR created_by_user_id IS NOT NULL",
            name="ck_profile_versions_created_by_user_present",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE", name="fk_profile_versions_profile_id"),
        nullable=False,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=STATUS_DRAFT,
    )

    # Which confirmed version is the live one. Replaces a current_version_id
    # pointer on Profile, which would make the two tables reference each other
    # in a cycle and could not prevent a profile pointing at another profile's
    # version.
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    created_by_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_profile_versions_created_by_user_id"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --- contact details -----------------------------------------------------
    # Columns rather than a child table: unlike experiences or skills, a person
    # has exactly one name and one phone number, and a 1:1 table would buy a
    # join in exchange for nothing. They live on the version, not on Profile,
    # because they are read off the resume like everything else here — so "which
    # address did we hold when this application went out" stays answerable.
    # All nullable: a resume that gives a name and nothing else is still a
    # resume, and these are never inferred when the document does not say.

    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The candidate's own summary paragraph, as the resume wrote it. Never
    # composed by us from the other sections.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile: Mapped["Profile"] = relationship(back_populates="versions")  # noqa: F821

    # The snapshot. Ordering is derived from the data itself rather than stored,
    # so there is no display_order column to drift out of sync.
    experiences: Mapped[list["Experience"]] = relationship(  # noqa: F821
        back_populates="profile_version",
        cascade="all, delete-orphan",
        order_by="Experience.start_date.desc()",
    )
    education: Mapped[list["Education"]] = relationship(  # noqa: F821
        back_populates="profile_version",
        cascade="all, delete-orphan",
        order_by="Education.start_date.desc()",
    )
    skills: Mapped[list["Skill"]] = relationship(  # noqa: F821
        back_populates="profile_version",
        cascade="all, delete-orphan",
        order_by="Skill.name",
    )
    projects: Mapped[list["Project"]] = relationship(  # noqa: F821
        back_populates="profile_version",
        cascade="all, delete-orphan",
        order_by="Project.name",
    )
    certifications: Mapped[list["Certification"]] = relationship(  # noqa: F821
        back_populates="profile_version",
        cascade="all, delete-orphan",
        order_by="Certification.name",
    )
    links: Mapped[list["ProfileLink"]] = relationship(  # noqa: F821
        back_populates="profile_version",
        cascade="all, delete-orphan",
        order_by="ProfileLink.id",
    )

    @property
    def contact(self) -> dict[str, str | None] | None:
        """The contact columns as one object, or None when the resume gave none.

        The snapshot presents contact details as a nested document while the
        table stores them as columns; this is the seam. None rather than an
        object of all-nulls, so "no contact details" reads as absent downstream
        instead of as a present-but-empty record.
        """
        values = {
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "location": self.location,
        }
        return values if any(value is not None for value in values.values()) else None

    @property
    def personal_links(self) -> list["ProfileLink"]:
        """This version's OWN contact links — never a project's.

        `links` is every `ProfileLink` row belonging to this version, in one
        order, project-owned rows included (see that model's docstring for
        why the two ownerships share the table). This is the filtered read
        every caller that means "the contact block's links" should use —
        the snapshot response, and every tailoring module that renders or
        grounds a document's header — so that a project's Demo/GitHub link
        cannot resurface as a personal one just because a caller forgot to
        filter `links` itself.

        A Python filter over an already-loaded, already-ordered collection
        rather than a second query or a filtered relationship: `links` is
        `selectinload`ed everywhere a version is read, so this costs nothing
        extra, and a relationship whose OWN join condition depends on a
        column value is a documented SQLAlchemy foot-gun for anything that
        also needs to `.append()` to it — `replace_snapshot` does, for a
        fresh personal link.
        """
        return [link for link in self.links if link.owner_type == OWNER_PERSONAL]

    def __repr__(self) -> str:
        return (
            f"<ProfileVersion id={self.id} profile_id={self.profile_id} "
            f"v{self.version_number} status={self.status!r}>"
        )
