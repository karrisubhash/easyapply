"""An object held by the database storage backend (app/services/storage.py).

One row per storage key: the bytes an uploaded resume or a generated artifact
consists of. It exists for a deployment with no persistent disk and no object
store — STORAGE_BACKEND=database — where the container filesystem does not
survive a restart. Nothing references it by foreign key: like a file under the
filesystem backend's root, an object is found only by the key the processing
code already holds, and the rows that hold keys (resume_documents,
tailored_resume_artifacts) are unchanged.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StoredObject(Base):
    __tablename__ = "stored_objects"
    __table_args__ = (
        CheckConstraint("length(key) > 0", name="ck_stored_objects_key_non_empty"),
        CheckConstraint("size_bytes = octet_length(data)", name="ck_stored_objects_size_matches"),
    )

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
