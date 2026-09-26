"""Match API representations.

`MatchRequest` is the only schema untrusted input reaches, and it carries one
optional field. Everything else about a match — whose it is, which requirements
row it pinned, which algorithm produced it — is asserted by the server from the
authenticated caller and the job's own history, never accepted from the client.
`extra="forbid"` makes that structural rather than a check to remember.

The response deliberately leads with counts and requirement-level results. The
score is present, named for what it measures, and documented as coverage rather
than as a prediction — §12's requirement, and the reason `overall_score` sits
below the breakdown in this file rather than at the top of it.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.match import (
    EVIDENCE_TIERS,
    EVIDENCE_STANDARDS,
    STANDARD_ANY_EVIDENCE,
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_UNCERTAIN,
)


class MatchRequest(BaseModel):
    """What a caller may ask for when creating a match."""

    model_config = ConfigDict(extra="forbid")

    #: Which immutable snapshot to judge. Omitted means the profile's current
    #: confirmed version — well-defined and immutable, never a draft. A version
    #: belonging to another user is a 404, resolved server-side.
    profile_version_id: int | None = Field(
        default=None,
        ge=1,
        description=(
            "The confirmed profile version to match. Defaults to the caller's current "
            "confirmed version. Drafts are refused."
        ),
    )


class MatchEvidenceRead(BaseModel):
    """A profile-version row that justifies — or fails to justify — a verdict.

    `qualifies` is the field that makes an UNCERTAIN explainable. Evidence
    that fell short is returned rather than dropped, with `note` saying why,
    so a user reading a gap sees what the matcher found and what it wanted
    instead of an empty list.
    """

    model_config = ConfigDict(from_attributes=True)

    source_type: str
    source_id: int | None
    label: str
    detail: str | None
    #: How strong this item is. Null for education and certification evidence,
    #: which is not graded.
    tier: str | None = None
    #: Whether this item met what the requirement asked for.
    qualifies: bool = True
    #: Why it qualifies, or why it does not, in the server's own words.
    note: str | None = None
    #: The clause the technology was found in, when the evidence came from
    #: prose — so a reader can judge the claim rather than trust the tier.
    quote: str | None = None


class MatchRequirementResultRead(BaseModel):
    """One job requirement, judged."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    requirement_index: int
    requirement_label: str
    requirement_key: str | None
    is_required: bool
    status: str
    confidence: float
    explanation: str
    #: What the posting demanded of the evidence, read from its own wording.
    #: "Kubernetes" and "Kubernetes in production" set different bars, and
    #: this is what lets a user see which one applied.
    evidence_standard: str = STANDARD_ANY_EVIDENCE
    matched_value: str | None
    evidence: list[MatchEvidenceRead] = Field(default_factory=list)


class MatchSummary(BaseModel):
    """A match as it appears in a listing: the counts, not the verdicts."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    profile_version_id: int
    #: Which version this judged, as the user names it. The id above is the
    #: database's name for the same row; a person reading "version 3" needs this
    #: one, and the match screen was printing the id under that label. The same
    #: field, for the same reason, as `TailoringRunSummary.profile_version_number`.
    profile_version_number: int | None = None
    job_requirements_id: int
    algorithm_version: str
    as_of_date: date
    created_at: datetime

    required_total: int
    required_matched: int
    required_missing: int
    required_uncertain: int

    preferred_total: int
    preferred_matched: int
    preferred_missing: int
    preferred_uncertain: int

    required_coverage: float
    preferred_coverage: float
    #: Coverage of structured job requirements by available profile evidence.
    #: Not a probability of being hired and not a ranking — see
    #: app/services/matching/scoring.py.
    overall_score: float


class MatchDetail(MatchSummary):
    """A match in full: every requirement verdict, with its evidence."""

    requirement_results: list[MatchRequirementResultRead] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_unmet_required(self) -> bool:
        """Whether any required requirement is unmet or unsettled.

        Derived on the server so a client cannot invent its own rule, and
        deliberately phrased as "unmet" rather than as a suitability verdict:
        §13 forbids this engine from concluding a candidate is unsuitable. It
        says what the counts say, nothing more.
        """
        return self.required_missing > 0 or self.required_uncertain > 0


#: Re-exported so clients and tests share one spelling of the vocabulary.
MATCH_STATUS_VALUES = (STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN)
MATCH_EVIDENCE_TIERS = EVIDENCE_TIERS
MATCH_EVIDENCE_STANDARDS = EVIDENCE_STANDARDS
