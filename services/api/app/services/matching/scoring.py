"""The score, and what it is allowed to mean.

    required_coverage  = required_matched  / required_total     (1.0 if no required requirements)
    preferred_coverage = preferred_matched / preferred_total    (1.0 if no preferred requirements)

    overall_score      = 0.8 * required_coverage + 0.2 * preferred_coverage

Three decisions worth defending, because a score is the part of this system
most likely to be quoted without its context:

**UNCERTAIN counts toward the denominator, never the numerator.** A requirement
we could not settle is not a requirement we met. Excluding uncertain results
entirely would let a profile with two matched and eight unreadable requirements
report 100%, which is the exact failure this domain is built to avoid. Counting
them as met would be worse. So they dilute, and the counts beside the score say
how much of the dilution is doubt rather than absence — which is why the counts
are stored as columns and not derived.

**Required is weighted 4:1 over preferred, not treated as a gate.** A missing
preferred requirement genuinely matters less than a missing required one, and
the weighting says so; but nothing here turns a missing required requirement
into a rejection. Per §13 the engine reports required_matched /
required_missing / required_uncertain separately and lets product logic decide
what to do with them. The weights are the only place the two axes mix, and they
mix gently.

**An empty axis scores 1.0, not 0.0.** A posting with no preferred
requirements has not failed to have them. Coverage of nothing is complete by
construction — the alternative would penalise a profile for a posting's
brevity.

This number is coverage of structured job requirements by available profile
evidence. It is not a probability of being hired, an aptitude estimate, or a
ranking of candidates, and no docstring, column comment, API field description
or UI string in this codebase may describe it as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.match import STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
from app.services.matching.rules import RequirementJudgment

#: How much each axis contributes to the overall number. Explicit constants
#: rather than literals inline, because a weighting change is a matching
#: algorithm change and must be visible in review — and paired with an
#: ALGORITHM_VERSION bump.
REQUIRED_WEIGHT = Decimal("0.8")
PREFERRED_WEIGHT = Decimal("0.2")


@dataclass(frozen=True)
class AxisCounts:
    total: int = 0
    matched: int = 0
    missing: int = 0
    uncertain: int = 0

    @property
    def coverage(self) -> Decimal:
        if self.total == 0:
            return Decimal("1.0000")
        return (Decimal(self.matched) / Decimal(self.total)).quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class Score:
    required: AxisCounts
    preferred: AxisCounts

    @property
    def overall(self) -> Decimal:
        blended = REQUIRED_WEIGHT * self.required.coverage + PREFERRED_WEIGHT * self.preferred.coverage
        return blended.quantize(Decimal("0.0001"))


def _count(judgments: list[RequirementJudgment], *, required: bool) -> AxisCounts:
    selected = [judgment for judgment in judgments if judgment.is_required is required]
    return AxisCounts(
        total=len(selected),
        matched=sum(1 for judgment in selected if judgment.status == STATUS_MATCHED),
        missing=sum(1 for judgment in selected if judgment.status == STATUS_MISSING),
        uncertain=sum(1 for judgment in selected if judgment.status == STATUS_UNCERTAIN),
    )


def score(judgments: list[RequirementJudgment]) -> Score:
    """Counts and coverage, derived from verdicts that already exist.

    Deliberately takes the finished judgments rather than computing anything
    itself: the score is a summary of the requirement-level results, never a
    separate opinion that could disagree with them.
    """
    return Score(
        required=_count(judgments, required=True),
        preferred=_count(judgments, required=False),
    )
