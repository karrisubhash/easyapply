"""Walking a job's requirements past a profile version, one rule at a time.

The engine owns the traversal and nothing else: which JobCandidate categories
are evaluated, in what order, and with what requirement_index — the judgments
themselves belong to `rules.py` and the score to `scoring.py`. Keeping those
apart is what makes a rule change reviewable in isolation.

It is a pure function of (JobCandidate, ProfileVersion, as_of). No database, no
clock, no configuration: the same three inputs produce the same verdicts on any
machine, which is what makes a stored `MatchResult` reproducible rather than
merely recorded. `as_of` is a parameter for exactly this reason — a matcher
that read `date.today()` internally would be unreproducible by construction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.models.match import (
    ALGORITHM_V5_1,
    CATEGORY_SKILL,
    CATEGORY_TECHNOLOGY,
)
from app.models.profile_version import ProfileVersion
from app.schemas.job_candidate import JobCandidate
from app.services.matching.geography import is_location_requirement
from app.services.matching.profile_index import ProfileIndex
from app.schemas.job_candidate import JobProvenance
from app.services.matching.rules import (
    RequirementJudgment,
    judge_certification,
    judge_location,
    judge_education,
    judge_experience,
    judge_qualification,
    judge_token_requirement,
)
from app.services.matching.scoring import Score, score

logger = logging.getLogger(__name__)

#: The rules in this package, as a version. Bumped when a change could alter a
#: verdict; old results keep their own value and stay interpretable.
#:
#: v1 -> v2: experience subjects are resolved against a technology vocabulary
#: rather than against whatever survives a stopword filter, and evidence is
#: graded into four tiers rather than split in two.
#:
#: v2 -> v3: a work-action inflection table, so a requirement about "leading a
#: team" is answered by a role that says "Lead four engineers". One verdict in
#: 143 moved on the real-data benchmark — a small change, and still a change,
#: which is the whole test. Because the algorithm version is part of the
#: idempotency key, a job already matched under v2 is re-evaluated under v3
#: rather than answered from the stored v2 row; without the bump a user would
#: keep being shown the verdict this release exists to correct.
#:
#: v3 -> v4: location requirements are judged rather than answered UNCERTAIN
#: by default. A requirement naming a city or country the vocabulary knows is
#: now MATCHED when the candidate's current employment puts them in that city,
#: or in that country. Several stored v3 results would change, so the version
#: must move: without it the idempotency key would hand back the old blanket
#: UNCERTAIN for a job already matched, and the location verdicts this release
#: adds would never be seen.
#:
#: v4 -> v5: the non_doing_v1 benchmark showed a marker's verdict was computed
#: once per whole clause and broadcast to every technology it named, so "X,
#: while building Y" graded both the same way. profile_index.py now divides a
#: clause into scope-bounded spans (app/services/matching/span_split.py) and
#: grades each independently, with three further, independent additions: a
#: trailing relative clause is reattached to the nearest technology token it
#: actually modifies; an explicit plain-English denial of use ("no Rust code
#: has shipped yet") is recognised even when it names no NON_DOING_MARKERS
#: word (negation.py); and a job title is graded by its own rule
#: (evidence.demonstrates_title) rather than the same path as prose, since a
#: title's grammar does not carry a marker's caveat meaning. 34 of 137
#: non_doing_v1 judgments moved, all in the direction of correctly
#: demonstrated experience previously read as a caveat — see
#: docs/matching-v5-results.md. NON_DOING_MARKERS, is_non_doing(),
#: demonstrates(), and resolve_technology_subject() are byte-for-byte
#: unchanged, and the real-data benchmark (matching_real_v1) is unaffected:
#: none of its profiles exercise the shapes this release targets. Several
#: stored v4 results for profiles that DO contain these shapes would change
#: under v5, so the version moves for the same reason every prior bump did:
#: without it the idempotency key would hand back the old, weaker verdict for
#: a job already matched.
#:
#: v5 -> v5_1: corrections, not a new capability — each one a verdict an
#: offline probe showed the profile does not support. Almost all move in the
#: conservative direction; the one exception is (5)'s "any one of" reading,
#: which turns a wrong MISSING into MATCHED where the posting offered
#: alternatives. (1) negation.py recognises the auxiliary and span-initial
#: denials the v5 patterns missed ("Did not use Django", "Didn't use", "Haven't
#: used", "Unfamiliar with", "Lacks … experience", "was never adopted"); every
#: one of them had been graded as paid work and counted toward duration. They
#: yield to a subordinating word before them, so a clause about something else
#: ("services that do not use sticky sessions") is not read as a denial. (2) A
#: field of work ("5+ years of software development") is confirmed only by an
#: affirmed clause or a title naming a phrase or word that names the discipline
#: on its own — never one word that needs a qualifier ("development",
#: "engineering", "end") — while total employment is still what is counted, a
#: trade-off measured against matching_real_v1 (docs/matching.md, Known
#: limits). IT is answered by roles saying IT or "information technology".
#: (3) A role's end date after `as_of` is clamped to it. (4) A degree whose end
#: or start date is still ahead is UNCERTAIN ("in progress"), not held. (5) A
#: requirement naming several technologies is judged on all of them ("and") or
#: any one ("or", "and/or", "(or X)"), not on the most specific alone; a comma
#: list is alternatives only when a WORD closes it. (6) One- and two-letter
#: technology subjects must be written as capitalised words of their own, so
#: "R&D" and "C-level" no longer resolve `r` and `c`. (7) Stated
#: years that exceed a short attributed total make the verdict UNCERTAIN rather
#: than MISSING. (8) Evidence under a non-MATCHED verdict is no longer stored
#: as qualifying. Stored v5 results for affected profiles would change, so the
#: version moves for the reason every prior bump did.
ALGORITHM_VERSION = ALGORITHM_V5_1


def _quotes(item) -> tuple[str, ...]:
    """The posting own verified words for one requirement.

    A structurer extracts "Kubernetes" from the line "Kubernetes in
    production, not just in a course", and the extraction loses the bar the
    posting was setting. The provenance quote still has it, so the rules read
    it to decide what the requirement demands of the evidence.

    Only VERIFIED quotes are used. An unverified citation is one the server
    could not find in the description — possibly fabricated by a provider — and
    letting it raise or lower a requirement bar would hand a model influence
    over a verdict that the deterministic engine exists to deny it.
    """
    return tuple(
        entry.quote
        for entry in getattr(item, "provenance", ())
        if isinstance(entry, JobProvenance) and entry.verified is True and entry.quote
    )


@dataclass(frozen=True)
class JobPosting:
    """What the matcher needs to know about the POSTING, beyond its requirements.

    `JobCandidate` carries what the job asks for; this carries the two facts
    about the job itself that a location requirement can need — the location
    the posting states, and whether the role is remote. A requirement saying
    "Based in our Berlin office" names a city; one saying "Based in our head
    office" does not, and the posting's own location field is the only thing
    that does.

    Passed as plain values rather than as the `Job` row, so `evaluate` stays a
    function of data it can be handed in a test. Reproducibility is unaffected
    for the reason `job_requirements_id` is safe to pin: `ingest_job` returns
    an existing row UNCHANGED — "the first-seen version wins" — so these two
    fields are write-once for the life of the job, exactly like the
    requirements row a result already pins.
    """

    location_raw: str | None = None
    workplace_type: str | None = None


@dataclass(frozen=True)
class MatchEvaluation:
    """Everything the persistence layer needs, and nothing it does not."""

    judgments: list[RequirementJudgment]
    score: Score
    algorithm_version: str
    as_of: date


def evaluate(
    candidate: JobCandidate,
    version: ProfileVersion,
    *,
    as_of: date,
    posting: JobPosting | None = None,
) -> MatchEvaluation:
    """Judge every checkable requirement in a structured job posting.

    `posting` carries the job's own location and workplace type, which some
    location requirements need and which `JobCandidate` does not hold. Optional
    so that a caller with only a requirement set still works; a location
    requirement then falls back to whatever place its own words name, and
    answers UNCERTAIN when they name none.

    Responsibilities are absent by design — they describe the role once hired,
    not a bar the candidate clears, which is why `JobCandidate` gives them no
    required/preferred axis in the first place. Everything else in the schema
    is evaluated, including free-text qualifications, which are recorded as
    UNCERTAIN rather than dropped so they cannot quietly shrink the
    denominator.
    """
    profile = ProfileIndex(version)
    judgments: list[RequirementJudgment] = []

    for index, item in enumerate(candidate.required_skills):
        judgments.append(
            judge_token_requirement(
                name=item.name,
                category=CATEGORY_SKILL,
                index=index,
                is_required=True,
                profile=profile,
                quotes=_quotes(item),
            )
        )
    offset = len(candidate.required_skills)
    for index, item in enumerate(candidate.preferred_skills):
        judgments.append(
            judge_token_requirement(
                name=item.name,
                category=CATEGORY_SKILL,
                # Continues the skill sequence rather than restarting, so
                # (category, requirement_index) stays unique per result — the
                # table's own uniqueness rule.
                index=offset + index,
                is_required=False,
                profile=profile,
                quotes=_quotes(item),
            )
        )

    for index, item in enumerate(candidate.technologies):
        judgments.append(
            judge_token_requirement(
                name=item.name,
                category=CATEGORY_TECHNOLOGY,
                index=index,
                is_required=item.is_required,
                profile=profile,
                quotes=_quotes(item),
            )
        )

    for index, item in enumerate(candidate.required_experience):
        judgments.append(
            judge_experience(
                description=item.description,
                minimum_years=item.minimum_years,
                index=index,
                is_required=True,
                profile=profile,
                as_of=as_of,
                quotes=_quotes(item),
            )
        )
    offset = len(candidate.required_experience)
    for index, item in enumerate(candidate.preferred_experience):
        judgments.append(
            judge_experience(
                description=item.description,
                minimum_years=item.minimum_years,
                index=offset + index,
                is_required=False,
                profile=profile,
                as_of=as_of,
                quotes=_quotes(item),
            )
        )

    for index, item in enumerate(candidate.education):
        judgments.append(
            judge_education(
                description=item.description,
                degree_level_required=item.degree_level,
                field_required=item.field_of_study,
                index=index,
                is_required=item.is_required,
                profile=profile,
                as_of=as_of,
            )
        )

    for index, item in enumerate(candidate.certifications):
        judgments.append(
            judge_certification(
                name=item.name,
                index=index,
                is_required=item.is_required,
                profile=profile,
                as_of=as_of,
            )
        )

    for index, item in enumerate(candidate.qualifications):
        if is_location_requirement(item.description):
            # Milestone A files location statements into the qualifications
            # catch-all, where they are indistinguishable from work
            # authorisation by shape alone. They are told apart here by the
            # same lead-in phrasings that filed them, held locally — see
            # geography.LOCATION_LEAD.
            judgments.append(
                judge_location(
                    description=item.description,
                    index=index,
                    is_required=item.is_required,
                    profile=profile,
                    posting=posting,
                )
            )
            continue
        judgments.append(
            judge_qualification(
                description=item.description,
                index=index,
                is_required=item.is_required,
            )
        )

    totals = score(judgments)

    logger.info(
        "match.evaluated",
        extra={
            "algorithm_version": ALGORITHM_VERSION,
            "profile_version_id": version.id,
            # Counts only — never a requirement's text, never profile content.
            "requirements": len(judgments),
            "required_matched": totals.required.matched,
            "required_missing": totals.required.missing,
            "required_uncertain": totals.required.uncertain,
        },
    )

    return MatchEvaluation(
        judgments=judgments,
        score=totals,
        algorithm_version=ALGORITHM_VERSION,
        as_of=as_of,
    )
