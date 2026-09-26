from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """An EasyApply account: what every user-owned row points at.

    How a request finds its user depends on the identity mode
    (app/api/identity.py). Under real sign-in it is (issuer, subject) through
    `user_identities` (app/models/user_identity.py), and `email` is NULL.
    `email` is the DEVELOPMENT identity's key — the X-Dev-User-Email a
    developer or a test suite sends — and nothing else: it is never used to find
    or link a real sign-in, because an address is not proof of who someone is.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    email: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
