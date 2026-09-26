"""What the AI is allowed to say about a resume.

This is NOT the profile snapshot, and the difference is the whole point. The
snapshot is what a user has confirmed about themselves; this is what a model
believes it read. So every field here is optional, every date keeps the words it
came from, and every claim carries a citation.

Two rules are enforced by the shape of these models rather than by prompting:

1. A date keeps its precision. "May 2024" becomes 2024-05-01 with precision
   "month" and `raw` "May 2024" — never a confident 1 May. This mirrors the
   precision columns the profile domain already has.
2. A claim cites a page and quotes the text it came from. The quote is checked
   against the extracted page text after the model returns, so a fabricated
   quote is detectable rather than merely regrettable.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DatePrecision = Literal["year", "month", "day"]
EmploymentType = Literal[
    "full_time", "part_time", "contract", "internship", "freelance", "temporary"
]
Proficiency = Literal["beginner", "intermediate", "advanced", "expert"]


class Provenance(BaseModel):
    """Where a claim came from, and how sure the model was."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1, description="1-based page of the extracted document")
    quote: str = Field(
        max_length=400,
        description="Text copied verbatim from that page. Never paraphrased.",
    )
    confidence: float = Field(ge=0.0, le=1.0)

    # Set by the server, never by the model: whether `quote` was actually found
    # on the page it cites. A false here means the model invented its evidence.
    verified: bool | None = None


class CandidateDate(BaseModel):
    """A date as the resume expressed it, plus a normalization we can justify."""

    model_config = ConfigDict(extra="forbid")

    raw: str = Field(max_length=100, description="Exactly as written, e.g. 'May 2024'")
    value: date | None = Field(
        default=None,
        description="Normalized date, or null when the text cannot be normalized",
    )
    precision: DatePrecision | None = Field(
        default=None,
        description="How precisely the resume stated it. 'May 2024' is 'month'.",
    )


class CandidateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provenance: list[Provenance] = Field(default_factory=list)


class CandidateLink(BaseModel):
    """A URL the resume carries, and the words it was written as.

    The label is what the document showed ("Github"); the url is where it
    pointed. Both are kept because a resume that renders every link as the word
    "GitHub" gives the reader nothing to tell two repositories apart.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2048)
    label: str | None = Field(default=None, max_length=100)


class CandidateContact(CandidateItem):
    """Who the resume says this is and how to reach them.

    Separate from the career sections because it is the one part of a resume
    that is not an event in a history — it has no dates and cannot repeat.
    """

    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=255)
    links: list[CandidateLink] = Field(default_factory=list)


class CandidateSummary(CandidateItem):
    """The resume's own summary or objective paragraph, as written.

    Never composed by us from the rest of the document: a summary the candidate
    did not write is a claim they did not make.
    """

    text: str


class CandidateExperience(CandidateItem):
    company: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, max_length=255)
    start_date: CandidateDate | None = None
    end_date: CandidateDate | None = None
    description: str | None = None


class CandidateEducation(CandidateItem):
    institution: str | None = Field(default=None, max_length=255)
    degree: str | None = Field(default=None, max_length=255)
    field_of_study: str | None = Field(default=None, max_length=255)
    start_date: CandidateDate | None = None
    end_date: CandidateDate | None = None
    # Absent unless the resume states one. A grade is the classic hallucination.
    grade: str | None = Field(default=None, max_length=64)
    description: str | None = None


class CandidateSkill(CandidateItem):
    name: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    proficiency: Proficiency | None = None
    years_of_experience: float | None = Field(default=None, ge=0)


class CandidateProject(CandidateItem):
    name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    description: str | None = None
    start_date: CandidateDate | None = None
    end_date: CandidateDate | None = None
    project_url: str | None = None
    technologies: list[str] = Field(default_factory=list)
    #: This project's own links — a Demo, a GitHub repository — as opposed to
    #: `CandidateContact.links`, which is the candidate's personal ones. Both
    #: are `CandidateLink`; only which list a reading puts one in says whose
    #: it is.
    links: list[CandidateLink] = Field(default_factory=list)


class CandidateCertification(CandidateItem):
    name: str | None = Field(default=None, max_length=255)
    issuing_organization: str | None = Field(default=None, max_length=255)
    issue_date: CandidateDate | None = None
    expiration_date: CandidateDate | None = None
    credential_id: str | None = Field(default=None, max_length=255)
    credential_url: str | None = None


class ResumeCandidate(BaseModel):
    """The structured reading of one resume."""

    model_config = ConfigDict(extra="forbid")

    contact: CandidateContact | None = None
    summary: CandidateSummary | None = None
    experiences: list[CandidateExperience] = Field(default_factory=list)
    education: list[CandidateEducation] = Field(default_factory=list)
    skills: list[CandidateSkill] = Field(default_factory=list)
    projects: list[CandidateProject] = Field(default_factory=list)
    certifications: list[CandidateCertification] = Field(default_factory=list)

    def items(self) -> list[CandidateItem]:
        """Every citable item in the document, whatever section it sits in.

        Anything that walks provenance needs this list to be complete, and a
        walker that enumerates the sections by hand silently stops covering a
        section the day one is added — which is exactly how contact details and
        the summary went unchecked before they existed here.
        """
        singles = [item for item in (self.contact, self.summary) if item is not None]
        return [
            *singles,
            *self.experiences,
            *self.education,
            *self.skills,
            *self.projects,
            *self.certifications,
        ]


class UnmappedItem(BaseModel):
    """Something the resume contained that the profile snapshot cannot hold.

    Recorded rather than silently dropped. An experience with no date is the
    common case: `experiences.start_date` is NOT NULL in the profile schema, and
    inventing a date to satisfy it would be exactly the fabrication this design
    exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    section: str
    reason: str
    summary: str


#: The version of the candidate document these models define.
#:
#: Stored against every structured result, because a reading validated against
#: an older shape is not the same claim as one validated against this shape —
#: and without the version, a payload read back years later cannot say which it
#: was. Bumped whenever a field is added, removed or given different meaning.
#:
#: 2: added `contact`, `summary` and contact links. Before this, a resume's name,
#:    email, phone, location, summary paragraph and every URL on it had nowhere
#:    to go, so they were dropped between the page text and the profile.
#:
#: 3: added `CandidateProject.links`. Before this, EVERY link on the document —
#:    a project's own Demo/GitHub links included — was read onto `contact.links`
#:    the moment the document carried more than one hyperlink, because nothing
#:    distinguished a header link from one drawn beside a project three
#:    sections down the same page.
CANDIDATE_SCHEMA_VERSION = "3"
