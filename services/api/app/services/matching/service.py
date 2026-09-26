"""The matching application service: resolve, authorize, evaluate, persist.

Everything that makes a match trustworthy as a RECORD rather than as a
calculation lives here — which profile version is allowed to be matched, which
requirements row is pinned, what happens when the same match is asked for
twice, and who is allowed to read it afterwards. The judgment itself is
`engine.evaluate`, which this module calls exactly once and never second-
guesses.

Three rules this layer enforces that nothing below it can:

  * **A draft is never matched.** §2's requirement, and the reason the stored
    result stays meaningful: a confirmed version is immutable, so a later edit
    creates a new version rather than changing what this result was computed
    from. Matching a draft would produce a result whose inputs could change
    underneath it.
  * **Ownership is resolved from the profile side, every time.** A
    `profile_version_id` is a global primary key, so it cannot be trusted as
    evidence of anything; it is loaded through a join to `profiles.user_id`
    and a version belonging to someone else is a 404, not a 403 — the same
    posture the job and profile services already take.
  * **The requirements row is pinned, not the job.** `job_requirements` is
    written once per completed processing run and never updated, so pinning
    its id freezes the exact requirement text the verdicts were reached
    against. Reprocessing a job produces a new requirements row and therefore
    a genuinely new match, instead of quietly reinterpreting an old one.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Job,
    JobProcessingRun,
    JobRequirements,
    MatchEvidence,
    MatchRequirementResult,
    MatchResult,
    Profile,
    Project,
    ProfileVersion,
)
from app.models.match import STATUS_MATCHED
from app.models.profile_version import STATUS_CONFIRMED
from app.schemas.job import job_requirements_payload
from app.services.errors import ConflictError, NotFoundError
from app.services.job import service as job_service
from app.services.matching.engine import ALGORITHM_VERSION, JobPosting, evaluate
from app.services.matching.evidence import satisfies, why_not, why_qualifies

logger = logging.getLogger(__name__)

#: The note on evidence whose kind met the posting's bar under a verdict that is
#: not MATCHED — the verdict fell short for another reason (duration, place,
#: level), and the explanation beside it says which.
_SHORT_FOR_ANOTHER_REASON = (
    "Found, but not enough on its own for this requirement; the explanation above says why."
)


# --- resolving the two sides ---------------------------------------------------


def _load_profile_version(db: Session, *, user_id: int, profile_version_id: int) -> ProfileVersion:
    """A version by its global id, scoped to its owner.

    The join to `profiles` is the authorization check. Without it, a
    primary key from anywhere in the table would be accepted — the classic
    insecure-direct-object-reference, and the reason §19 says not to rely on
    the frontend for this.
    """
    version = db.scalar(
        select(ProfileVersion)
        .join(Profile, Profile.id == ProfileVersion.profile_id)
        .where(ProfileVersion.id == profile_version_id, Profile.user_id == user_id)
        .options(*_snapshot_loaders())
    )
    if version is None:
        raise NotFoundError("That profile version does not exist.")
    return version


def _load_current_version(db: Session, *, user_id: int) -> ProfileVersion:
    """The user's current confirmed version.

    Offered so the common case does not have to name an id, and safe to offer
    because "current" is neither arbitrary nor mutable: it is by definition a
    confirmed version, and confirmed versions are immutable. A draft could
    never be selected here — the database forbids `is_current` on one.
    """
    version = db.scalar(
        select(ProfileVersion)
        .join(Profile, Profile.id == ProfileVersion.profile_id)
        .where(Profile.user_id == user_id, ProfileVersion.is_current.is_(True))
        .options(*_snapshot_loaders())
    )
    if version is None:
        raise ConflictError(
            "This profile has no confirmed version yet. Confirm a version before matching."
        )
    return version


def _snapshot_loaders():
    """Load the whole snapshot in one go: matching reads every child table."""
    return (
        selectinload(ProfileVersion.experiences),
        selectinload(ProfileVersion.education),
        selectinload(ProfileVersion.skills),
        selectinload(ProfileVersion.projects).selectinload(Project.technologies),
        selectinload(ProfileVersion.certifications),
    )


def _load_requirements(db: Session, *, job: Job) -> JobRequirements:
    """The most recent completed structuring of this job.

    `job_requirements` rows only exist for runs that completed, so the newest
    row is the newest successful reading. A job still processing, or one whose
    only attempt failed, has nothing to match against — and that is a conflict
    to report, not an empty match to fabricate.
    """
    requirements = db.scalar(
        select(JobRequirements)
        .join(JobProcessingRun, JobProcessingRun.id == JobRequirements.job_processing_run_id)
        .where(JobProcessingRun.job_id == job.id)
        .order_by(JobProcessingRun.attempt.desc())
        .limit(1)
    )
    if requirements is None:
        raise ConflictError(
            "This job has not been structured yet, so there are no requirements to match against."
        )
    return requirements


# --- creating -------------------------------------------------------------------


def create_match(
    db: Session,
    *,
    user_id: int,
    job_id: int,
    profile_version_id: int | None = None,
    as_of: date | None = None,
) -> tuple[MatchResult, bool]:
    """Evaluate a profile version against a job, or return the existing result.

    Returns `(match, created)`. `created` is False when this exact evaluation
    already existed — same version, same requirements row, same algorithm — in
    which case the stored result is returned untouched rather than recomputed.
    That is what makes the endpoint safe to call repeatedly, and it is also why
    `as_of_date` is stored rather than used as part of the key: re-requesting
    yesterday's match returns yesterday's answer, complete with the date it was
    measured against, instead of silently producing a different one because a
    day passed.
    """
    job = job_service.get_job(db, user_id=user_id, job_id=job_id)
    requirements = _load_requirements(db, job=job)

    if profile_version_id is None:
        version = _load_current_version(db, user_id=user_id)
    else:
        version = _load_profile_version(db, user_id=user_id, profile_version_id=profile_version_id)

    if version.status != STATUS_CONFIRMED:
        raise ConflictError(
            "Only a confirmed profile version can be matched. A draft can still change, "
            "which would make the result meaningless."
        )

    existing = _find_existing(
        db, profile_version_id=version.id, job_requirements_id=requirements.id
    )
    if existing is not None:
        return existing, False

    evaluation = evaluate(
        job_requirements_payload(requirements.payload),
        version,
        as_of=as_of or date.today(),
        # The job's own location, for the location rules. Safe to read at match
        # time for the same reason the requirements row is: `ingest_job`
        # returns an existing row UNCHANGED, so these fields are write-once and
        # a stored result stays recomputable from the ids it already pins.
        posting=JobPosting(
            location_raw=job.location_raw, workplace_type=job.workplace_type
        ),
    )

    match = MatchResult(
        user_id=user_id,
        profile_version_id=version.id,
        job_id=job.id,
        job_requirements_id=requirements.id,
        algorithm_version=evaluation.algorithm_version,
        as_of_date=evaluation.as_of,
        required_total=evaluation.score.required.total,
        required_matched=evaluation.score.required.matched,
        required_missing=evaluation.score.required.missing,
        required_uncertain=evaluation.score.required.uncertain,
        preferred_total=evaluation.score.preferred.total,
        preferred_matched=evaluation.score.preferred.matched,
        preferred_missing=evaluation.score.preferred.missing,
        preferred_uncertain=evaluation.score.preferred.uncertain,
        required_coverage=evaluation.score.required.coverage,
        preferred_coverage=evaluation.score.preferred.coverage,
        overall_score=evaluation.score.overall,
    )
    db.add(match)

    for judgment in evaluation.judgments:
        row = MatchRequirementResult(
            match_result=match,
            category=judgment.category,
            requirement_index=judgment.requirement_index,
            # Cut to their columns (String(255)/String(128)/String(255)). A
            # requirement's label is its posting text, up to 500 characters for
            # an experience line, and a certification line of 150 characters
            # became a 150-character key: the INSERT raised DataError and the
            # job could never be matched at all.
            requirement_label=judgment.requirement_label[:255],
            requirement_key=judgment.requirement_key[:128] if judgment.requirement_key else None,
            is_required=judgment.is_required,
            status=judgment.status,
            confidence=round(judgment.confidence, 2),
            explanation=judgment.explanation[:500],
            matched_value=judgment.matched_value[:255] if judgment.matched_value else None,
            evidence_standard=judgment.evidence_standard,
        )
        db.add(row)
        for item in judgment.evidence:
            # Whether this item met the bar, and why — computed once here so
            # the stored row can explain itself without re-running a rule.
            # An UNCERTAIN keeps the evidence that fell short rather than
            # discarding it: "we found this and it was not enough" is the most
            # useful thing the result can say about a gap.
            #
            # matching_v5_1: evidence is only "qualifying" under a MATCHED
            # verdict. `satisfies` answers whether the evidence's KIND meets the
            # posting's bar, and a verdict can fall short for a reason that is
            # not about kind at all — a role in Toronto against a Berlin
            # requirement, 1.7 years against 3, a bachelor's against a master's.
            # Those used to be stored as qualifying, with a note ("A paid role's
            # description demonstrates this directly") contradicting the
            # UNCERTAIN or MISSING beside it, and shown to the user as support.
            matched = judgment.status == STATUS_MATCHED
            kind_meets_bar = satisfies(item.tier, judgment.evidence_standard) if item.tier else True
            qualifies = matched and kind_meets_bar
            if not item.tier:
                note = None
            elif qualifies:
                note = why_qualifies(item.tier, judgment.evidence_standard)
            elif kind_meets_bar:
                note = _SHORT_FOR_ANOTHER_REASON
            else:
                note = why_not(item.tier, judgment.evidence_standard)
            db.add(
                MatchEvidence(
                    requirement_result=row,
                    source_type=item.source_type,
                    source_id=item.source_id,
                    label=item.label[:255],
                    detail=item.detail[:500] if item.detail else None,
                    tier=item.tier,
                    qualifies=qualifies,
                    note=note[:300] if note else None,
                    quote=item.quote[:300] if item.quote else None,
                )
            )

    # The same race `job.service.ingest_job` guards: two requests for the same
    # evaluation landing between the SELECT above and this INSERT. The unique
    # constraint decides, and the loser reads the winner's row rather than
    # raising — a duplicate match is not an error, it is the same answer.
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.rollback()
        winner = _find_existing(
            db, profile_version_id=version.id, job_requirements_id=requirements.id
        )
        if winner is None:
            raise
        return winner, False

    db.commit()
    db.refresh(match)

    logger.info(
        "match.created",
        extra={
            "match_id": match.id,
            "job_id": job.id,
            "profile_version_id": version.id,
            "algorithm_version": match.algorithm_version,
            "required_matched": match.required_matched,
            "required_missing": match.required_missing,
        },
    )
    return match, True


def _find_existing(
    db: Session, *, profile_version_id: int, job_requirements_id: int
) -> MatchResult | None:
    return db.scalar(
        select(MatchResult)
        .where(
            MatchResult.profile_version_id == profile_version_id,
            MatchResult.job_requirements_id == job_requirements_id,
            MatchResult.algorithm_version == ALGORITHM_VERSION,
        )
        .options(*_result_loaders())
    )


# --- reading --------------------------------------------------------------------


def _result_loaders():
    """What every read of a match needs, in one round trip.

    The profile version is here for its `version_number` alone — see
    `MatchResult.profile_version_number`. Eager, because the listing endpoint
    serialises a page of matches and a lazy load there is one SELECT per row.
    """
    return (
        selectinload(MatchResult.requirement_results).selectinload(
            MatchRequirementResult.evidence
        ),
        selectinload(MatchResult.profile_version),
    )


def get_match(db: Session, *, user_id: int, match_id: int) -> MatchResult:
    """One match, scoped to its owner. A stranger's id is a 404."""
    match = db.scalar(
        select(MatchResult)
        .where(MatchResult.id == match_id, MatchResult.user_id == user_id)
        .options(*_result_loaders())
    )
    if match is None:
        raise NotFoundError("That match does not exist.")
    return match


def list_matches_for_job(db: Session, *, user_id: int, job_id: int) -> list[MatchResult]:
    """Every match this user has for this job, newest first.

    Goes through the job service so that a job belonging to someone else is a
    404 before any match row is read, rather than an empty list that quietly
    confirms the job exists.
    """
    job = job_service.get_job(db, user_id=user_id, job_id=job_id)
    return list(
        db.scalars(
            select(MatchResult)
            .where(MatchResult.user_id == user_id, MatchResult.job_id == job.id)
            .order_by(MatchResult.created_at.desc(), MatchResult.id.desc())
            .options(*_result_loaders())
        )
    )
