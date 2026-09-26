"""The benchmark's own vocabulary and shapes. Knows nothing about any matcher.

This module — and `groundtruth.py`, `metrics.py` and `failures.py` beside it —
must never import `app.services.matching`. That is not a style preference; it
is the property the whole benchmark rests on. If the code that loads the
expected answers could reach the code that produces the actual ones, the
expected answers would stop being independent evidence and the benchmark would
be measuring the engine against itself.

`evaluation/benchmark/adapter.py` is the single seam where a matcher is
touched, and `tests/test_benchmark_independence.py` asserts the boundary by
walking these modules' imports.

The vocabularies below are DELIBERATELY local copies rather than imports of
`app.models.match`'s constants. A human reviewer writes `"matched"` because
that is the word for what they decided, not because the engine happens to spell
it that way today. If a future algorithm renames a verdict, the translation
belongs in the adapter — the recorded human judgments must not move.

    Dataset
      ├── ProfileFixture[]        a realistic candidate. Every item carries a
      │                           `ref`, which is what ground truth cites —
      │                           stable across any renumbering of database ids.
      ├── JobFixture[]            a realistic posting: the description a human
      │                           read, plus the requirements they confirmed it
      │                           states. Each requirement has a stable id.
      └── Case[]                  one profile judged against one job, with one
                                  GroundTruth per requirement.

Requirements live on the JOB, not on the case, so a job used against three
profiles states its requirements once and the three sets of human verdicts stay
independent of each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

# --- verdicts -----------------------------------------------------------------

VERDICT_MATCHED = "matched"
VERDICT_MISSING = "missing"
VERDICT_UNCERTAIN = "uncertain"

VERDICTS = (VERDICT_MATCHED, VERDICT_MISSING, VERDICT_UNCERTAIN)

#: What a reviewer means by each word. Quoted in the dataset README and in the
#: reviewer instructions, and repeated here so the definition travels with the
#: code that enforces it.
VERDICT_DEFINITIONS = {
    VERDICT_MATCHED: (
        "The profile, read as a document, clearly satisfies this requirement. A "
        "reviewer can point at the specific item that does so and would not "
        "expect a colleague to disagree."
    ),
    VERDICT_MISSING: (
        "The requirement is definitely not met. Either the profile contains "
        "nothing that speaks to it, or it contains enough to settle the "
        "question in the negative — a duration that can be computed and falls "
        "clearly short, a degree level that is plainly not reached. A MISSING "
        "of the second kind cites the evidence that was weighed, because "
        "'we found nothing' and 'we found this and it is not enough' read very "
        "differently to the person who wrote the profile."
    ),
    VERDICT_UNCERTAIN: (
        "The profile contains something relevant that does not settle the "
        "question: evidence too weak to call a claim, a duration that cannot "
        "be computed from what is stated, a degree in a field whose relatedness "
        "is a judgment call, or a requirement the profile schema cannot answer "
        "at all."
    ),
}

# --- where evidence comes from -------------------------------------------------
#
# The kinds of profile item a reviewer may cite. Mirrors the shape of a profile
# version, because that is what a reviewer is reading — not because the match
# domain enumerates the same words.

EVIDENCE_SKILL = "skill"
EVIDENCE_EXPERIENCE = "experience"
EVIDENCE_PROJECT = "project"
EVIDENCE_EDUCATION = "education"
EVIDENCE_CERTIFICATION = "certification"
#: No profile item supports this verdict. Required for MISSING, and permitted
#: for an UNCERTAIN whose doubt is about the REQUIREMENT rather than about
#: anything the profile says (a work-authorization line, say).
EVIDENCE_NONE = "none"

EVIDENCE_SOURCES = (
    EVIDENCE_SKILL,
    EVIDENCE_EXPERIENCE,
    EVIDENCE_PROJECT,
    EVIDENCE_EDUCATION,
    EVIDENCE_CERTIFICATION,
    EVIDENCE_NONE,
)

# --- requirement categories ----------------------------------------------------

CATEGORY_SKILL = "skill"
CATEGORY_TECHNOLOGY = "technology"
CATEGORY_EXPERIENCE = "experience"
CATEGORY_EDUCATION = "education"
CATEGORY_CERTIFICATION = "certification"
CATEGORY_QUALIFICATION = "qualification"

CATEGORIES = (
    CATEGORY_SKILL,
    CATEGORY_TECHNOLOGY,
    CATEGORY_EXPERIENCE,
    CATEGORY_EDUCATION,
    CATEGORY_CERTIFICATION,
    CATEGORY_QUALIFICATION,
)

# --- what a requirement is TESTING ---------------------------------------------
#
# Authored by the reviewer as a property of the case, never as a prediction of
# failure. It says what makes this requirement interesting to judge; combined
# with the direction of a disagreement it is what lets `failures.py` name a
# failure category without consulting any matching rule.

ASPECTS = (
    "exact_skill",
    "alias",
    "near_synonym",
    "technology",
    #: The skill is demonstrated, specifically and in a paid role, but only in
    #: the prose of a description — never in a field whose purpose is to
    #: declare it.
    "prose_evidence",
    #: The skill is declared in a list and nothing anywhere else in the profile
    #: touches it.
    "uncorroborated_claim",
    #: Never stated, but so strongly implied by adjacent evidence that a
    #: reviewer would not call it absent.
    "implied_not_stated",
    "certification",
    "certification_variant",
    "education_level",
    "education_field",
    "related_field",
    "experience_duration",
    "experience_insufficient",
    "experience_ambiguous",
    "project_not_employment",
    "overlapping_employment",
    "seniority",
    "domain",
    "location",
    "free_text",
    "weak_evidence",
    "missing_evidence",
)


class DatasetError(ValueError):
    """A dataset that cannot be trusted to measure anything.

    Raised at load time rather than tolerated, because every quiet fallback
    here — a missing verdict treated as MISSING, an unknown aspect ignored —
    would silently shrink what the benchmark actually checks while the report
    still printed a number.
    """


# --- profile fixtures -----------------------------------------------------------


@dataclass(frozen=True)
class ProfileSkill:
    ref: str
    name: str
    years: float | None = None
    proficiency: str | None = None


@dataclass(frozen=True)
class ProfileExperience:
    ref: str
    company: str
    job_title: str
    start: date
    end: date | None = None
    description: str | None = None
    employment_type: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class ProfileEducation:
    ref: str
    institution: str
    degree: str | None = None
    field_of_study: str | None = None


@dataclass(frozen=True)
class ProfileCertification:
    ref: str
    name: str
    issuer: str | None = None
    expires: date | None = None


@dataclass(frozen=True)
class ProfileProject:
    ref: str
    name: str
    technologies: tuple[str, ...] = ()
    description: str | None = None
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class ProfileFixture:
    """A realistic candidate, synthesised rather than collected.

    Composed from patterns that occur in real profiles — a career changer, a
    contractor with overlapping engagements, someone whose strongest evidence
    sits in prose — with no real person's details. See the dataset README on
    why synthesis rather than anonymisation.
    """

    id: str
    label: str
    summary: str
    skills: tuple[ProfileSkill, ...] = ()
    experiences: tuple[ProfileExperience, ...] = ()
    education: tuple[ProfileEducation, ...] = ()
    certifications: tuple[ProfileCertification, ...] = ()
    projects: tuple[ProfileProject, ...] = ()

    def refs(self) -> set[str]:
        return {
            item.ref
            for group in (
                self.skills,
                self.experiences,
                self.education,
                self.certifications,
                self.projects,
            )
            for item in group
        }


# --- job fixtures ---------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """One thing a posting asks for, as a human confirmed the posting states it.

    `minimum_years`, `degree_level` and `field_of_study` are the structured
    fields a job structurer would fill. They are authored here rather than
    produced by running the structurer, so that a matching benchmark measures
    MATCHING: a structuring mistake would otherwise show up as a matching
    failure and be diagnosed in the wrong domain.
    """

    id: str
    category: str
    label: str
    is_required: bool
    aspect: str
    minimum_years: float | None = None
    degree_level: str | None = None
    field_of_study: str | None = None
    #: The line of the posting this was read from. Kept so a disputed
    #: requirement can be checked against the description without guessing.
    source_quote: str | None = None


@dataclass(frozen=True)
class JobFixture:
    id: str
    label: str
    job_title: str
    company_kind: str
    location: str | None
    description: str
    requirements: tuple[Requirement, ...]

    def requirement(self, requirement_id: str) -> Requirement | None:
        return next((item for item in self.requirements if item.id == requirement_id), None)


# --- ground truth -----------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedEvidence:
    """The profile item a reviewer would point at.

    `refs` is a set because more than one item can equally justify a verdict —
    two roles that both name the technology, say. The benchmark counts evidence
    as correct when the engine cites ANY of them, because citing one good
    reason is not worse than citing two.
    """

    source_type: str
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroundTruth:
    """One human judgment. The unit of truth this whole benchmark is built on.

    `rationale` is not documentation. It is the record of WHY a person decided
    this, which is what makes a disagreement with the engine reviewable later
    rather than a matter of whose memory is better — and what makes it possible
    to notice that the human was the one who got it wrong.
    """

    requirement_id: str
    verdict: str
    evidence: ExpectedEvidence
    rationale: str
    #: Overrides the requirement's own aspect for this pairing.
    #:
    #: A requirement's aspect is written on the JOB, but what a requirement
    #: actually tests depends on the profile it meets. "Kubernetes" tests the
    #: K8s alias against a profile that writes it that way, and tests
    #: uncorroborated claims against one that lists it and nothing else. The
    #: reviewer judging a specific pairing is the only one who knows which, so
    #: they may say so here.
    aspect: str | None = None
    #: Set when reviewers did not initially agree, with the disagreement
    #: recorded. A case nobody found hard is not the same as a case everyone
    #: found easy, and the metrics report the two separately.
    contested: bool = False
    contested_note: str | None = None


@dataclass(frozen=True)
class Case:
    """One profile judged against one job, on one day."""

    id: str
    profile_id: str
    job_id: str
    as_of: date
    note: str
    ground_truth: tuple[GroundTruth, ...]

    def truth_for(self, requirement_id: str) -> GroundTruth | None:
        return next(
            (item for item in self.ground_truth if item.requirement_id == requirement_id), None
        )


@dataclass(frozen=True)
class ReviewMetadata:
    """Who decided, when, and under what instructions."""

    dataset_version: str
    created: str
    reviewers: tuple[str, ...]
    methodology: str
    independence_statement: str


@dataclass(frozen=True)
class Dataset:
    version: str
    review: ReviewMetadata
    profiles: dict[str, ProfileFixture]
    jobs: dict[str, JobFixture]
    cases: tuple[Case, ...]

    @property
    def requirement_count(self) -> int:
        """Judged requirements — the number of human decisions in the dataset.

        Not the number of distinct requirements: a job used against three
        profiles was judged three times, and each judgment is a separate piece
        of evidence about the matcher.
        """
        return sum(len(case.ground_truth) for case in self.cases)


# --- validation --------------------------------------------------------------------
#
# Every rule here rejects a dataset that would otherwise still produce a
# number. A benchmark that scores 0.91 over a dataset missing a third of its
# verdicts is worse than one that refuses to run.


def validate(dataset: Dataset) -> None:
    problems: list[str] = []

    for case in dataset.cases:
        profile = dataset.profiles.get(case.profile_id)
        job = dataset.jobs.get(case.job_id)
        if profile is None:
            problems.append(f"{case.id}: unknown profile {case.profile_id!r}")
        if job is None:
            problems.append(f"{case.id}: unknown job {case.job_id!r}")
        if profile is None or job is None:
            continue

        judged = [truth.requirement_id for truth in case.ground_truth]
        if len(judged) != len(set(judged)):
            problems.append(f"{case.id}: a requirement is judged more than once")

        stated = {item.id for item in job.requirements}
        for missing in sorted(stated - set(judged)):
            problems.append(f"{case.id}: requirement {missing!r} has no ground-truth verdict")
        for unknown in sorted(set(judged) - stated):
            problems.append(f"{case.id}: verdict for {unknown!r}, which {job.id} does not state")

        for truth in case.ground_truth:
            problems.extend(_check_truth(case, profile, truth))

    for job in dataset.jobs.values():
        seen = [item.id for item in job.requirements]
        if len(seen) != len(set(seen)):
            problems.append(f"{job.id}: duplicate requirement id")
        for requirement in job.requirements:
            if requirement.category not in CATEGORIES:
                problems.append(f"{requirement.id}: unknown category {requirement.category!r}")
            if requirement.aspect not in ASPECTS:
                problems.append(f"{requirement.id}: unknown aspect {requirement.aspect!r}")

    if problems:
        raise DatasetError(
            f"{len(problems)} problem(s) in dataset {dataset.version}:\n  "
            + "\n  ".join(problems[:40])
        )


def _check_truth(case: Case, profile: ProfileFixture, truth: GroundTruth) -> list[str]:
    where = f"{case.id}/{truth.requirement_id}"
    problems: list[str] = []

    if truth.verdict not in VERDICTS:
        problems.append(f"{where}: unknown verdict {truth.verdict!r}")
    if truth.evidence.source_type not in EVIDENCE_SOURCES:
        problems.append(f"{where}: unknown evidence source {truth.evidence.source_type!r}")
    if not truth.rationale or len(truth.rationale.strip()) < 15:
        # A judgment without a reason cannot be reviewed, and cannot be
        # overturned when it turns out to be the wrong one.
        problems.append(f"{where}: rationale is missing or too short to review")

    # The invalid verdict/evidence combinations. Each one describes a dataset
    # that contradicts itself, and a self-contradictory expectation would score
    # the engine against nonsense.
    if truth.verdict == VERDICT_MISSING:
        # A MISSING may cite what was weighed and found short — see
        # VERDICT_DEFINITIONS. What it may not do is name a source type
        # without naming which item, which would be an unfalsifiable claim
        # about evidence nobody can look up.
        if truth.evidence.source_type == EVIDENCE_NONE and truth.evidence.refs:
            problems.append(
                f"{where}: MISSING with source 'none' claims no evidence exists, so it cannot cite any"
            )
        if truth.evidence.source_type != EVIDENCE_NONE and not truth.evidence.refs:
            problems.append(
                f"{where}: MISSING citing a source type must name which item was weighed"
            )
    if truth.verdict == VERDICT_MATCHED:
        if truth.evidence.source_type == EVIDENCE_NONE or not truth.evidence.refs:
            problems.append(
                f"{where}: MATCHED must name the profile item that satisfies the requirement"
            )
    if truth.verdict == VERDICT_UNCERTAIN and truth.evidence.source_type != EVIDENCE_NONE:
        if not truth.evidence.refs:
            problems.append(
                f"{where}: UNCERTAIN citing a source type must name which item it means"
            )

    known = profile.refs()
    for ref in truth.evidence.refs:
        if ref not in known:
            problems.append(f"{where}: cites {ref!r}, which is not in profile {profile.id}")

    if truth.aspect is not None and truth.aspect not in ASPECTS:
        problems.append(f"{where}: unknown aspect override {truth.aspect!r}")

    if truth.contested and not truth.contested_note:
        problems.append(f"{where}: marked contested without recording the disagreement")

    return problems


def as_json_safe(value: Any) -> Any:
    """Dates to ISO strings, tuples to lists — for the machine-readable report."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [as_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: as_json_safe(item) for key, item in value.items()}
    return value
