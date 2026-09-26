from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Profile(Base):
    """A user's career profile.

    The profile itself holds no career data. It is the stable anchor that
    versions hang off, so that account concerns and career data can evolve
    independently.
    """

    __tablename__ = "profiles"

    __table_args__ = (
        # A user has at most one profile. UNIQUE on the child's foreign key is
        # what makes this 1:1 rather than 1:N — SQL has no other way to say it.
        # It also indexes user_id, so no separate index is needed.
        UniqueConstraint("user_id", name="uq_profiles_user_id"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    # RESTRICT, not CASCADE: erasing a user is a deliberate procedure, not a
    # side effect of a stray DELETE.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_profiles_user_id"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Deliberately one-directional. The profile domain depends on identity;
    # identity knows nothing about profiles.
    user: Mapped["User"] = relationship()  # noqa: F821

    versions: Mapped[list["ProfileVersion"]] = relationship(  # noqa: F821
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="ProfileVersion.version_number",
    )

    def __repr__(self) -> str:
        return f"<Profile id={self.id} user_id={self.user_id}>"
