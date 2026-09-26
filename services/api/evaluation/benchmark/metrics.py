"""Scoring a matcher against human judgments. Knows nothing about any matcher.

Takes `Observation`s — one (requirement, expected verdict, actual verdict,
cited evidence) quadruple each — and reduces them to numbers. Nothing here
knows how the actual verdict was reached, which is what lets the same metrics
score matching_v1, a matching_v2, or a semantic model without being rewritten.

**There is deliberately no headline accuracy.** A single agreement figure over
mixed verdicts is the most misleading number this benchmark could publish: a
matcher that answered UNCERTAIN to everything would score respectably on a
dataset with many genuinely uncertain requirements while being useless, and a
matcher that turned five real misses into confident matches would lose the same
few points as one that turned five matches into misses — though the first is
dangerous and the second is merely unhelpful.

So `agreement` exists, is reported, and is reported *below* the two asymmetric
error counts that actually matter:

    false_matches    the profile does not satisfy the requirement and the
                     matcher said it does. Invisible to the person reading the
                     result, because nothing invites them to check a green row.
                     This is the number to minimise first.

    false_missing    the profile does satisfy it and the matcher said nothing
                     was found. A real error — it hides evidence — but one the
                     user can see and correct.

Per-verdict precision and recall are one-vs-rest, and are what make the two
directions of an UNCERTAIN error legible: low UNCERTAIN precision means the
matcher hedges on questions the profile settles; low UNCERTAIN recall means it
answers confidently where it should not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evaluation.benchmark.schema import (
    VERDICT_MATCHED,
    VERDICT_MISSING,
    VERDICT_UNCERTAIN,
    VERDICTS,
)


@dataclass(frozen=True)
class Observation:
    """One requirement, as the human judged it and as the matcher judged it."""

    case_id: str
    requirement_id: str
    category: str
    aspect: str
    is_required: bool
    expected: str
    actual: str | None
    #: Whether the matcher judged this on the required or the preferred axis.
    #: Checked rather than assumed: a matcher that reads a requirement
    #: correctly but files it under the wrong axis reports a missing PREFERRED
    #: where a missing REQUIRED was meant, which is a different fact about the
    #: candidate even though the verdict word is identical.
    actual_is_required: bool | None = None
    #: Profile refs the matcher actually cited, resolved back to dataset refs.
    actual_refs: tuple[str, ...] = ()
    #: Refs the reviewer said would justify the verdict. Empty for MISSING.
    expected_refs: tuple[str, ...] = ()
    #: Whether the reviewer recorded a disagreement about this judgment.
    contested: bool = False

    @property
    def agrees(self) -> bool:
        return self.actual == self.expected

    @property
    def is_false_match(self) -> bool:
        return self.actual == VERDICT_MATCHED and self.expected != VERDICT_MATCHED

    @property
    def is_false_missing(self) -> bool:
        return self.actual == VERDICT_MISSING and self.expected != VERDICT_MISSING

    @property
    def classification_correct(self) -> bool:
        """Whether required/preferred was read the way the posting states it."""
        return self.actual_is_required is None or self.actual_is_required == self.is_required

    @property
    def evidence_checkable(self) -> bool:
        """Whether citing the right thing is even a question here.

        Only when the reviewer named an item AND the matcher reached the same
        verdict. Comparing evidence across a disagreement would double-count
        the disagreement as an evidence failure.
        """
        return bool(self.expected_refs) and self.agrees and self.actual != VERDICT_MISSING

    @property
    def evidence_correct(self) -> bool:
        """Did the matcher cite something the reviewer would have cited?

        Any overlap counts. Citing one good reason is not worse than citing
        every good reason, and a matcher that listed two supporting roles where
        the reviewer named one has not made a mistake.
        """
        return bool(set(self.actual_refs) & set(self.expected_refs))


@dataclass(frozen=True)
class Rate:
    """A ratio that remembers what it was made of."""

    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        """None, not zero, when nothing was measured — an unasked question has
        no answer, and printing 0.00 for one invites reading it as a failure."""
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    def __str__(self) -> str:
        if self.value is None:
            return "  n/a"
        return f"{self.value:5.3f}"


@dataclass(frozen=True)
class VerdictMetrics:
    """One-vs-rest precision and recall for a single verdict."""

    verdict: str
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def predicted(self) -> int:
        return self.true_positive + self.false_positive

    @property
    def expected(self) -> int:
        return self.true_positive + self.false_negative

    @property
    def precision(self) -> Rate:
        """Of the times the matcher said this, how often was it right."""
        return Rate(self.true_positive, self.predicted)

    @property
    def recall(self) -> Rate:
        """Of the times it should have said this, how often did it."""
        return Rate(self.true_positive, self.expected)

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision.value, self.recall.value
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * precision * recall / (precision + recall)


def verdict_metrics(observations: list[Observation], verdict: str) -> VerdictMetrics:
    return VerdictMetrics(
        verdict=verdict,
        true_positive=sum(
            1 for o in observations if o.actual == verdict and o.expected == verdict
        ),
        false_positive=sum(
            1 for o in observations if o.actual == verdict and o.expected != verdict
        ),
        false_negative=sum(
            1 for o in observations if o.actual != verdict and o.expected == verdict
        ),
    )


def confusion(observations: list[Observation]) -> dict[str, dict[str, int]]:
    """expected -> actual -> count. The whole picture, before any summarising.

    Reported in full because every derived metric here is a projection of this
    table, and the projections can each look acceptable while the table shows
    an obvious skew.
    """
    table = {expected: {actual: 0 for actual in VERDICTS} for expected in VERDICTS}
    for observation in observations:
        if observation.actual in table[observation.expected]:
            table[observation.expected][observation.actual] += 1
    return table


@dataclass
class Report:
    """Everything measured, with nothing reduced to one number."""

    algorithm_version: str
    dataset_version: str
    observations: list[Observation] = field(default_factory=list)
    #: Requirements the matcher produced no verdict for at all.
    unjudged: list[str] = field(default_factory=list)
    score_consistent: list[str] = field(default_factory=list)
    score_inconsistent: list[str] = field(default_factory=list)
    reproducible: list[str] = field(default_factory=list)
    irreproducible: list[str] = field(default_factory=list)

    # --- safety first ---------------------------------------------------------

    @property
    def false_matches(self) -> list[Observation]:
        return [o for o in self.observations if o.is_false_match]

    @property
    def false_missing(self) -> list[Observation]:
        return [o for o in self.observations if o.is_false_missing]

    @property
    def false_match_rate(self) -> Rate:
        """Of requirements the profile does NOT satisfy, how many were called
        matched. The denominator is what it should be: a matcher cannot be
        blamed for false matches on requirements that are all genuinely met."""
        eligible = [o for o in self.observations if o.expected != VERDICT_MATCHED]
        return Rate(sum(1 for o in eligible if o.is_false_match), len(eligible))

    # --- per verdict ----------------------------------------------------------

    def for_verdict(self, verdict: str) -> VerdictMetrics:
        return verdict_metrics(self.observations, verdict)

    @property
    def matched(self) -> VerdictMetrics:
        return self.for_verdict(VERDICT_MATCHED)

    @property
    def missing(self) -> VerdictMetrics:
        return self.for_verdict(VERDICT_MISSING)

    @property
    def uncertain(self) -> VerdictMetrics:
        return self.for_verdict(VERDICT_UNCERTAIN)

    @property
    def overcautious(self) -> list[Observation]:
        """Hedged where the profile settles the question."""
        return [
            o
            for o in self.observations
            if o.actual == VERDICT_UNCERTAIN and o.expected != VERDICT_UNCERTAIN
        ]

    @property
    def overconfident(self) -> list[Observation]:
        """Answered where the profile does not settle the question."""
        return [
            o
            for o in self.observations
            if o.expected == VERDICT_UNCERTAIN and o.actual not in (None, VERDICT_UNCERTAIN)
        ]

    # --- axes -----------------------------------------------------------------

    def agreement_for(self, observations: list[Observation]) -> Rate:
        return Rate(sum(1 for o in observations if o.agrees), len(observations))

    @property
    def agreement(self) -> Rate:
        """Raw agreement. Reported, never led with — see the module docstring."""
        return self.agreement_for(self.observations)

    @property
    def required_agreement(self) -> Rate:
        return self.agreement_for([o for o in self.observations if o.is_required])

    @property
    def preferred_agreement(self) -> Rate:
        return self.agreement_for([o for o in self.observations if not o.is_required])

    @property
    def uncontested_agreement(self) -> Rate:
        """Agreement over the judgments reviewers did not argue about.

        The fairer figure for the matcher: a requirement two humans disagreed
        on is not one an algorithm can be expected to get 'right'."""
        return self.agreement_for([o for o in self.observations if not o.contested])

    def by_aspect(self) -> dict[str, Rate]:
        aspects = sorted({o.aspect for o in self.observations})
        return {
            aspect: self.agreement_for([o for o in self.observations if o.aspect == aspect])
            for aspect in aspects
        }

    def by_category(self) -> dict[str, Rate]:
        categories = sorted({o.category for o in self.observations})
        return {
            category: self.agreement_for(
                [o for o in self.observations if o.category == category]
            )
            for category in categories
        }

    # --- evidence -------------------------------------------------------------

    @property
    def evidence_rate(self) -> Rate:
        """Of the verdicts where both agreed and the reviewer named an item,
        how often the matcher pointed at one of those items."""
        checkable = [o for o in self.observations if o.evidence_checkable]
        return Rate(sum(1 for o in checkable if o.evidence_correct), len(checkable))

    @property
    def evidence_errors(self) -> list[Observation]:
        return [
            o for o in self.observations if o.evidence_checkable and not o.evidence_correct
        ]

    @property
    def misclassified(self) -> list[Observation]:
        """Requirements filed under the wrong required/preferred axis."""
        return [o for o in self.observations if not o.classification_correct]

    @property
    def classification_rate(self) -> Rate:
        return Rate(
            sum(1 for o in self.observations if o.classification_correct),
            len(self.observations),
        )

    @property
    def unsupported_matches(self) -> list[Observation]:
        """A MATCHED with nothing cited at all. Should always be empty."""
        return [
            o for o in self.observations if o.actual == VERDICT_MATCHED and not o.actual_refs
        ]

    @property
    def confusion(self) -> dict[str, dict[str, int]]:
        return confusion(self.observations)
