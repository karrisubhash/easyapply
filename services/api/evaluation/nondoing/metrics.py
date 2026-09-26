"""Scoring the non_doing_v1 benchmark. Knows nothing about any matcher.

Takes observations — one authored expectation and one actual value, at
whichever of the five pipeline stages a case exercises — and reduces them to
the sections the report asks for: per-category and per-marker accuracy, false
positives/negatives, tier changes, duration changes, minimal-pair failures,
technology-attribution failures, title-path failures, *-away failures, and a
breakdown by the kind of defect a mismatch represents.

Nothing here knows HOW an actual value was produced — `adapter.py` is the only
module that touches the matching code — which is what lets this file describe
scoring as a problem rather than as a reflection of the implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evaluation.nondoing.schema import DEFECT_NONE, PipelineCase, SentenceCase


@dataclass(frozen=True)
class ActualToken:
    attributed_employment: bool
    tier: str


@dataclass(frozen=True)
class SentenceObservation:
    case: SentenceCase
    actual_non_doing: bool

    @property
    def agrees(self) -> bool:
        return self.actual_non_doing == self.case.expected_non_doing

    @property
    def is_false_positive(self) -> bool:
        """Expected the clause to demonstrate the work; the implementation
        called it non-doing. The costly direction: evidence is discarded."""
        return (not self.case.expected_non_doing) and self.actual_non_doing

    @property
    def is_false_negative(self) -> bool:
        """Expected the clause to disclaim the work; the implementation
        treated it as a demonstration."""
        return self.case.expected_non_doing and not self.actual_non_doing


@dataclass(frozen=True)
class PipelineObservation:
    case: PipelineCase
    actual_tokens: dict[str, ActualToken]
    actual_resolved_subject: str | None
    actual_duration_years: float | None
    actual_duration_uncertain: bool
    actual_verdict: str

    # --- stage 2/3: per-token attribution and tier --------------------------

    def token_mismatches(self) -> list[tuple[str, str, str]]:
        """(token, field, detail) for every token whose attribution or tier
        does not match what was authored."""
        mismatches = []
        for expectation in self.case.expected.tokens:
            actual = self.actual_tokens.get(expectation.token)
            if actual is None:
                mismatches.append((expectation.token, "missing", "token not indexed at all"))
                continue
            if actual.attributed_employment != expectation.attributed_employment:
                mismatches.append(
                    (
                        expectation.token,
                        "attribution",
                        f"expected attributed_employment={expectation.attributed_employment}, "
                        f"got {actual.attributed_employment}",
                    )
                )
            if actual.tier != expectation.tier:
                mismatches.append(
                    (expectation.token, "tier", f"expected {expectation.tier}, got {actual.tier}")
                )
        return mismatches

    @property
    def tokens_agree(self) -> bool:
        return not self.token_mismatches()

    @property
    def resolution_agrees(self) -> bool:
        if self.case.expected.resolved_subject is None:
            return True
        return self.actual_resolved_subject == self.case.expected.resolved_subject

    # --- stage 4: duration ---------------------------------------------------

    @property
    def duration_agrees(self) -> bool:
        expected = self.case.expected.duration
        if not expected.measurable:
            return self.actual_duration_uncertain or self.actual_duration_years is None
        if self.actual_duration_years is None:
            return False
        return abs(self.actual_duration_years - (expected.years or 0.0)) <= 0.3

    # --- stage 5: verdict ------------------------------------------------------

    @property
    def verdict_agrees(self) -> bool:
        return self.actual_verdict == self.case.expected.verdict

    @property
    def agrees(self) -> bool:
        """Whole-case agreement: every stage this case makes a claim about."""
        return (
            self.tokens_agree
            and self.resolution_agrees
            and self.duration_agrees
            and self.verdict_agrees
        )

    @property
    def first_divergent_stage(self) -> str | None:
        """Which of the five stages first disagrees with the author, in
        pipeline order — the stage a fix should target."""
        if not self.tokens_agree:
            return "1-2:non_doing/attribution"
        if not self.resolution_agrees:
            return "2:technology_attribution"
        if not self.duration_agrees:
            return "4:experience_duration"
        if not self.verdict_agrees:
            return "5:requirement_verdict"
        return None


@dataclass
class Rate:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        return None if self.denominator == 0 else self.numerator / self.denominator

    def __str__(self) -> str:
        return "  n/a" if self.value is None else f"{self.value:5.3f}"


def _rate(observations: list) -> Rate:
    return Rate(sum(1 for o in observations if o.agrees), len(observations))


@dataclass
class Report:
    dataset_version: str
    algorithm_version: str
    sentence_observations: list[SentenceObservation] = field(default_factory=list)
    pipeline_observations: list[PipelineObservation] = field(default_factory=list)

    # --- overview --------------------------------------------------------------

    @property
    def all_observations(self) -> list:
        return [*self.sentence_observations, *self.pipeline_observations]

    @property
    def overall_agreement(self) -> Rate:
        return _rate(self.all_observations)

    @property
    def sentence_agreement(self) -> Rate:
        return _rate(self.sentence_observations)

    @property
    def pipeline_agreement(self) -> Rate:
        return _rate(self.pipeline_observations)

    # --- per category / per marker ----------------------------------------------

    def accuracy_by_category(self) -> dict[str, Rate]:
        categories = sorted(
            {o.case.category for o in self.all_observations}
        )
        result = {}
        for category in categories:
            bucket = [o for o in self.all_observations if o.case.category == category]
            result[category] = _rate(bucket)
        return result

    def accuracy_by_marker(self) -> dict[str, Rate]:
        markers = sorted({marker for o in self.all_observations for marker in o.case.markers})
        result = {}
        for marker in markers:
            bucket = [o for o in self.all_observations if marker in o.case.markers]
            result[marker] = _rate(bucket)
        return result

    # --- safety: false positives / negatives (sentence-level is_non_doing) -----

    @property
    def false_positives(self) -> list[SentenceObservation]:
        """Expected False (demonstrates the work), got True (non-doing).
        Evidence silently discarded — the direction that costs a candidate the
        strongest thing their profile could have shown."""
        return [o for o in self.sentence_observations if o.is_false_positive]

    @property
    def false_negatives(self) -> list[SentenceObservation]:
        """Expected True (a disclaimer), got False (counted as done)."""
        return [o for o in self.sentence_observations if o.is_false_negative]

    # --- pipeline-specific sections ---------------------------------------------

    @property
    def tier_changes(self) -> list[tuple[PipelineObservation, str, str, str]]:
        """(observation, token, expected_tier, actual_tier) for every token
        whose tier the author's reading and the pipeline disagree on."""
        changes = []
        for observation in self.pipeline_observations:
            for expectation in observation.case.expected.tokens:
                actual = observation.actual_tokens.get(expectation.token)
                actual_tier = actual.tier if actual else "none"
                if actual_tier != expectation.tier:
                    changes.append((observation, expectation.token, expectation.tier, actual_tier))
        return changes

    @property
    def duration_changes(self) -> list[PipelineObservation]:
        return [o for o in self.pipeline_observations if not o.duration_agrees]

    def minimal_pairs(self) -> dict[str, list]:
        pairs: dict[str, list] = {}
        for observation in self.all_observations:
            pair_id = observation.case.pair_id
            if pair_id:
                pairs.setdefault(pair_id, []).append(observation)
        return pairs

    @property
    def minimal_pair_failures(self) -> list[tuple[str, list]]:
        """Pairs where the contrast the author intended was not preserved:
        either member disagrees, or (worse) both members were forced to the
        same actual value despite opposite expected ones."""
        failures = []
        for pair_id, members in self.minimal_pairs().items():
            if any(not member.agrees for member in members):
                failures.append((pair_id, members))
        return failures

    @property
    def technology_attribution_failures(self) -> list[PipelineObservation]:
        relevant = {"technology_attribution", "composite"}
        return [
            o
            for o in self.pipeline_observations
            if o.case.category in relevant and (not o.tokens_agree or not o.resolution_agrees)
        ]

    @property
    def title_path_failures(self) -> list[PipelineObservation]:
        return [
            o
            for o in self.pipeline_observations
            if o.case.category == "job_title_path" and not o.agrees
        ]

    @property
    def away_construction_failures(self) -> list:
        sentence = [
            o
            for o in self.sentence_observations
            if o.case.category == "away_construction" and not o.agrees
        ]
        pipeline = [
            o
            for o in self.pipeline_observations
            if o.case.category == "away_construction_pipeline" and not o.agrees
        ]
        return [*sentence, *pipeline]

    # --- defect classification ----------------------------------------------------

    def defect_breakdown(self) -> dict[str, dict[str, int]]:
        """defect_class -> {"predicted_and_confirmed", "predicted_but_correct",
        "unpredicted_surprise"} counts.

        Authored defect_class is a PREDICTION made before the run. This checks
        the prediction against what actually happened: a case tagged with a
        defect class that then disagrees confirms the prediction; one that
        agrees means the implementation was more robust than expected; and a
        case tagged "none" that disagrees is a surprise the dataset did not
        anticipate.
        """
        breakdown: dict[str, dict[str, int]] = {}
        for observation in self.all_observations:
            defect_class = observation.case.defect_class
            bucket = breakdown.setdefault(
                defect_class, {"predicted_and_confirmed": 0, "predicted_but_correct": 0}
            )
            if defect_class == DEFECT_NONE:
                continue
            if observation.agrees:
                bucket["predicted_but_correct"] += 1
            else:
                bucket["predicted_and_confirmed"] += 1

        surprises = sum(
            1
            for o in self.all_observations
            if o.case.defect_class == DEFECT_NONE and not o.agrees
        )
        breakdown.setdefault(DEFECT_NONE, {"predicted_and_confirmed": 0, "predicted_but_correct": 0})
        breakdown[DEFECT_NONE]["unpredicted_surprise"] = surprises
        return breakdown

    @property
    def surprises(self) -> list:
        """Controls (defect_class == none) that disagreed anyway — a mismatch
        the dataset's own analysis did not predict, and therefore the most
        important thing in the report to read closely."""
        return [
            o
            for o in self.all_observations
            if o.case.defect_class == DEFECT_NONE and not o.agrees
        ]


__all__ = ["ActualToken", "PipelineObservation", "Rate", "Report", "SentenceObservation"]
