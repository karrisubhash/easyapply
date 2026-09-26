"""The non_doing_v1 benchmark's own vocabulary and shapes. Knows nothing about
any matcher.

This module — and `groundtruth.py` and `metrics.py` beside it — must never
import `app.services.matching`, transitively or otherwise. That boundary is
what makes the expected answers below independent evidence about the vocabulary
rather than a reflection of it, and it is checked in
`tests/test_nondoing_benchmark_independence.py`: structurally (the import
graph), textually (the dataset names no engine internals) and behaviourally
(changing the marker set moves the score and not the ground truth).

`NON_DOING_MARKER_VOCABULARY` below is a DELIBERATE local copy of the words in
`app.services.matching.vocabulary.NON_DOING_MARKERS`, copied by hand rather
than imported, for the same reason `evaluation/benchmark/schema.py` hand-copies
the matching engine's verdict vocabulary: a human authoring an expected answer
writes "true" because that is what the sentence means, not because the engine
happens to spell its marker list that way today. It exists here purely so a
case can be TAGGED with which marker it is about and so the dataset's own
coverage can be checked against a fixed list — never to compute an expected
answer. If a future change to the production list adds or removes a word, nothing
here moves, and the benchmark keeps measuring the version it was authored
against until someone deliberately updates this copy and bumps the dataset
version.

Two kinds of case, because a marker's effect is genuinely different at each
layer of the pipeline the task asked to be measured separately:

    SentenceCase    a single clause, judged directly against what
                    `is_non_doing` is documented to mean: does this clause say
                    a technology was NOT used? The majority of the dataset.

    PipelineCase    a small synthesised profile — one or two paid roles, an
                    optional declared skill, and a requirement — carried
                    through all five stages a real match reaches:

                        1. non-doing classification (per clause)
                        2. technology attribution (which token, which role)
                        3. evidence tier
                        4. experience duration
                        5. requirement verdict

                    Authored so that stage 1-5 expectations are independent of
                    each other: a case may expect stage 1 to fire correctly and
                    stage 5 to still be wrong, because the tier or duration
                    machinery downstream loses information stage 1 got right.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# --- the marker vocabulary, copied by hand ------------------------------------
#
# See the module docstring: this is authoring/validation metadata only. No
# expected answer in cases.json is computed from it.

NON_DOING_MARKER_VOCABULARY: frozenset[str] = frozenset(
    {
        "evaluated", "evaluating", "evaluation", "considered", "considering",
        "explored", "exploring", "investigated", "investigating", "trialled",
        "trialed", "piloted", "prototyped", "researched",
        "cancelled", "canceled", "abandoned", "shelved", "rejected",
        "deprecated", "retired", "retiring", "sunset", "decommissioned",
        "planned", "planning", "proposed", "upcoming", "intend", "intends",
        "migrating away", "moved away", "moving away", "replacing",
        "learning", "learn", "studying", "familiar", "familiarity",
        "exposure", "interested", "curious", "keen",
    }
)

# --- shared vocabulary ---------------------------------------------------------

CONSTRUCTION_POSITIVE = "positive"
CONSTRUCTION_NEGATIVE = "negative"
CONSTRUCTIONS = (CONSTRUCTION_POSITIVE, CONSTRUCTION_NEGATIVE)

SENTENCE_CATEGORIES = (
    "genuine_non_doing",
    "completed_work_with_marker",
    "noun_collision",
    "proper_name_collision",
    "job_title",
    "research_verb",
    "lifecycle_verb",
    "learning_phrase",
    "explicit_non_use",
    "away_construction",
)

PIPELINE_CATEGORIES = (
    "technology_attribution",
    "duration_consequence",
    "evidence_tier_consequence",
    "job_title_path",
    "away_construction_pipeline",
    "composite",
)

#: What KIND of defect a mismatch on this case would represent, authored as a
#: PREDICTION before the benchmark is ever run — see the dataset README. "none"
#: means the case is a control: the author expects the implementation to get
#: it right, and a mismatch here is a surprise rather than a confirmation.
DEFECT_NONE = "none"
DEFECT_TOKENIZER = "tokenizer"
DEFECT_VOCABULARY = "vocabulary"
DEFECT_CLAUSE_CONTEXT = "clause_context"
DEFECT_ATTRIBUTION = "attribution"
DEFECT_EVIDENCE_MODEL = "evidence_model"

DEFECT_CLASSES = (
    DEFECT_NONE,
    DEFECT_TOKENIZER,
    DEFECT_VOCABULARY,
    DEFECT_CLAUSE_CONTEXT,
    DEFECT_ATTRIBUTION,
    DEFECT_EVIDENCE_MODEL,
)

DEFECT_CLASS_DESCRIPTIONS = {
    DEFECT_NONE: "No defect predicted; included as a control.",
    DEFECT_TOKENIZER: (
        "The word-splitting/hyphenation/whitespace mechanics misread a word's "
        "boundary — the same class of bug as the original scikit-learn split."
    ),
    DEFECT_VOCABULARY: (
        "The marker (or MARKER_BEARING_NAMES) list itself is over- or "
        "under-broad for a word sense: the word is correctly found, but its "
        "presence in the list is wrong for this sentence, or a plain-English "
        "non-use statement uses no listed word at all."
    ),
    DEFECT_CLAUSE_CONTEXT: (
        "The marker fires on the right word, but clause-level granularity "
        "extends its effect further than the phrase it grammatically "
        "qualifies — a single unsplit clause mixes a caveat with a resolution."
    ),
    DEFECT_ATTRIBUTION: (
        "Two technologies share one clause; a marker relevant to one wrongly "
        "changes the evidence recorded for the other."
    ),
    DEFECT_EVIDENCE_MODEL: (
        "is_non_doing and the clause split are both correct, but the tier or "
        "duration model built on top of them cannot represent the distinction "
        "a human reviewer would draw — e.g. it cannot report MISSING for a "
        "mention that explicitly states a duration far short of the bar."
    ),
}


class DatasetError(ValueError):
    """A dataset that cannot be trusted to measure anything."""


# --- sentence-level cases ------------------------------------------------------


@dataclass(frozen=True)
class SentenceCase:
    """One clause, and the semantic answer a careful reader gives to "does this
    say the technology was NOT used?" — authored before anything was run."""

    id: str
    category: str
    markers: tuple[str, ...]
    construction: str
    text: str
    expected_non_doing: bool
    rationale: str
    pair_id: str | None = None
    defect_class: str = DEFECT_NONE


# --- pipeline-level cases -------------------------------------------------------


@dataclass(frozen=True)
class PipelineSkill:
    name: str
    years: float | None = None


@dataclass(frozen=True)
class PipelineExperience:
    job_title: str
    start: date
    end: date | None
    description: str


@dataclass(frozen=True)
class PipelineRequirement:
    #: "skill" -> judge_token_requirement; "duration" -> judge_experience.
    kind: str
    name: str
    minimum_years: float | None = None
    quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenExpectation:
    """What a careful reviewer expects the evidence index to say about one
    canonical technology token, named somewhere in the case's experiences."""

    token: str
    #: Whether AT LEAST ONE clause naming this token, read fairly, should be
    #: filed as evidence the work was done (declarations aside).
    attributed_employment: bool
    tier: str  # declared_corroborated | employment_described | declared_only | mentioned | none


@dataclass(frozen=True)
class DurationExpectation:
    #: False when the case's requirement is not a duration requirement, or
    #: when no number can honestly be attached to the evidence at all.
    measurable: bool
    years: float | None = None


@dataclass(frozen=True)
class PipelineExpected:
    tokens: tuple[TokenExpectation, ...]
    resolved_subject: str | None
    duration: DurationExpectation
    verdict: str  # matched | missing | uncertain


@dataclass(frozen=True)
class PipelineCase:
    id: str
    category: str
    markers: tuple[str, ...]
    note: str
    skills: tuple[PipelineSkill, ...]
    experiences: tuple[PipelineExperience, ...]
    requirement: PipelineRequirement
    as_of: date
    expected: PipelineExpected
    pair_id: str | None = None
    defect_class: str = DEFECT_NONE

    def token_expectation(self, token: str) -> TokenExpectation | None:
        return next((item for item in self.expected.tokens if item.token == token), None)

    def note_ok(self) -> bool:
        return bool(self.note) and len(self.note.strip()) >= 15


# --- the dataset -----------------------------------------------------------------


@dataclass(frozen=True)
class ReviewMetadata:
    created: str
    reviewer: str
    methodology: str
    independence_statement: str


@dataclass(frozen=True)
class Dataset:
    version: str
    review: ReviewMetadata
    sentence_cases: tuple[SentenceCase, ...]
    pipeline_cases: tuple[PipelineCase, ...]

    @property
    def case_count(self) -> int:
        return len(self.sentence_cases) + len(self.pipeline_cases)


VERDICTS = ("matched", "missing", "uncertain")
TIERS = ("declared_corroborated", "employment_described", "declared_only", "mentioned", "none")
REQUIREMENT_KINDS = ("skill", "duration")


# --- validation ------------------------------------------------------------------


def validate(dataset: Dataset) -> None:
    problems: list[str] = []

    seen_ids: set[str] = set()
    pairs: dict[str, list[Any]] = {}

    for case in dataset.sentence_cases:
        where = case.id
        if case.id in seen_ids:
            problems.append(f"{where}: duplicate case id")
        seen_ids.add(case.id)

        if case.category not in SENTENCE_CATEGORIES:
            problems.append(f"{where}: unknown category {case.category!r}")
        if case.construction not in CONSTRUCTIONS:
            problems.append(f"{where}: unknown construction {case.construction!r}")
        if case.defect_class not in DEFECT_CLASSES:
            problems.append(f"{where}: unknown defect_class {case.defect_class!r}")
        if not case.markers:
            problems.append(f"{where}: names no marker(s) it is about")
        for marker in case.markers:
            if marker not in NON_DOING_MARKER_VOCABULARY and marker != "none":
                problems.append(f"{where}: tags marker {marker!r}, which is not in the vocabulary")
        if not case.text.strip():
            problems.append(f"{where}: empty text")
        if not case.rationale or len(case.rationale.strip()) < 15:
            problems.append(f"{where}: rationale is missing or too short to review")
        if case.pair_id:
            pairs.setdefault(case.pair_id, []).append(case.expected_non_doing)

    for case in dataset.pipeline_cases:
        where = case.id
        if case.id in seen_ids:
            problems.append(f"{where}: duplicate case id")
        seen_ids.add(case.id)

        if case.category not in PIPELINE_CATEGORIES:
            problems.append(f"{where}: unknown category {case.category!r}")
        if case.defect_class not in DEFECT_CLASSES:
            problems.append(f"{where}: unknown defect_class {case.defect_class!r}")
        if not case.experiences:
            problems.append(f"{where}: has no experiences to index")
        if not case.note_ok():
            problems.append(f"{where}: note is missing or too short to review")
        if case.requirement.kind not in REQUIREMENT_KINDS:
            problems.append(f"{where}: unknown requirement kind {case.requirement.kind!r}")
        if case.requirement.kind == "duration" and case.requirement.minimum_years is None:
            problems.append(f"{where}: duration requirement has no minimum_years")
        if case.expected.verdict not in VERDICTS:
            problems.append(f"{where}: unknown expected verdict {case.expected.verdict!r}")
        if not case.expected.tokens:
            problems.append(f"{where}: expects no tokens at all")
        for token_expectation in case.expected.tokens:
            if token_expectation.tier not in TIERS:
                problems.append(
                    f"{where}: unknown expected tier {token_expectation.tier!r} for "
                    f"{token_expectation.token!r}"
                )
        if case.expected.duration.measurable and case.expected.duration.years is None:
            problems.append(f"{where}: duration marked measurable with no years given")
        if case.pair_id:
            pairs.setdefault(case.pair_id, []).append(case.expected.verdict)

    for pair_id, values in pairs.items():
        if len(values) < 2:
            problems.append(f"pair {pair_id!r}: has only one member")
        elif len(set(map(str, values))) < 2:
            problems.append(
                f"pair {pair_id!r}: every member expects the same outcome — not a contrast"
            )

    if problems:
        raise DatasetError(
            f"{len(problems)} problem(s) in dataset {dataset.version}:\n  "
            + "\n  ".join(problems[:60])
        )


def as_json_safe(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [as_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: as_json_safe(item) for key, item in value.items()}
    return value


__all__ = [
    "CONSTRUCTIONS",
    "CONSTRUCTION_NEGATIVE",
    "CONSTRUCTION_POSITIVE",
    "Dataset",
    "DatasetError",
    "DEFECT_ATTRIBUTION",
    "DEFECT_CLASSES",
    "DEFECT_CLASS_DESCRIPTIONS",
    "DEFECT_CLAUSE_CONTEXT",
    "DEFECT_EVIDENCE_MODEL",
    "DEFECT_NONE",
    "DEFECT_TOKENIZER",
    "DEFECT_VOCABULARY",
    "DurationExpectation",
    "NON_DOING_MARKER_VOCABULARY",
    "PIPELINE_CATEGORIES",
    "PipelineCase",
    "PipelineExperience",
    "PipelineExpected",
    "PipelineRequirement",
    "PipelineSkill",
    "REQUIREMENT_KINDS",
    "ReviewMetadata",
    "SENTENCE_CATEGORIES",
    "SentenceCase",
    "TIERS",
    "TokenExpectation",
    "VERDICTS",
    "as_json_safe",
    "validate",
]
