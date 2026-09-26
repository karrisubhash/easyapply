from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ResumeDocument(Base):
    """An uploaded resume file. Immutable once written.

    The row records what was uploaded; the bytes live in object storage under
    `storage_key`. Nothing here is ever updated — a re-upload of the same
    content resolves to this same row, and a changed resume is a different
    document. Processing state deliberately does NOT live here: an upload
    happens once, processing can happen many times, and putting a status column
    on the artifact would throw away the history of the attempts.

    Owned by a user rather than a profile: a resume can be uploaded before a
    profile exists, and it should outlive any particular profile version.
    """

    __tablename__ = "resume_documents"

    __table_args__ = (
        # Identical bytes from the same user are the same document. Scoped per
        # user rather than globally: a global hash index would let one user's
        # upload fail because a stranger uploaded the same file, which is an
        # existence oracle dressed up as a conflict.
        UniqueConstraint(
            "user_id",
            "content_sha256",
            name="uq_resume_documents_user_id_content_sha256",
        ),
        CheckConstraint("byte_size > 0", name="ck_resume_documents_byte_size_positive"),
        CheckConstraint(
            "char_length(content_sha256) = 64",
            name="ck_resume_documents_content_sha256_length",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_resume_documents_user_id"),
        nullable=False,
    )

    # Display only. It is attacker-controlled and is never used to build a path;
    # the storage key is derived from the content hash instead.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # 128, not 64: the DOCX media type alone is 71 characters.
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)

    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # An opaque handle. The API never returns it, and no caller may interpret it.
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    runs: Mapped[list["ResumeProcessingRun"]] = relationship(  # noqa: F821
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ResumeProcessingRun.attempt",
    )

    @property
    def latest_run(self) -> "ResumeProcessingRun | None":  # noqa: F821
        return self.runs[-1] if self.runs else None

    def __repr__(self) -> str:
        return f"<ResumeDocument id={self.id} filename={self.original_filename!r}>"
