from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

#: Whose link this is. A personal one belongs to the version's own contact
#: block — a LinkedIn, a personal GitHub, a portfolio site. A project one
#: belongs to exactly one `Project` row and nothing else: a Demo or a
#: repository link drawn beside that project's own entry, never the person's.
#:
#: The distinction exists because both shapes are read from the SAME source —
#: a resume's hyperlink annotations, wherever on the page they are drawn — and
#: nothing about a `ProfileLink` row on its own says which section it came
#: from. Before this column existed, a project's own Demo/GitHub link and the
#: candidate's personal one were the same kind of row, and every reader that
#: meant "the contact block's links" — the snapshot response, and every
#: tailoring module that renders or grounds a document's header — read
#: `ProfileVersion.links` directly and got both. See
#: `ProfileVersion.personal_links`.
OWNER_PERSONAL = "personal"
OWNER_PROJECT = "project"
OWNER_TYPES = frozenset({OWNER_PERSONAL, OWNER_PROJECT})


class ProfileLink(Base):
    """A URL the resume carried, as confirmed in one profile version.

    Its own table rather than columns named after the sites we happen to know:
    a `linkedin_url`/`github_url` pair cannot hold a second repository, a
    personal site or whatever the next platform is, and every resume that lists
    one project link per project needs exactly that.

    Order is preserved by id — a resume lists its links in an order that carries
    meaning, and re-sorting them alphabetically would put the portfolio before
    the LinkedIn profile for no reason a reader would recognise.

    `profile_version_id` is set on every row, personal and project-owned alike
    — it is what makes "every link this version holds, in one order" a single
    query rather than a union of two. `project_id` is the ownership itself:
    null for a personal link, and the one project a project-owned link belongs
    to. The CHECK constraint below is what keeps the two columns from
    disagreeing about which this row is.
    """

    __tablename__ = "profile_links"

    __table_args__ = (
        CheckConstraint("length(url) > 0", name="ck_profile_links_url_not_empty"),
        CheckConstraint(
            "owner_type IN ('personal', 'project')",
            name="ck_profile_links_owner_type",
        ),
        CheckConstraint(
            "(owner_type = 'personal' AND project_id IS NULL) OR "
            "(owner_type = 'project' AND project_id IS NOT NULL)",
            name="ck_profile_links_project_id_matches_owner_type",
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
            name="fk_profile_links_profile_version_id",
        ),
        nullable=False,
        index=True,
    )

    url: Mapped[str] = mapped_column(String(2048), nullable=False)

    # What the document showed the link as ("Github"). Nullable: a resume that
    # prints the bare URL has no separate label, and inventing one would be a
    # claim the document did not make.
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    owner_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=OWNER_PERSONAL
    )

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_profile_links_project_id",
        ),
        nullable=True,
        index=True,
    )

    profile_version: Mapped["ProfileVersion"] = relationship(  # noqa: F821
        back_populates="links"
    )

    project: Mapped["Project | None"] = relationship(  # noqa: F821
        back_populates="links"
    )

    def __repr__(self) -> str:
        return f"<ProfileLink id={self.id} owner_type={self.owner_type!r} url={self.url!r}>"
