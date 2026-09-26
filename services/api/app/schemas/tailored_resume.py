"""What a generator is allowed to say in a tailored resume.

The mirror of `resume_candidate.py` and `job_candidate.py`, and for the same
reason: this is not a fact about the user, it is a CLAIM made by something that
can be wrong. The difference is the direction. A resume candidate is a reading
of a document the user supplied; a tailored resume is prose written on the
user's behalf and addressed to an employer. Being wrong here is worse, because
the user may not notice before they send it.

Two rules are enforced by the shape of these models rather than by prompting,
and both exist so `app/services/tailoring/grounding.py` can check the output
against the snapshot without trusting the generator at all:

1. **Every entry names the snapshot row it stands on.** `source_id` is the
   primary key of an Experience, Education, Project or Certification in the
   profile version being tailored. A generator cannot emit a job the user never
   held, because there is no id for one — and an id belonging to a different
   version is caught by the validator.

2. **Every sentence quotes what it was derived from.** `source_quote` is text
   copied verbatim out of the snapshot. It is the tailored-resume equivalent of
   `Provenance.quote`, and it is what lets the review screen show

       ORIGINAL FACT  ->  TAILORED WORDING

   side by side. A generator that paraphrases without quoting produces a bullet
   the validator cannot ground, which is a finding rather than a silent pass.

What is deliberately NOT here: any DERIVED claim the snapshot does not already
contain. There is no `years_of_experience` on an experience entry and no
`seniority`. A generator asked for a resume will happily invent "8+ years of
experience leading teams" from a list of jobs, and the only reliable defence
against a field being filled in is for the field not to exist.

`TailoredContact` is the deliberate exception, and the line it sits on the right
side of is worth stating, because an earlier version of this module drew it in
the wrong place and shipped a resume with no name on it.

The danger is not "a field about the person". It is **a field nothing can check**.
`years_of_experience` is dangerous because no column holds it: whatever a
generator writes there, the validator has nothing to compare it against. But
`full_name`, `email`, `phone` and `location` are columns on the profile version,
read off the user's own resume, and `grounding._check_contact` compares them
exactly — the same check, of the same strictness, that catches a promoted job
title. A generator cannot invent a phone number here any more than it can invent
an employer.

Leaving them out did not prevent a fabrication. It produced a DOCX with no name,
no address and no way for an employer to reply — a document that was flawlessly
grounded and could not be sent to anyone.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DatePrecision = Literal["year", "month", "day"]

#: Bumped whenever a field is added, removed or given different meaning. Stored
#: against every generated document, the same discipline
#: `CANDIDATE_SCHEMA_VERSION` and `JOB_CANDIDATE_SCHEMA_VERSION` follow.
TAILORED_RESUME_SCHEMA_VERSION = "2"

#: Bumped when the deterministic generator's selection or wording rules change
#: in a way that could change its output. The AI generator carries the prompt
#: version instead. Recorded on the run either way.
PROMPT_VERSION_DETERMINISTIC = "tailoring_v2"


class TailoredLine(BaseModel):
    """One generated sentence, and the snapshot text behind it.

    `text` is what appears in the document. `source_quote` is what the snapshot
    actually said. They are usually close — the deterministic generator mostly
    selects and re-punctuates — and the point of keeping both is that a reader
    can see the distance between them rather than being asked to trust it.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=600)

    #: Verbatim from the snapshot row named by the enclosing entry's
    #: `source_id`. Never paraphrased, never assembled from two places.
    source_quote: str = Field(max_length=600)

    #: Which column it was copied out of: "description", "job_title", "name".
    #: Recorded so a finding can say where to look, not just that it looked.
    source_field: str = Field(max_length=32)


class TailoredLink(BaseModel):
    """One link in the header. Both halves are transcribed from a link row."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2048)
    label: str | None = Field(default=None, max_length=100)


class TailoredContact(BaseModel):
    """Who this resume is from, and how to reply to it.

    Every field is a column on the profile version, read off the user's own
    resume and confirmed by them. None of it is composed, inferred or formatted:
    `grounding._check_contact` compares each one to the stored column exactly,
    so a "tidied" phone number or a name in a different case is an error rather
    than a nicety.

    Nullable throughout, because the profile's own columns are: a confirmed
    version whose resume gave no phone number has no phone number, and printing
    a blank line is better than any of the ways a generator might fill one in.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=255)

    #: A handful of the version's links. Each must BE one of them — the
    #: validator checks the url against the stored set, so a plausible-looking
    #: profile URL that the user never gave is an error.
    links: list[TailoredLink] = Field(default_factory=list, max_length=6)


class TailoredEntry(BaseModel):
    """Anything that stands on exactly one snapshot row."""

    model_config = ConfigDict(extra="forbid")

    #: The id of the row in the profile version being tailored. The validator
    #: resolves it; an id from another version — or from nowhere — is an error,
    #: not a warning, because it means the document is describing someone else.
    source_id: int = Field(ge=1)


class TailoredExperience(TailoredEntry):
    """One job, as it should appear on this resume.

    The identity fields are copied, not generated: a company name or a job title
    that has been "improved" is a lie with a straight face, and `grounding.py`
    checks each of these against the snapshot row exactly.
    """

    company: str = Field(max_length=255)
    job_title: str = Field(max_length=255)
    location: str | None = Field(default=None, max_length=255)
    start_date: date
    start_date_precision: DatePrecision | None = None
    end_date: date | None = None
    end_date_precision: DatePrecision | None = None

    bullets: list[TailoredLine] = Field(default_factory=list)


class TailoredProject(TailoredEntry):
    name: str = Field(max_length=255)
    role: str | None = Field(default=None, max_length=255)
    #: Copied from the project's own technology rows. The validator checks set
    #: membership: a technology the project does not list cannot appear here.
    technologies: list[str] = Field(default_factory=list)
    bullets: list[TailoredLine] = Field(default_factory=list)


class TailoredEducation(TailoredEntry):
    institution: str = Field(max_length=255)
    degree: str | None = Field(default=None, max_length=255)
    field_of_study: str | None = Field(default=None, max_length=255)
    start_date: date | None = None
    #: Carried for the same reason `TailoredExperience` carries it, and it was
    #: missing here: the domain stores "2015" as 2015-01-01 with precision
    #: "year", so a renderer that cannot see the precision prints "Jan 2015" and
    #: invents a month the resume never gave. That is a false fact introduced
    #: after grounding has finished looking, which is the worst place to put one.
    start_date_precision: DatePrecision | None = None
    end_date: date | None = None
    end_date_precision: DatePrecision | None = None


class TailoredCertification(TailoredEntry):
    name: str = Field(max_length=255)
    issuing_organization: str | None = Field(default=None, max_length=255)
    #: No precision field, because `Certification` has no precision column to
    #: carry one. The renderer therefore prints the YEAR only — the most it can
    #: state without inventing. Adding the column upstream would let a
    #: certification dated to a month say so; until then, under-stating is the
    #: side of the line to be on.
    issue_date: date | None = None


class TailoredSkillGroup(BaseModel):
    """Skills, grouped for reading rather than re-stated.

    Every name must appear in the snapshot's skills or in a project's
    technologies. Grouping and ordering are the generator's contribution; the
    set of skills is not.
    """

    model_config = ConfigDict(extra="forbid")

    category: str = Field(max_length=64)
    skills: list[str] = Field(default_factory=list)


class TailoredResumeDocument(BaseModel):
    """One generated resume, aimed at one job.

    `targeted_requirements` is the generator saying which of the posting's
    requirements it tried to answer. It is not a claim about the candidate and
    is not grounded against the snapshot — it is grounded against the JOB, and
    the review screen shows it so the user can see what the document was
    optimising for. An empty list is honest when the generator had nothing to
    aim at.
    """

    model_config = ConfigDict(extra="forbid")

    #: Who the resume is from. Optional only because a confirmed version may
    #: genuinely hold no contact columns; a document without it renders without
    #: a header, which is a visible hole rather than a silent one.
    contact: TailoredContact | None = None

    #: A single line under the name — usually the current or most recent title.
    #: Grounded like everything else: it must come from a snapshot row.
    headline: TailoredLine | None = None

    #: The one place a generator writes connective prose. Kept as a list of
    #: lines rather than a paragraph so each sentence is separately groundable;
    #: a paragraph is one blob in which a single invented clause hides.
    summary: list[TailoredLine] = Field(default_factory=list)

    skills: list[TailoredSkillGroup] = Field(default_factory=list)
    experience: list[TailoredExperience] = Field(default_factory=list)
    projects: list[TailoredProject] = Field(default_factory=list)
    education: list[TailoredEducation] = Field(default_factory=list)
    certifications: list[TailoredCertification] = Field(default_factory=list)

    #: Requirement labels from the job this document was written against.
    targeted_requirements: list[str] = Field(default_factory=list, max_length=50)
