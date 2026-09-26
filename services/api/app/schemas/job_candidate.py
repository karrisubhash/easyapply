"""What a structurer is allowed to say about a job posting.

The mirror of `app/schemas/resume_candidate.py`, and for the same reason: this
is not the canonical `Job` row, it is a claim about what its description
contains, made by something that can be wrong — a regex today, a model later.
Every item carries a citation, and nothing here is written to the database
until it has validated against this schema. See
`app/services/job/structuring.py` for where that validation happens and
`app/services/job/pipeline.py` for why an unvalidated dict from a structurer
never reaches `JobRequirements.payload` directly.

The split into nine categories is deliberate and comes straight from the
domain, not from convenience: required and preferred are different claims about
the same posting, and collapsing "5+ years required" and "5+ years preferred"
into one list of experience strings would throw away the distinction any future
matching system exists to preserve. Only skills and experience are split into
separate required/preferred lists at the top level — that split is exactly what
the domain spec names explicitly. Education, certifications, technologies and
qualifications each stay as one list with an `is_required` flag per item,
because a posting saying "Bachelor's required, Master's preferred" is one
education section making two claims, not two sections.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Mirrors the two axes on the Job model. Kept here as a comment, not
#: re-exported: the structurer emits free-text category labels for
#: technologies (e.g. "language", "database", "cloud"), which is intentionally
#: looser than the Job model's own closed vocabulary for employment/workplace
#: type — a technology's category is descriptive, not an identity field.
DEGREE_LEVELS = Literal["associate", "bachelor", "master", "doctorate", "other"]


class JobProvenance(BaseModel):
    """Where a requirement came from, and how sure the structurer was.

    `source_section` is the mirror of resume's `page`: a job description has no
    pages, but it usually has headings ("Requirements", "Nice to have",
    "Responsibilities"), and naming the one a claim came from is what makes the
    claim checkable by a person reading the original posting.
    """

    model_config = ConfigDict(extra="forbid")

    source_section: str | None = Field(
        default=None,
        max_length=200,
        description="The heading this claim was found under, if one was identified.",
    )
    quote: str = Field(
        max_length=400,
        description="Text copied verbatim from the description. Never paraphrased.",
    )
    confidence: float = Field(ge=0.0, le=1.0)

    # Set by the server, never by the structurer: whether `quote` was actually
    # found in the job's description_text. False means the structurer invented
    # its evidence — meaningless for the deterministic structurer, which can
    # only quote text it already read, but required once a model can structure
    # jobs too, exactly as resume verifies its own AI provenance.
    verified: bool | None = None


class JobRequirementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provenance: list[JobProvenance] = Field(default_factory=list)


class JobSkillItem(JobRequirementItem):
    name: str = Field(max_length=128)
    category: str | None = Field(default=None, max_length=64)


class JobExperienceItem(JobRequirementItem):
    description: str = Field(max_length=500)
    minimum_years: float | None = Field(default=None, ge=0)
    maximum_years: float | None = Field(default=None, ge=0)


class JobEducationItem(JobRequirementItem):
    description: str = Field(max_length=500)
    degree_level: str | None = Field(default=None, max_length=32)
    field_of_study: str | None = Field(default=None, max_length=255)
    is_required: bool = True


class JobCertificationItem(JobRequirementItem):
    name: str = Field(max_length=255)
    is_required: bool = True


class JobResponsibilityItem(JobRequirementItem):
    """Responsibilities describe the role, not a bar to clear — there is no
    required/preferred axis for what a job asks someone to do once hired."""

    description: str = Field(max_length=500)


class JobTechnologyItem(JobRequirementItem):
    name: str = Field(max_length=128)
    category: str | None = Field(default=None, max_length=64)
    is_required: bool = True


class JobQualificationItem(JobRequirementItem):
    """The catch-all: claims that are real requirements but do not fit skills,
    experience, education, certifications or technologies — work authorization,
    travel expectations, on-call rotations, security clearance, and where the
    job is ("Based in Berlin", "Remote within Germany").

    Location statements live here deliberately rather than in a list of their
    own. `engine.evaluate` iterates a fixed set of JobCandidate lists, so a new
    `locations` list it did not read would make those requirements vanish from
    a match result entirely — shrinking the denominator, which is the one way a
    coverage score can mislead without any individual verdict being wrong. A
    structured location item belongs with the engine branch that reads it, not
    ahead of it.
    """

    description: str = Field(max_length=500)
    is_required: bool = True


class JobCandidate(BaseModel):
    """The structured reading of one job's description."""

    model_config = ConfigDict(extra="forbid")

    required_skills: list[JobSkillItem] = Field(default_factory=list)
    preferred_skills: list[JobSkillItem] = Field(default_factory=list)
    required_experience: list[JobExperienceItem] = Field(default_factory=list)
    preferred_experience: list[JobExperienceItem] = Field(default_factory=list)
    education: list[JobEducationItem] = Field(default_factory=list)
    certifications: list[JobCertificationItem] = Field(default_factory=list)
    responsibilities: list[JobResponsibilityItem] = Field(default_factory=list)
    technologies: list[JobTechnologyItem] = Field(default_factory=list)
    qualifications: list[JobQualificationItem] = Field(default_factory=list)


#: Bumped whenever a field is added, removed or given different meaning.
#: Stored against every structured result so a payload read back later can say
#: which shape it was validated against, the same discipline as
#: `resume_candidate.CANDIDATE_SCHEMA_VERSION`.
JOB_CANDIDATE_SCHEMA_VERSION = "1"
