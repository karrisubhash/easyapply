"""Running the matcher over the evaluation dataset and scoring what it decided.

The third of these runners, after `runner.py` (resumes) and `job_runner.py`
(postings), and the one with the simplest unit of truth: every case states, for
each requirement, the verdict a careful person would reach. Scoring is then a
comparison of verdicts, not of extracted text.

Five measures, and the reason for each:

  classification_accuracy  of the requirements the case pins, how many got the
                           expected verdict. The headline, and on its own not
                           enough — the errors below are not equally bad.

  false_matches            expected MISSING or UNCERTAIN, reported MATCHED.
                           The worst output this system can produce: unlike a
                           miss it is invisible to the person reading it, who
                           has no reason to look. Counted separately and
                           reported first for that reason.

  false_missing            expected MATCHED or UNCERTAIN, reported MISSING.
                           A real error — it hides evidence the profile
                           actually contains — but a visible one.

  uncertainty_errors       the two directions of misusing UNCERTAIN, kept
                           apart: `overcautious` is a verdict the profile
                           settles being reported as doubt, `overconfident` is
                           doubt reported as a settled verdict. Folding them
                           together would hide which way the engine leans.

  score_consistency        the stored counts agree with the verdicts, the
                           coverage agrees with the counts, and a second run
                           produces the identical result. §15's claim, checked
                           rather than asserted.

Deliberately not a test — the same reasoning `runner.py` gives for itself. It
reports numbers for a person to read. A threshold checked into CI before
anyone knows what a good score looks like freezes today's accident into
tomorrow's requirement, and §22 says in as many words not to optimize the
score to look good.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.models import (
    Certification,
    Education,
    Experience,
    ProfileVersion,
    Project,
    ProjectTechnology,
    Skill,
)
from app.models.match import STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
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
from app.services.matching.engine import ALGORITHM_VERSION, evaluate

DATASETS = Path(__file__).parent / "datasets"
DEFAULT_VERSION = "matching_v2"


def load_cases(version: str = DEFAULT_VERSION) -> dict[str, Any]:
    path = DATASETS / version / "cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- building the two sides from a case ---------------------------------------
#
# In-memory ORM objects, never added to a session: the engine is a pure
# function of (JobCandidate, ProfileVersion, as_of), so the evaluation needs no
# database and gains nothing from having one.


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def as_profile_version(spec: dict[str, Any]) -> ProfileVersion:
    version = ProfileVersion(
        id=1, profile_id=1, version_number=1, status="confirmed", created_by_type="user"
    )
    counter = iter(range(1, 10_000))

    version.skills = [
        Skill(
            id=next(counter),
            name=item["name"],
            years_of_experience=(
                Decimal(str(item["years"])) if item.get("years") is not None else None
            ),
            proficiency=item.get("proficiency"),
        )
        for item in spec.get("skills", [])
    ]
    version.experiences = [
        Experience(
            id=next(counter),
            company=item.get("company", "Acme"),
            job_title=item.get("job_title", "Engineer"),
            start_date=_day(item.get("start")),
            end_date=_day(item.get("end")),
            description=item.get("description"),
        )
        for item in spec.get("experiences", [])
    ]
    version.education = [
        Education(
            id=next(counter),
            institution=item.get("institution", "State University"),
            degree=item.get("degree"),
            field_of_study=item.get("field_of_study"),
        )
        for item in spec.get("education", [])
    ]
    version.certifications = [
        Certification(
            id=next(counter),
            name=item["name"],
            issuing_organization=item.get("issuer"),
            expiration_date=_day(item.get("expires")),
        )
        for item in spec.get("certifications", [])
    ]

    projects = []
    for item in spec.get("projects", []):
        project = Project(
            id=next(counter),
            name=item["name"],
            description=item.get("description"),
            start_date=_day(item.get("start")),
            end_date=_day(item.get("end")),
        )
        project.technologies = [
            ProjectTechnology(project_id=project.id, name=name)
            for name in item.get("technologies", [])
        ]
        projects.append(project)
    version.projects = projects

    return version


def _cite(quote: str) -> list[JobProvenance]:
    """Citations are the JOB domain's concern and are verified there; the
    matcher never reads them. Present because `JobCandidate` is the real
    schema and the evaluation must run against the real shape."""
    return [JobProvenance(source_section="requirements", quote=quote, confidence=0.9, verified=True)]


def as_candidate(spec: dict[str, Any]) -> JobCandidate:
    return JobCandidate(
        required_skills=[
            JobSkillItem(name=name, provenance=_cite(name))
            for name in spec.get("required_skills", [])
        ],
        preferred_skills=[
            JobSkillItem(name=name, provenance=_cite(name))
            for name in spec.get("preferred_skills", [])
        ],
        technologies=[
            JobTechnologyItem(name=name, is_required=required, provenance=_cite(name))
            for name, required in spec.get("technologies", [])
        ],
        required_experience=[
            JobExperienceItem(description=text, minimum_years=years, provenance=_cite(text))
            for text, years in spec.get("required_experience", [])
        ],
        preferred_experience=[
            JobExperienceItem(description=text, minimum_years=years, provenance=_cite(text))
            for text, years in spec.get("preferred_experience", [])
        ],
        education=[
            JobEducationItem(
                description=text,
                degree_level=level,
                field_of_study=field_of_study,
                is_required=required,
                provenance=_cite(text),
            )
            for text, level, field_of_study, required in spec.get("education", [])
        ],
        certifications=[
            JobCertificationItem(name=name, is_required=required, provenance=_cite(name))
            for name, required in spec.get("certifications", [])
        ],
        qualifications=[
            JobQualificationItem(description=text, is_required=required, provenance=_cite(text))
            for text, required in spec.get("qualifications", [])
        ],
    )


# --- scoring ------------------------------------------------------------------


@dataclass
class RequirementOutcome:
    category: str
    label: str
    is_required: bool
    expected: str
    actual: str | None
    has_evidence: bool

    @property
    def correct(self) -> bool:
        return self.actual == self.expected

    @property
    def false_match(self) -> bool:
        """Reported MATCHED where the case says it is not. The failure mode
        this whole milestone is arranged to avoid."""
        return self.actual == STATUS_MATCHED and self.expected != STATUS_MATCHED

    @property
    def false_missing(self) -> bool:
        return self.actual == STATUS_MISSING and self.expected != STATUS_MISSING

    @property
    def overcautious(self) -> bool:
        """UNCERTAIN where the profile actually settles the question."""
        return self.actual == STATUS_UNCERTAIN and self.expected != STATUS_UNCERTAIN

    @property
    def overconfident(self) -> bool:
        """A settled verdict where the case says the profile cannot settle it."""
        return self.expected == STATUS_UNCERTAIN and self.actual not in (None, STATUS_UNCERTAIN)


@dataclass
class CaseScore:
    case_id: str
    shape: str
    outcomes: list[RequirementOutcome] = field(default_factory=list)
    #: Requirements the case pins that the engine never produced a verdict for.
    unjudged: list[str] = field(default_factory=list)
    #: Verdicts the engine produced that the case does not pin. Not an error —
    #: a case may pin only the requirements it is about — but reported so a
    #: case that has quietly stopped covering its own subject is visible.
    unpinned: int = 0
    score_consistent: bool = True
    reproducible: bool = True
    error: str | None = None

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def correct(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.correct)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def false_matches(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.false_match)

    @property
    def false_missing(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.false_missing)

    @property
    def overcautious(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.overcautious)

    @property
    def overconfident(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.overconfident)

    @property
    def evidence_complete(self) -> bool:
        """No MATCHED verdict anywhere without something to point at."""
        return all(
            outcome.has_evidence
            for outcome in self.outcomes
            if outcome.actual == STATUS_MATCHED
        )


def _key(category: str, label: str) -> tuple[str, str]:
    return category, " ".join(label.split()).casefold()


def score_case(case: dict[str, Any]) -> CaseScore:
    as_of = date.fromisoformat(case["as_of"])
    version = as_profile_version(case.get("profile", {}))
    candidate = as_candidate(case.get("job", {}))

    evaluation = evaluate(candidate, version, as_of=as_of)
    again = evaluate(candidate, version, as_of=as_of)

    produced = {
        _key(judgment.category, judgment.requirement_label): judgment
        for judgment in evaluation.judgments
    }

    score = CaseScore(case_id=case["id"], shape=case["shape"])
    score.reproducible = (
        evaluation.judgments == again.judgments
        and evaluation.score.overall == again.score.overall
    )
    score.score_consistent = _score_is_consistent(evaluation)

    pinned = set()
    for expectation in case["expected"]:
        key = _key(expectation["category"], expectation["label"])
        pinned.add(key)
        judgment = produced.get(key)
        if judgment is None:
            score.unjudged.append(f"{expectation['category']}:{expectation['label']}")
            continue
        score.outcomes.append(
            RequirementOutcome(
                category=expectation["category"],
                label=expectation["label"],
                is_required=judgment.is_required,
                expected=expectation["status"],
                actual=judgment.status,
                has_evidence=bool(judgment.evidence),
            )
        )

    score.unpinned = len([key for key in produced if key not in pinned])
    return score


def _score_is_consistent(evaluation) -> bool:
    """The counts say what the verdicts say, and the coverage says what the
    counts say. A scoring bug that disagreed with its own inputs would be
    invisible in the headline accuracy."""
    totals = evaluation.score
    for axis, required in ((totals.required, True), (totals.preferred, False)):
        selected = [j for j in evaluation.judgments if j.is_required is required]
        if axis.total != len(selected):
            return False
        if axis.matched != sum(1 for j in selected if j.status == STATUS_MATCHED):
            return False
        if axis.missing != sum(1 for j in selected if j.status == STATUS_MISSING):
            return False
        if axis.uncertain != sum(1 for j in selected if j.status == STATUS_UNCERTAIN):
            return False
        if axis.matched + axis.missing + axis.uncertain != axis.total:
            return False
        expected_coverage = (
            Decimal(axis.matched) / Decimal(axis.total)
            if axis.total
            else Decimal("1.0000")
        )
        if axis.coverage != expected_coverage.quantize(Decimal("0.0001")):
            return False
    return Decimal("0") <= totals.overall <= Decimal("1")


def run(*, version: str = DEFAULT_VERSION) -> list[CaseScore]:
    dataset = load_cases(version)
    scores = []
    for case in dataset["cases"]:
        try:
            scores.append(score_case(case))
        except Exception as exc:  # a matcher that raises scores zero, loudly
            scores.append(
                CaseScore(case_id=case["id"], shape=case["shape"], error=repr(exc))
            )
    return scores


# --- reporting ----------------------------------------------------------------


def report(scores: list[CaseScore]) -> str:
    lines = [
        f"algorithm: {ALGORITHM_VERSION}",
        "",
        f"{'case':<38} {'acc':>6} {'n':>3} {'false+':>7} {'false-':>7} {'unc±':>6}",
        "-" * 72,
    ]
    for score in scores:
        if score.error:
            lines.append(f"{score.case_id:<38} {'ERROR':>6}  {score.error}")
            continue
        lines.append(
            f"{score.case_id:<38} {score.accuracy:>6.2f} {score.total:>3} "
            f"{score.false_matches:>7} {score.false_missing:>7} "
            f"{score.overcautious + score.overconfident:>6}"
        )

    scored = [s for s in scores if not s.error]
    total = sum(s.total for s in scored)
    correct = sum(s.correct for s in scored)
    lines += [
        "-" * 72,
        f"requirements judged        {total}",
        f"classification accuracy    {correct / total if total else 0:.3f}",
        f"false matches              {sum(s.false_matches for s in scored)}"
        "   (reported MATCHED where the case says otherwise)",
        f"false missing              {sum(s.false_missing for s in scored)}",
        f"uncertainty overcautious   {sum(s.overcautious for s in scored)}",
        f"uncertainty overconfident  {sum(s.overconfident for s in scored)}",
        f"evidence complete          {all(s.evidence_complete for s in scored)}",
        f"score consistent           {all(s.score_consistent for s in scored)}",
        f"reproducible               {all(s.reproducible for s in scored)}",
    ]
    unjudged = [item for s in scored for item in s.unjudged]
    if unjudged:
        lines.append(f"UNJUDGED requirements      {unjudged}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    scores = run(version=args.version)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "case_id": s.case_id,
                        "accuracy": s.accuracy,
                        "total": s.total,
                        "false_matches": s.false_matches,
                        "false_missing": s.false_missing,
                        "overcautious": s.overcautious,
                        "overconfident": s.overconfident,
                        "error": s.error,
                    }
                    for s in scores
                ],
                indent=2,
            )
        )
    else:
        print(report(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
