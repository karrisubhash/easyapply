"""Naming what went wrong. Knows nothing about any matcher.

A failure category here is derived from two things only: the `aspect` the
reviewer attached to the requirement when they wrote the case, and the
DIRECTION of the disagreement. Nothing consults a matching rule, which is what
keeps the diagnosis usable when matching_v1 is replaced — "missing alias" means
the same thing about a semantic model as about a lookup table.

`aspect` is a property of the case, authored before any matcher ran: it records
what makes the requirement interesting to judge, not a prediction that anything
will fail. That distinction is what stops this file from becoming a second,
sloppier copy of the rules.

The direction matters as much as the aspect. On an `alias` requirement,
expected MATCHED / actual MISSING is a missing alias; the reverse, on a
`near_synonym` requirement, is a false one. Same aspect, opposite defects,
opposite fixes.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.benchmark.metrics import Observation
from evaluation.benchmark.schema import VERDICT_MATCHED, VERDICT_MISSING

# --- the categories -------------------------------------------------------------

MISSING_ALIAS = "missing_alias"
FALSE_ALIAS = "false_alias"
NORMALIZATION = "normalization"
EVIDENCE_STRENGTH = "evidence_strength"
EXPERIENCE_ATTRIBUTION = "experience_attribution"
EXPERIENCE_DURATION = "experience_duration"
EDUCATION_AMBIGUITY = "education_ambiguity"
CERTIFICATION = "certification"
SENIORITY = "seniority"
DOMAIN = "domain"
LOCATION = "location"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
REQUIREMENT_CLASSIFICATION = "requirement_classification"
EVIDENCE_MISMATCH = "evidence_mismatch"
UNJUDGED = "unjudged"
OTHER = "other"

CATEGORIES = (
    MISSING_ALIAS,
    FALSE_ALIAS,
    NORMALIZATION,
    EVIDENCE_STRENGTH,
    EXPERIENCE_ATTRIBUTION,
    EXPERIENCE_DURATION,
    EDUCATION_AMBIGUITY,
    CERTIFICATION,
    SENIORITY,
    DOMAIN,
    LOCATION,
    INSUFFICIENT_EVIDENCE,
    REQUIREMENT_CLASSIFICATION,
    EVIDENCE_MISMATCH,
    UNJUDGED,
    OTHER,
)

#: What each category means and, where it is actionable, what would fix it.
#: Written down because a failure name that nobody can act on is a label, not
#: a diagnosis.
CATEGORY_NOTES = {
    MISSING_ALIAS: "Two spellings of one thing were not recognised as one. Fixed by a justified alias.",
    FALSE_ALIAS: "Two different things were treated as one. The dangerous direction — fixed by REMOVING an equivalence.",
    NORMALIZATION: "The same name failed to match itself. A tokenisation or punctuation defect.",
    EVIDENCE_STRENGTH: "The evidence was real but read at the wrong strength: work plainly described in a role treated as a passing mention, or an unsupported list entry treated as a settled claim. Fixed by grading evidence rather than by sorting it into two bins.",
    EXPERIENCE_ATTRIBUTION: "Employment could not be tied to the subject, or non-employment was counted as employment.",
    EXPERIENCE_DURATION: "A duration was computed or compared wrongly.",
    EDUCATION_AMBIGUITY: "A degree level or field was read wrongly, or relatedness was asserted where it cannot be.",
    CERTIFICATION: "A credential's identity, level or validity was judged wrongly.",
    SENIORITY: "A seniority bar was not evaluated as such.",
    DOMAIN: "An industry or domain requirement was not evaluated as such.",
    LOCATION: "A location or work-authorization requirement was not evaluated as such.",
    INSUFFICIENT_EVIDENCE: "Weak evidence was read as strong, or as nothing at all.",
    REQUIREMENT_CLASSIFICATION: "The requirement was filed under the wrong required/preferred axis.",
    EVIDENCE_MISMATCH: "The verdict was right but the item cited to justify it was not.",
    UNJUDGED: "The matcher produced no verdict for a requirement the posting states.",
    OTHER: "A disagreement the aspect does not explain. Read the case.",
}

#: aspect -> the category its failures belong to, when direction does not
#: change the answer.
_BY_ASPECT = {
    "exact_skill": NORMALIZATION,
    "technology": NORMALIZATION,
    "certification": CERTIFICATION,
    "certification_variant": CERTIFICATION,
    "education_level": EDUCATION_AMBIGUITY,
    "education_field": EDUCATION_AMBIGUITY,
    "related_field": EDUCATION_AMBIGUITY,
    "experience_duration": EXPERIENCE_DURATION,
    "experience_insufficient": EXPERIENCE_DURATION,
    "experience_ambiguous": EXPERIENCE_ATTRIBUTION,
    "project_not_employment": EXPERIENCE_ATTRIBUTION,
    "overlapping_employment": EXPERIENCE_DURATION,
    "seniority": SENIORITY,
    "domain": DOMAIN,
    "location": LOCATION,
    "free_text": INSUFFICIENT_EVIDENCE,
    "missing_evidence": INSUFFICIENT_EVIDENCE,
    "implied_not_stated": INSUFFICIENT_EVIDENCE,
    # The three faces of the same defect: the matcher sorts evidence into
    # "declared in a field" and "everything else", and a reviewer grades it.
    "prose_evidence": EVIDENCE_STRENGTH,
    "uncorroborated_claim": EVIDENCE_STRENGTH,
    "weak_evidence": EVIDENCE_STRENGTH,
}


def categorise(observation: Observation) -> str:
    """The category a single disagreement belongs to."""
    if observation.actual is None:
        return UNJUDGED
    if not observation.classification_correct:
        # Checked before the verdict: an axis error is a different defect from
        # a verdict error, and reporting it as the latter would send someone to
        # look at the rules when the requirement was mis-filed.
        return REQUIREMENT_CLASSIFICATION
    if observation.agrees:
        # `evidence_checkable` is the guard, not `evidence_correct`: a correct
        # MISSING cites nothing by definition, and asking whether it cited the
        # right thing would report every agreed miss as an evidence failure.
        if observation.evidence_checkable and not observation.evidence_correct:
            return EVIDENCE_MISMATCH
        return OTHER

    aspect = observation.aspect

    # The two directions on the alias axis are opposite defects with opposite
    # fixes, so they are decided by direction rather than by aspect alone.
    if aspect in ("alias", "near_synonym"):
        if observation.actual == VERDICT_MATCHED and observation.expected != VERDICT_MATCHED:
            return FALSE_ALIAS
        if observation.expected == VERDICT_MATCHED and observation.actual == VERDICT_MISSING:
            return MISSING_ALIAS
        return NORMALIZATION

    return _BY_ASPECT.get(aspect, OTHER)


@dataclass(frozen=True)
class Failure:
    """One disagreement, with everything needed to adjudicate it.

    Carries the human's rationale alongside the matcher's explanation on
    purpose: most of the value of this benchmark is in reading the two side by
    side, and sometimes concluding that the human was wrong.
    """

    category: str
    case_id: str
    profile_id: str
    profile_label: str
    job_id: str
    job_label: str
    requirement_id: str
    requirement_label: str
    requirement_category: str
    aspect: str
    is_required: bool
    expected: str
    actual: str | None
    expected_rationale: str
    expected_refs: tuple[str, ...]
    actual_explanation: str | None
    actual_refs: tuple[str, ...]
    #: Which rule produced the verdict, as the matcher reports it. Opaque to
    #: this module — recorded for the reader, never branched on.
    actual_rule: str | None
    contested: bool

    @property
    def severity(self) -> int:
        """Ordering for the report: the errors a reader cannot see, first."""
        if self.category == FALSE_ALIAS:
            return 0
        if self.actual == VERDICT_MATCHED and self.expected != VERDICT_MATCHED:
            return 1
        if self.actual == VERDICT_MISSING and self.expected == VERDICT_MATCHED:
            return 2
        if self.category == EVIDENCE_MISMATCH:
            return 4
        return 3


def group(failures: list[Failure]) -> dict[str, list[Failure]]:
    grouped: dict[str, list[Failure]] = {}
    for failure in failures:
        grouped.setdefault(failure.category, []).append(failure)
    return {
        category: sorted(items, key=lambda f: (f.severity, f.case_id, f.requirement_id))
        for category, items in sorted(
            grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])
        )
    }


def summarise(failures: list[Failure]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for failure in failures:
        counts[failure.category] = counts.get(failure.category, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))
