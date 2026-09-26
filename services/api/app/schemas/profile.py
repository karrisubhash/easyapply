"""Profile and version representations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.snapshot import Snapshot, SnapshotRead

# Mirrors of the database's value sets, exposed so generated clients get a
# union instead of a bare string. As with the snapshot literals, the values are
# mirrored and the predicates are not; a test pins them to the model constants.
VersionStatus = Literal["draft", "confirmed"]
CreatedByType = Literal["user", "ai", "system"]


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class ProfileVersionSummary(BaseModel):
    """A version without its snapshot, for listings."""

    model_config = ConfigDict(from_attributes=True)

    #: The database id, which is what `MatchRequest.profile_version_id` and
    #: `TailoringRequest.profile_version_id` take.
    #:
    #: Exposed because those two endpoints already require it and a client had
    #: no way to obtain it — a version picker could offer "Version 3" and then
    #: have nothing to send. Safe to expose for the same reason it is safe to
    #: accept: it is a global key that proves nothing, and both services resolve
    #: it through a join to `profiles.user_id`, so another user's id is a 404
    #: rather than a read.
    #:
    #: `version_number` stays the one the URL space uses and the one a person
    #: reads. The two are different numbers and must not be substituted for each
    #: other.
    id: int

    version_number: int
    status: VersionStatus
    is_current: bool
    created_by_type: CreatedByType
    created_at: datetime
    confirmed_at: datetime | None


class ProfileVersionRead(ProfileVersionSummary):
    snapshot: SnapshotRead

    @model_validator(mode="before")
    @classmethod
    def _assemble(cls, data):
        """Gather the version's contact columns and child rows into one document.

        Done here rather than as a property on ProfileVersion: the domain model
        does not need to know that an HTTP representation exists.
        """
        if isinstance(data, dict):
            return data
        return {
            "id": data.id,
            "version_number": data.version_number,
            "status": data.status,
            "is_current": data.is_current,
            "created_by_type": data.created_by_type,
            "created_at": data.created_at,
            "confirmed_at": data.confirmed_at,
            "snapshot": SnapshotRead.model_validate(data),
        }


class ConfirmVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Confirming without promoting is legitimate: a version can be frozen for
    # the record without becoming the one the resume generator reads.
    make_current: bool = True
