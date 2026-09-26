"""Running a job structurer over the evaluation dataset and scoring what it read.

The mirror of `evaluation/runner.py`, adapted to what `JobCandidate` actually
is: skills and experience are already split into required/preferred lists at
the schema level, while technologies, education, certifications and
qualifications carry `is_required` per item, and responsibilities carry no
required/preferred axis at all. `CATEGORY_CONFIG` below encodes that shape once
so the scoring does not have to special-case it per category.

Deliberately not a test — same reasoning `evaluation/runner.py` gives for
itself: this reports numbers for a person to read, not a threshold to go red
against.

Four measures per category, and the shape mirrors the milestone this evaluates
against, not the resume domain's three:

  recall                  of what the case says is there, how much was found
  precision                of what was found, how much the case actually states
  fabrication              items reported that the case does not contain at all —
                            counted separately from precision because a miss and
                            an invention are not equally bad; see below
  classification_accuracy  of items that WERE found (matched by identity,
                            ignoring required/preferred), how many landed in the
                            correct bucket. Measured separately from recall and
                            precision because "found the right technology but
                            called it preferred instead of required" is a
                            different failure from "invented a technology" —
                            the milestone this evaluates asks for exactly that
                            separation.

Provenance is scored across the whole case, not per category: of every
citation the structurer produced, what fraction survived
`verify_job_provenance` against the case's own description text. A
deterministic structurer that only ever quotes text it read should score at or
near 1.0; a provider that fabricates a citation shows up here directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.job import Job
from app.services.job.structuring import verify_job_provenance

DATASETS = Path(__file__).parent / "datasets"
DEFAULT_VERSION = "jobs_v1"


def load_cases(version: str = DEFAULT_VERSION) -> dict[str, Any]:
    path = DATASETS / version / "cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def as_job(case: dict[str, Any]) -> Job:
    """A case's description, in the shape a real `Job` row would carry it."""
    return Job(
        id=0,
        user_id=0,
        source_type="other",
        source_url="https://example.test/jobs/evaluation-case",
        canonical_url="https://example.test/jobs/evaluation-case",
        content_hash="0" * 64,
        job_title=case.get("job_title", "Untitled role"),
        company_name=case.get("company_name"),
        raw_payload={},
        description_text=case["description"],
    )


# --- the shape of a JobCandidate, encoded once --------------------------------
#
# "split": two separate list attributes, already required vs preferred at the
#          schema level (required_skills/preferred_skills,
#          required_experience/preferred_experience).
# "flag":  one list attribute; each item carries its own `is_required`.
# "flat":  one list attribute with no required/preferred axis at all
#          (responsibilities).


@dataclass(frozen=True)
class CategoryConfig:
    kind: str
    identity_fields: tuple[str, ...]
    required_attr: str | None = None
    preferred_attr: str | None = None
    flag_attr: str | None = None


CATEGORY_CONFIG: dict[str, CategoryConfig] = {
    "skills": CategoryConfig(
        "split", ("name",), required_attr="required_skills", preferred_attr="preferred_skills"
    ),
    "experience": CategoryConfig(
        "split",
        ("minimum_years", "maximum_years"),
        required_attr="required_experience",
        preferred_attr="preferred_experience",
    ),
    "technologies": CategoryConfig("flag", ("name",), flag_attr="technologies"),
    "education": CategoryConfig("flag", ("degree_level",), flag_attr="education"),
    "certifications": CategoryConfig("flag", ("name",), flag_attr="certifications"),
    "qualifications": CategoryConfig("flag", ("description",), flag_attr="qualifications"),
    "responsibilities": CategoryConfig("flat", ("description",), flag_attr="responsibilities"),
}


def _normalise(value: Any) -> Any:
    """Compare on meaning, not on typography — the same reasoning
    `evaluation/runner.py._normalise` gives for resumes."""
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return value


def _identity(item: Any, fields: tuple[str, ...]) -> tuple:
    def get(name: str) -> Any:
        raw = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
        return _normalise(raw)

    return tuple(get(name) for name in fields)


def _produced_required_and_preferred(candidate, config: CategoryConfig) -> tuple[list, list]:
    if config.kind == "split":
        return (
            list(getattr(candidate, config.required_attr)),
            list(getattr(candidate, config.preferred_attr)),
        )
    if config.kind == "flag":
        items = list(getattr(candidate, config.flag_attr))
        return [i for i in items if i.is_required], [i for i in items if not i.is_required]
    # "flat": no axis, so everything is "required" for bookkeeping purposes —
    # scored below with classification skipped for this kind.
    return list(getattr(candidate, config.flag_attr)), []


@dataclass
class CategoryScore:
    category: str
    expected: int = 0
    produced: int = 0
    matched: int = 0
    fabricated: list[str] = field(default_factory=list)
    #: Of items matched by identity alone, how many were in the right bucket.
    classification_total: int = 0
    classification_correct: int = 0

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0

    @property
    def precision(self) -> float:
        return self.matched / self.produced if self.produced else 1.0

    @property
    def classification_accuracy(self) -> float | None:
        if self.classification_total == 0:
            return None
        return self.classification_correct / self.classification_total


def score_category(
    category: str, config: CategoryConfig, case_expected: dict[str, Any], candidate
) -> CategoryScore:
    expected_required = [_identity(i, config.identity_fields) for i in case_expected.get("required", [])]
    expected_preferred = [_identity(i, config.identity_fields) for i in case_expected.get("preferred", [])]
    expected_all = expected_required + expected_preferred

    produced_required_items, produced_preferred_items = _produced_required_and_preferred(candidate, config)
    produced_required = [_identity(i, config.identity_fields) for i in produced_required_items]
    produced_preferred = [_identity(i, config.identity_fields) for i in produced_preferred_items]

    score = CategoryScore(
        category=category,
        expected=len(expected_all),
        produced=len(produced_required) + len(produced_preferred),
    )

    # Recall/precision/fabrication: pool required+preferred together first,
    # so a correctly-found item in the wrong bucket is not double-punished as
    # both a miss and a fabrication — that failure is what
    # classification_accuracy exists to measure on its own.
    remaining = list(expected_all)
    for produced_key in produced_required + produced_preferred:
        if produced_key in remaining:
            remaining.remove(produced_key)
            score.matched += 1
        else:
            score.fabricated.append(str(produced_key))

    if config.kind != "flat":
        # Classification accuracy, over items that genuinely matched
        # something expected: was the match in the bucket the case says it
        # should be in?
        remaining_required = list(expected_required)
        remaining_preferred = list(expected_preferred)
        for key in produced_required:
            if key in remaining_required:
                remaining_required.remove(key)
                score.classification_total += 1
                score.classification_correct += 1
            elif key in remaining_preferred:
                remaining_preferred.remove(key)
                score.classification_total += 1
        for key in produced_preferred:
            if key in remaining_preferred:
                remaining_preferred.remove(key)
                score.classification_total += 1
                score.classification_correct += 1
            elif key in remaining_required:
                remaining_required.remove(key)
                score.classification_total += 1

    return score


@dataclass
class CaseScore:
    case_id: str
    shape: str
    categories: dict[str, CategoryScore]
    provenance_total: int = 0
    provenance_verified: int = 0
    error: str | None = None

    @property
    def recall(self) -> float:
        values = [c.recall for c in self.categories.values()]
        return sum(values) / len(values) if values else 0.0

    @property
    def precision(self) -> float:
        values = [c.precision for c in self.categories.values()]
        return sum(values) / len(values) if values else 0.0

    @property
    def fabrications(self) -> int:
        return sum(len(c.fabricated) for c in self.categories.values())

    @property
    def classification_accuracy(self) -> float | None:
        values = [c.classification_accuracy for c in self.categories.values() if c.classification_accuracy is not None]
        return sum(values) / len(values) if values else None

    @property
    def provenance_verified_rate(self) -> float:
        return self.provenance_verified / self.provenance_total if self.provenance_total else 1.0


_PROVENANCE_SECTIONS = (
    "required_skills",
    "preferred_skills",
    "required_experience",
    "preferred_experience",
    "education",
    "certifications",
    "responsibilities",
    "technologies",
    "qualifications",
)


def _score_provenance(candidate, description: str | None) -> tuple[int, int]:
    verify_job_provenance(candidate, description)
    total = verified = 0
    for section_name in _PROVENANCE_SECTIONS:
        for item in getattr(candidate, section_name):
            for entry in item.provenance:
                total += 1
                if entry.verified:
                    verified += 1
    return total, verified


def score_case(case: dict[str, Any], candidate, description: str | None) -> CaseScore:
    categories = {
        name: score_category(name, config, case["expected"].get(name, {}), candidate)
        for name, config in CATEGORY_CONFIG.items()
        if name in case["expected"]
    }
    provenance_total, provenance_verified = _score_provenance(candidate, description)
    return CaseScore(
        case_id=case["id"],
        shape=case["shape"],
        categories=categories,
        provenance_total=provenance_total,
        provenance_verified=provenance_verified,
    )


# --- running -----------------------------------------------------------------


def build_structurer(name: str):
    from app.services.job.structuring import DeterministicJobStructurer, ProviderJobStructurer

    if name == "deterministic":
        return DeterministicJobStructurer()
    if name == "ai":
        return ProviderJobStructurer()
    raise SystemExit(f"unknown structurer {name!r}; use 'deterministic' or 'ai'")


def run(*, structurer=None, version: str = DEFAULT_VERSION) -> list[CaseScore]:
    dataset = load_cases(version)
    structurer = structurer or build_structurer("deterministic")

    scores: list[CaseScore] = []
    for case in dataset["cases"]:
        job = as_job(case)
        try:
            reading = structurer.structure(job)
        except Exception as exc:  # a structurer that fails is a score of zero
            scores.append(CaseScore(case_id=case["id"], shape=case["shape"], categories={}, error=type(exc).__name__))
            continue
        scores.append(score_case(case, reading.candidate, job.description_text))
    return scores


def report(scores: list[CaseScore]) -> str:
    lines = [
        f"{'case':<32} {'recall':>7} {'prec':>6} {'fab':>4} {'class':>7} {'prov':>6}",
        "-" * 68,
    ]
    for score in scores:
        if score.error:
            lines.append(f"{score.case_id:<32} {'ERROR':>7} {score.error}")
            continue
        class_acc = score.classification_accuracy
        class_str = f"{class_acc:.2f}" if class_acc is not None else "n/a"
        lines.append(
            f"{score.case_id:<32} {score.recall:>7.2f} {score.precision:>6.2f} "
            f"{score.fabrications:>4} {class_str:>7} {score.provenance_verified_rate:>6.2f}"
        )

    scored = [s for s in scores if not s.error]
    if scored:
        lines.append("-" * 68)
        class_values = [s.classification_accuracy for s in scored if s.classification_accuracy is not None]
        class_mean = f"{sum(class_values) / len(class_values):.2f}" if class_values else "n/a"
        lines.append(
            f"{'mean':<32} "
            f"{sum(s.recall for s in scored) / len(scored):>7.2f} "
            f"{sum(s.precision for s in scored) / len(scored):>6.2f} "
            f"{sum(s.fabrications for s in scored):>4} "
            f"{class_mean:>7} "
            f"{sum(s.provenance_verified_rate for s in scored) / len(scored):>6.2f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structurer", default="deterministic", choices=["deterministic", "ai"])
    parser.add_argument("--version", default=DEFAULT_VERSION)
    args = parser.parse_args(argv)

    scores = run(structurer=build_structurer(args.structurer), version=args.version)
    print(report(scores))
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
