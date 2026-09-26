"""The one place the benchmark touches a matcher.

Every other module under `evaluation/benchmark/` is algorithm-agnostic: the
dataset, the ground truth, the metrics and the failure categories describe
matching as a problem, not matching_v1 as an implementation. This file is the
seam. When matching_v2 arrives, or a semantic or hybrid approach, a new adapter
implementing `MatchingAlgorithm` is the entire change — the dataset does not
move and the recorded human judgments do not move, which is the only way the
comparison between algorithms means anything.

Two translations happen here, and both are deliberately one-directional:

  * **Fixture to engine input.** Benchmark fixtures carry stable string `ref`s;
    the engine wants ORM objects with integer ids. The adapter assigns the ids
    and keeps the map, so nothing downstream ever has to know that an id was
    involved.

  * **Engine output to observation.** The engine reports verdicts per
    (category, requirement_index); the benchmark speaks in requirement ids. The
    adapter knows the order it built the candidate lists in, so the mapping is
    exact rather than a label match — two requirements with the same wording in
    one posting stay distinct.

`rule_label` reads the engine's OUTPUT to say which kind of rule produced a
verdict. It is a reading, not a re-derivation: it looks only at the category,
the verdict and the kind of evidence cited, all of which are in the result. It
never re-runs a rule or consults one, because a diagnosis that re-implemented
the thing it diagnoses would agree with it by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from evaluation.benchmark.schema import (
    JobFixture,
    ProfileFixture,
    Requirement,
)

# --- what the benchmark needs from any matcher -------------------------------


@dataclass(frozen=True)
class Verdict:
    """One requirement, as some matcher judged it.

    The algorithm-neutral shape. Anything that can produce this list can be
    benchmarked, whatever it does internally.
    """

    requirement_id: str
    status: str
    is_required: bool
    explanation: str
    #: Dataset refs, already resolved — never database ids.
    refs: tuple[str, ...]
    rule_label: str


@dataclass(frozen=True)
class AlgorithmResult:
    algorithm_version: str
    verdicts: tuple[Verdict, ...]
    #: Whether the algorithm's own summary agrees with its own verdicts. The
    #: adapter checks this because only the adapter knows what summary the
    #: algorithm publishes and how it claims to compute it.
    self_consistent: bool
    #: Kept for the report; the benchmark never treats it as a quality signal.
    summary: dict[str, float | int]

    def by_requirement(self) -> dict[str, Verdict]:
        return {verdict.requirement_id: verdict for verdict in self.verdicts}


class MatchingAlgorithm(Protocol):
    """What the benchmark requires of anything it scores."""

    @property
    def version(self) -> str: ...

    def run(
        self, *, profile: ProfileFixture, job: JobFixture, as_of: date
    ) -> AlgorithmResult: ...


# --- this repository's deterministic engine ------------------------------------


def _rule_label(category: str, status: str, sources: set[str]) -> str:
    """A name for the kind of rule that produced a verdict, read off the result."""
    if status == "missing":
        return f"{category}:no-evidence"
    if not sources:
        return f"{category}:{status}:unsupported"
    if sources & {"skill", "project_technology"}:
        return f"{category}:structured-evidence"
    if sources == {"experience"}:
        return f"{category}:employment-evidence"
    if sources == {"project"}:
        return f"{category}:project-evidence"
    return f"{category}:{'+'.join(sorted(sources))}"


class DeterministicEngineAdapter:
    """Runs this repository's deterministic engine over a benchmark case.

    Named for the implementation rather than for a version of it: the engine's
    own `ALGORITHM_VERSION` is what gets reported and stored, so a rules bump
    from matching_v1 to matching_v2 changes the measurement without needing a
    second adapter or a second name.
    """

    def __init__(self) -> None:
        # Imported here rather than at module scope so that importing the
        # benchmark package does not pull the engine in. `groundtruth.py` and
        # `metrics.py` can then be imported by a test that asserts the
        # matching modules are absent from the process.
        from app.services.matching.engine import ALGORITHM_VERSION

        self._version = ALGORITHM_VERSION

    @property
    def version(self) -> str:
        return self._version

    # --- fixture -> engine input ---------------------------------------------

    def _profile_version(self, profile: ProfileFixture):
        from app.models import (
            Certification,
            Education,
            Experience,
            ProfileVersion,
            Project,
            ProjectTechnology,
            Skill,
        )

        version = ProfileVersion(
            id=1,
            profile_id=1,
            version_number=1,
            status="confirmed",
            created_by_type="user",
        )
        counter = iter(range(1, 100_000))
        #: (engine source_type, row id) -> dataset ref
        self._refs: dict[tuple[str, int], str] = {}

        def remember(source_types: tuple[str, ...], row_id: int, ref: str) -> None:
            for source_type in source_types:
                self._refs[(source_type, row_id)] = ref

        skills = []
        for item in profile.skills:
            row_id = next(counter)
            skills.append(
                Skill(
                    id=row_id,
                    name=item.name,
                    years_of_experience=(
                        Decimal(str(item.years)) if item.years is not None else None
                    ),
                    proficiency=item.proficiency,
                )
            )
            remember(("skill",), row_id, item.ref)
        version.skills = skills

        experiences = []
        for item in profile.experiences:
            row_id = next(counter)
            experiences.append(
                Experience(
                    id=row_id,
                    company=item.company,
                    job_title=item.job_title,
                    start_date=item.start,
                    end_date=item.end,
                    description=item.description,
                    employment_type=item.employment_type,
                    # Passed through even though matching_v1 never reads it.
                    # Withholding data the profile schema genuinely carries
                    # would make "location is not evaluated" a finding about
                    # this adapter rather than about the engine.
                    location=item.location,
                )
            )
            remember(("experience",), row_id, item.ref)
        version.experiences = experiences

        education = []
        for item in profile.education:
            row_id = next(counter)
            education.append(
                Education(
                    id=row_id,
                    institution=item.institution,
                    degree=item.degree,
                    field_of_study=item.field_of_study,
                )
            )
            remember(("education",), row_id, item.ref)
        version.education = education

        certifications = []
        for item in profile.certifications:
            row_id = next(counter)
            certifications.append(
                Certification(
                    id=row_id,
                    name=item.name,
                    issuing_organization=item.issuer,
                    expiration_date=item.expires,
                )
            )
            remember(("certification",), row_id, item.ref)
        version.certifications = certifications

        projects = []
        for item in profile.projects:
            row_id = next(counter)
            project = Project(
                id=row_id,
                name=item.name,
                description=item.description,
                start_date=item.start,
                end_date=item.end,
            )
            project.technologies = [
                ProjectTechnology(project_id=row_id, name=name) for name in item.technologies
            ]
            projects.append(project)
            # A project is cited under two engine source types depending on
            # whether the technology was listed or merely described; both mean
            # the same project to a reviewer.
            remember(("project", "project_technology"), row_id, item.ref)
        version.projects = projects

        return version

    def _candidate(self, job: JobFixture):
        """Build the engine's input, recording where each requirement landed.

        The position map is what makes the reverse mapping exact. Matching on
        the label would merge two requirements a posting states twice — which
        real postings do, and which the benchmark has to keep apart.
        """
        from app.schemas.job_candidate import (
            JobCandidate,
            JobCertificationItem,
            JobEducationItem,
            JobExperienceItem,
            JobProvenance,
            JobQualificationItem,
            JobSkillItem,
            JobTechnologyItem,
        )

        def cite(requirement: Requirement) -> list[JobProvenance]:
            # Citations belong to the job domain and are verified there; the
            # matcher never reads them. Present because JobCandidate is the
            # real schema and the benchmark runs against the real shape.
            return [
                JobProvenance(
                    source_section="requirements",
                    quote=(requirement.source_quote or requirement.label)[:500],
                    confidence=0.9,
                    verified=True,
                )
            ]

        buckets: dict[str, list[Requirement]] = {
            "required_skills": [],
            "preferred_skills": [],
            "technologies": [],
            "required_experience": [],
            "preferred_experience": [],
            "education": [],
            "certifications": [],
            "qualifications": [],
        }
        for requirement in job.requirements:
            if requirement.category == "skill":
                buckets[
                    "required_skills" if requirement.is_required else "preferred_skills"
                ].append(requirement)
            elif requirement.category == "technology":
                buckets["technologies"].append(requirement)
            elif requirement.category == "experience":
                buckets[
                    "required_experience"
                    if requirement.is_required
                    else "preferred_experience"
                ].append(requirement)
            elif requirement.category == "education":
                buckets["education"].append(requirement)
            elif requirement.category == "certification":
                buckets["certifications"].append(requirement)
            else:
                buckets["qualifications"].append(requirement)

        # (engine category, engine requirement_index) -> requirement id.
        # Mirrors how `engine.evaluate` numbers what it walks: preferred skills
        # and preferred experience continue their required list's sequence
        # rather than restarting.
        self._positions: dict[tuple[str, int], str] = {}
        for index, requirement in enumerate(buckets["required_skills"]):
            self._positions[("skill", index)] = requirement.id
        offset = len(buckets["required_skills"])
        for index, requirement in enumerate(buckets["preferred_skills"]):
            self._positions[("skill", offset + index)] = requirement.id
        for index, requirement in enumerate(buckets["technologies"]):
            self._positions[("technology", index)] = requirement.id
        for index, requirement in enumerate(buckets["required_experience"]):
            self._positions[("experience", index)] = requirement.id
        offset = len(buckets["required_experience"])
        for index, requirement in enumerate(buckets["preferred_experience"]):
            self._positions[("experience", offset + index)] = requirement.id
        for index, requirement in enumerate(buckets["education"]):
            self._positions[("education", index)] = requirement.id
        for index, requirement in enumerate(buckets["certifications"]):
            self._positions[("certification", index)] = requirement.id
        for index, requirement in enumerate(buckets["qualifications"]):
            self._positions[("qualification", index)] = requirement.id

        return JobCandidate(
            required_skills=[
                JobSkillItem(name=r.label, provenance=cite(r))
                for r in buckets["required_skills"]
            ],
            preferred_skills=[
                JobSkillItem(name=r.label, provenance=cite(r))
                for r in buckets["preferred_skills"]
            ],
            technologies=[
                JobTechnologyItem(name=r.label, is_required=r.is_required, provenance=cite(r))
                for r in buckets["technologies"]
            ],
            required_experience=[
                JobExperienceItem(
                    description=r.label, minimum_years=r.minimum_years, provenance=cite(r)
                )
                for r in buckets["required_experience"]
            ],
            preferred_experience=[
                JobExperienceItem(
                    description=r.label, minimum_years=r.minimum_years, provenance=cite(r)
                )
                for r in buckets["preferred_experience"]
            ],
            education=[
                JobEducationItem(
                    description=r.label,
                    degree_level=r.degree_level,
                    field_of_study=r.field_of_study,
                    is_required=r.is_required,
                    provenance=cite(r),
                )
                for r in buckets["education"]
            ],
            certifications=[
                JobCertificationItem(
                    name=r.label, is_required=r.is_required, provenance=cite(r)
                )
                for r in buckets["certifications"]
            ],
            qualifications=[
                JobQualificationItem(
                    description=r.label, is_required=r.is_required, provenance=cite(r)
                )
                for r in buckets["qualifications"]
            ],
        )

    # --- running ---------------------------------------------------------------

    def run(
        self, *, profile: ProfileFixture, job: JobFixture, as_of: date
    ) -> AlgorithmResult:
        from app.services.matching.engine import JobPosting, evaluate

        version = self._profile_version(profile)
        candidate = self._candidate(job)
        evaluation = evaluate(
            candidate,
            version,
            as_of=as_of,
            # The fixture's own `location` field is the analogue of
            # `Job.location_raw`, and is handed over for the same reason
            # `Experience.location` is: withholding data the real schema
            # carries would make "location is not evaluated" a finding about
            # this adapter rather than about the engine.
            posting=JobPosting(location_raw=job.location, workplace_type=None),
        )

        verdicts = []
        for judgment in evaluation.judgments:
            requirement_id = self._positions.get(
                (judgment.category, judgment.requirement_index)
            )
            if requirement_id is None:
                # The engine judged something the adapter did not place. That
                # is an adapter bug, and reporting it as a matcher failure
                # would send someone to read the wrong code.
                raise RuntimeError(
                    f"unmapped judgment {judgment.category}#{judgment.requirement_index} "
                    f"({judgment.requirement_label!r}) for job {job.id}"
                )
            sources = {item.source_type for item in judgment.evidence}
            refs = tuple(
                dict.fromkeys(
                    ref
                    for item in judgment.evidence
                    if (ref := self._refs.get((item.source_type, item.source_id))) is not None
                )
            )
            verdicts.append(
                Verdict(
                    requirement_id=requirement_id,
                    status=judgment.status,
                    is_required=judgment.is_required,
                    explanation=judgment.explanation,
                    refs=refs,
                    rule_label=_rule_label(judgment.category, judgment.status, sources),
                )
            )

        return AlgorithmResult(
            algorithm_version=evaluation.algorithm_version,
            verdicts=tuple(verdicts),
            self_consistent=_engine_self_consistent(evaluation),
            summary={
                "required_total": evaluation.score.required.total,
                "required_matched": evaluation.score.required.matched,
                "required_missing": evaluation.score.required.missing,
                "required_uncertain": evaluation.score.required.uncertain,
                "preferred_total": evaluation.score.preferred.total,
                "preferred_matched": evaluation.score.preferred.matched,
                "preferred_missing": evaluation.score.preferred.missing,
                "preferred_uncertain": evaluation.score.preferred.uncertain,
                "required_coverage": float(evaluation.score.required.coverage),
                "preferred_coverage": float(evaluation.score.preferred.coverage),
                "overall_score": float(evaluation.score.overall),
            },
        )


def _engine_self_consistent(evaluation) -> bool:
    """Does the engine's published summary agree with its own verdicts?

    Specific to this engine because the formula is this engine's — which is
    why it lives in the adapter and not in `metrics.py`. It asks nothing about whether the verdicts
    are RIGHT, only whether the counts and coverage describe the verdicts that
    were actually reached. A summary that disagrees with its own inputs would
    be invisible in every other measure here.

    The formula is not re-derived to check the score: the counts are recounted
    from the verdicts, and the coverage is checked against those counts.
    """
    totals = evaluation.score
    for axis, required in ((totals.required, True), (totals.preferred, False)):
        selected = [j for j in evaluation.judgments if j.is_required is required]
        counted = {
            "total": len(selected),
            "matched": sum(1 for j in selected if j.status == "matched"),
            "missing": sum(1 for j in selected if j.status == "missing"),
            "uncertain": sum(1 for j in selected if j.status == "uncertain"),
        }
        if (axis.total, axis.matched, axis.missing, axis.uncertain) != (
            counted["total"],
            counted["matched"],
            counted["missing"],
            counted["uncertain"],
        ):
            return False
        if counted["matched"] + counted["missing"] + counted["uncertain"] != counted["total"]:
            return False
        expected_coverage = (
            Decimal(counted["matched"]) / Decimal(counted["total"])
            if counted["total"]
            else Decimal("1.0000")
        )
        if axis.coverage != expected_coverage.quantize(Decimal("0.0001")):
            return False
    return Decimal("0") <= totals.overall <= Decimal("1")


#: The one implementation currently in the tree. A second entry here is what a
#: semantic or hybrid matcher would add — and the dataset, the ground truth,
#: the metrics and the failure categories would not move.
_ADAPTERS = {"engine": DeterministicEngineAdapter}


def build(name: str = "engine") -> MatchingAlgorithm:
    """An adapter by name.

    A version string is accepted too, and checked rather than trusted: asking
    for `matching_v1` when the tree now ships `matching_v2` is refused instead
    of silently benchmarking the wrong thing and labelling the report with the
    version that was asked for.
    """
    if name in _ADAPTERS:
        return _ADAPTERS[name]()
    built = DeterministicEngineAdapter()
    if name == built.version:
        return built
    raise SystemExit(
        f"unknown algorithm {name!r}. This tree ships {built.version!r}; add an "
        f"adapter implementing MatchingAlgorithm for anything else."
    )
