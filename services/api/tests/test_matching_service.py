"""The application service: what makes a match a RECORD rather than a sum.

Four concerns, none of which the engine can enforce for itself:

  * which profile version is allowed to be matched (§2 — never a draft)
  * which requirements row is pinned (§15 — reprocessing makes a new match,
    not a quietly reinterpreted old one)
  * what happens when the same match is asked for twice (§18)
  * who is allowed to read the result afterwards (§19)

Real rows against a real migrated database, because the idempotency story is a
unique constraint and the immutability story is a trigger — neither of which
exists in the ORM metadata.
"""

from datetime import date

import pytest

from app.models import MatchEvidence, MatchRequirementResult, MatchResult
from app.models.match import STATUS_MATCHED, STATUS_MISSING, STATUS_UNCERTAIN
from app.services.errors import ConflictError, NotFoundError
from app.services.matching.engine import ALGORITHM_VERSION
from app.services.matching.service import (
    create_match,
    get_match,
    list_matches_for_job,
)
from tests.matching_fixtures import (
    AS_OF,
    a_candidate,
    a_structured_job,
    another_user,
    persisted_profile_version,
)


@pytest.fixture
def version(db, user):
    return persisted_profile_version(
        db,
        user,
        skills=[("Python", 6.0), ("PostgreSQL", None)],
        experiences=[
            {
                "job_title": "Backend Developer",
                "start": date(2019, 1, 1),
                "end": date(2024, 1, 1),
                "description": "Django REST API development",
            }
        ],
        education=[{"degree": "BSc Computer Science", "field_of_study": "Computer Science"}],
        certifications=["AWS Certified Developer"],
        projects=[("EasyApply", ["Redis"])],
    )


@pytest.fixture
def job(db, user):
    return a_structured_job(
        db,
        user,
        candidate=a_candidate(
            required_skills=["Python", "Rust"],
            preferred_skills=["Kubernetes"],
            technologies=[("Redis", False)],
            required_experience=[("3+ years of Python", 3.0)],
            education=[("Bachelor's degree in Computer Science", "bachelor", None, True)],
            certifications=[("AWS Certified Developer", True)],
        ),
    )


# =============================================================================
# Creating
# =============================================================================


def test_a_match_persists_results_and_evidence_as_rows(db, user, version, job):
    """§16: normalized, not one JSON blob. The queries this data exists for —
    "which required skills are missing across all my matches" — are set-shaped."""
    job_row, requirements = job
    match, created = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    assert created is True
    assert match.id is not None
    assert db.query(MatchRequirementResult).filter_by(match_result_id=match.id).count() == len(
        match.requirement_results
    )
    assert db.query(MatchEvidence).join(MatchRequirementResult).filter(
        MatchRequirementResult.match_result_id == match.id
    ).count() > 0


def test_the_stored_counts_match_the_stored_verdicts(db, user, version, job):
    job_row, _ = job
    match, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    required = [row for row in match.requirement_results if row.is_required]
    assert match.required_total == len(required)
    assert match.required_matched == sum(1 for row in required if row.status == STATUS_MATCHED)
    assert match.required_missing == sum(1 for row in required if row.status == STATUS_MISSING)
    assert match.required_uncertain == sum(
        1 for row in required if row.status == STATUS_UNCERTAIN
    )


def test_every_matched_row_that_was_stored_kept_its_evidence(db, user, version, job):
    """The invariant the model docstring says cannot be a CHECK because it
    spans two tables. It is enforced by the engine and asserted here."""
    job_row, _ = job
    match, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    matched = [row for row in match.requirement_results if row.status == STATUS_MATCHED]
    assert matched, "the fixture is meant to produce at least one match"
    for row in matched:
        assert row.evidence, row.requirement_label


def test_evidence_points_at_real_profile_rows(db, user, version, job):
    """§5: no fictional evidence. Every source_id names a row in the version
    that was matched."""
    job_row, _ = job
    match, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    real = {
        "skill": {row.id for row in version.skills},
        "experience": {row.id for row in version.experiences},
        "education": {row.id for row in version.education},
        "certification": {row.id for row in version.certifications},
        "project": {row.id for row in version.projects},
        "project_technology": {row.id for row in version.projects},
    }
    for result in match.requirement_results:
        for item in result.evidence:
            if item.source_id is not None:
                assert item.source_id in real[item.source_type], item.label


def test_a_missing_verdict_says_so_in_words(db, user, job):
    """§5's closing requirement: MISSING must state that no qualifying
    evidence was found, not merely be empty."""
    job_row, _ = job
    empty = persisted_profile_version(db, user)
    match, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=empty.id, as_of=AS_OF
    )

    misses = [row for row in match.requirement_results if row.status == STATUS_MISSING]
    assert misses
    for row in misses:
        assert row.evidence == []
        assert "no qualifying evidence was found" in row.explanation.lower()


def test_the_algorithm_version_and_as_of_date_are_stored(db, user, version, job):
    """§14, mandatory. §15, so "5+ years" can be re-measured against the same
    day it was first measured against."""
    job_row, _ = job
    match, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    assert match.algorithm_version == ALGORITHM_VERSION == "matching_v5_1"
    assert match.as_of_date == AS_OF


def test_the_requirements_row_is_pinned_not_the_job(db, user, version, job):
    job_row, requirements = job
    match, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    assert match.job_requirements_id == requirements.id
    assert match.job_id == job_row.id


def test_the_current_confirmed_version_is_used_when_none_is_named(db, user, version, job):
    job_row, _ = job
    match, _ = create_match(db, user_id=user.id, job_id=job_row.id, as_of=AS_OF)

    assert match.profile_version_id == version.id


# =============================================================================
# What may be matched
# =============================================================================


def test_a_draft_version_is_refused(db, user, job):
    """§2. A draft can still change, which would leave the stored result
    describing inputs that no longer exist."""
    job_row, _ = job
    draft = persisted_profile_version(
        db, user, skills=[("Python", None)], confirmed=False, is_current=False
    )

    with pytest.raises(ConflictError, match="confirmed"):
        create_match(
            db, user_id=user.id, job_id=job_row.id, profile_version_id=draft.id, as_of=AS_OF
        )


def test_a_profile_with_no_confirmed_version_cannot_match(db, user, job):
    job_row, _ = job
    persisted_profile_version(db, user, confirmed=False, is_current=False)

    with pytest.raises(ConflictError, match="Confirm a version"):
        create_match(db, user_id=user.id, job_id=job_row.id, as_of=AS_OF)


def test_an_unstructured_job_cannot_be_matched(db, user, version):
    """Nothing to match against is a conflict to report, not an empty match
    to fabricate."""
    from app.models import Job

    job = Job(
        user_id=user.id,
        source_type="linkedin",
        source_url="https://www.linkedin.com/jobs/view/unstructured",
        canonical_url="https://www.linkedin.com/jobs/view/unstructured",
        external_job_id="unstructured",
        content_hash="b" * 64,
        job_title="Engineer",
        company_name="Acme",
        description_text="Requirements",
        raw_payload={},
    )
    db.add(job)
    db.flush()

    with pytest.raises(ConflictError, match="not been structured"):
        create_match(db, user_id=user.id, job_id=job.id, profile_version_id=version.id)


# =============================================================================
# Idempotency (§18)
# =============================================================================


def test_the_same_request_twice_returns_the_same_row(db, user, version, job):
    job_row, _ = job
    first, created_first = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )
    second, created_second = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert db.query(MatchResult).filter_by(job_id=job_row.id).count() == 1


def test_a_repeat_request_returns_the_original_as_of_date(db, user, version, job):
    """The reason `as_of_date` is stored rather than keyed on: re-requesting
    yesterday's match returns yesterday's answer, complete with the day it was
    measured against, instead of silently producing a different one."""
    job_row, _ = job
    first, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )
    second, created = create_match(
        db,
        user_id=user.id,
        job_id=job_row.id,
        profile_version_id=version.id,
        as_of=date(2027, 1, 1),
    )

    assert created is False
    assert second.as_of_date == AS_OF


def test_a_different_profile_version_is_a_different_match(db, user, version, job):
    job_row, _ = job
    create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )
    second_version = persisted_profile_version(
        db, user, skills=[("Python", 6.0), ("Rust", None)], version_number=2
    )

    match, created = create_match(
        db,
        user_id=user.id,
        job_id=job_row.id,
        profile_version_id=second_version.id,
        as_of=AS_OF,
    )

    assert created is True
    assert db.query(MatchResult).filter_by(job_id=job_row.id).count() == 2


def test_a_historical_v4_result_is_untouched_and_v5_creates_its_own_row(
    db, user, version, job
):
    """§18 for a version bump specifically: `algorithm_version` is part of
    `uq_match_results_version_requirements_algorithm`, so a row stored under
    a PRIOR algorithm is not the row `_find_existing` looks for under the
    current one. A profile+job pair "already matched" under matching_v4 must
    be re-evaluated under matching_v5 rather than answered from the old row —
    and the old row must survive that untouched, exactly as
    `app/models/match.py`'s ALGORITHM_V1..V4 comments promise for every prior
    bump. This is the one property the whole matching_v5 release depends on
    holding, proved directly rather than assumed from the schema."""
    job_row, requirements = job
    stale = MatchResult(
        user_id=user.id,
        profile_version_id=version.id,
        job_id=job_row.id,
        job_requirements_id=requirements.id,
        algorithm_version="matching_v4",
        as_of_date=AS_OF,
        required_total=1,
        required_matched=0,
        required_missing=1,
        required_uncertain=0,
        preferred_total=0,
        preferred_matched=0,
        preferred_missing=0,
        preferred_uncertain=0,
        required_coverage=0,
        preferred_coverage=1,
        overall_score=0,
    )
    db.add(stale)
    db.commit()
    stale_id = stale.id

    match, created = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    assert created is True, "a v4 row must not satisfy a v5 request"
    assert match.id != stale_id
    assert match.algorithm_version == ALGORITHM_VERSION == "matching_v5_1"

    rows = (
        db.query(MatchResult)
        .filter_by(profile_version_id=version.id, job_requirements_id=requirements.id)
        .all()
    )
    assert {row.algorithm_version for row in rows} == {"matching_v4", "matching_v5_1"}
    preserved = next(row for row in rows if row.algorithm_version == "matching_v4")
    assert preserved.id == stale_id
    assert preserved.required_missing == 1  # untouched, not recomputed under v5


def test_reprocessing_a_job_produces_a_genuinely_new_match(db, user, version, job):
    """§15's central decision. A second completed run writes a NEW
    `job_requirements` row, so the old match keeps describing the old reading
    and the new one is a real evaluation rather than a reinterpretation."""
    job_row, first_requirements = job
    first, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    _, second_requirements = a_structured_job(
        db,
        user,
        job=job_row,
        attempt=2,
        candidate=a_candidate(required_skills=["Python", "Go", "Elixir"]),
    )
    second, created = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    assert created is True
    assert second.id != first.id
    assert second.job_requirements_id == second_requirements.id
    assert first.job_requirements_id == first_requirements.id
    # The old verdicts are untouched and still interpretable.
    db.refresh(first)
    assert first.required_total != second.required_total


def test_an_older_match_is_not_rewritten_by_a_later_profile_version(db, user, version, job):
    """§2's promise, end to end: a later edit must not silently change an
    existing match result."""
    job_row, _ = job
    match, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )
    before = (match.required_matched, match.required_missing, match.overall_score)

    persisted_profile_version(
        db,
        user,
        skills=[("Python", 6.0), ("Rust", None), ("Kubernetes", None)],
        version_number=2,
    )
    db.refresh(match)

    assert (match.required_matched, match.required_missing, match.overall_score) == before
    assert match.profile_version_id == version.id


# =============================================================================
# Reading and ownership (§19)
# =============================================================================


def test_a_match_can_be_read_back_by_its_owner(db, user, version, job):
    job_row, _ = job
    created, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )

    found = get_match(db, user_id=user.id, match_id=created.id)

    assert found.id == created.id
    assert found.requirement_results


def test_another_users_match_is_a_404_not_a_403(db, user, version, job):
    """The same posture the job and profile services take: a stranger's id
    must not be confirmed to exist."""
    job_row, _ = job
    created, _ = create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )
    stranger = another_user(db)

    with pytest.raises(NotFoundError):
        get_match(db, user_id=stranger.id, match_id=created.id)


def test_another_users_profile_version_cannot_be_matched(db, user, version, job):
    """The insecure-direct-object-reference §19 names. A `profile_version_id`
    is a global primary key and proves nothing on its own."""
    job_row, _ = job
    stranger = another_user(db)
    stranger_job, _ = a_structured_job(db, stranger, external_job_id="stranger-job")

    with pytest.raises(NotFoundError):
        create_match(
            db,
            user_id=stranger.id,
            job_id=stranger_job.id,
            profile_version_id=version.id,
            as_of=AS_OF,
        )


def test_another_users_job_cannot_be_matched(db, user, version, job):
    stranger = another_user(db)
    persisted_profile_version(db, stranger, skills=[("Python", None)])
    job_row, _ = job

    with pytest.raises(NotFoundError):
        create_match(db, user_id=stranger.id, job_id=job_row.id, as_of=AS_OF)


def test_listing_is_scoped_to_the_caller(db, user, version, job):
    job_row, _ = job
    create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )
    stranger = another_user(db)

    assert len(list_matches_for_job(db, user_id=user.id, job_id=job_row.id)) == 1
    with pytest.raises(NotFoundError):
        list_matches_for_job(db, user_id=stranger.id, job_id=job_row.id)


def test_listing_returns_newest_first(db, user, version, job):
    job_row, _ = job
    create_match(
        db, user_id=user.id, job_id=job_row.id, profile_version_id=version.id, as_of=AS_OF
    )
    second_version = persisted_profile_version(
        db, user, skills=[("Rust", None)], version_number=2
    )
    newer, _ = create_match(
        db,
        user_id=user.id,
        job_id=job_row.id,
        profile_version_id=second_version.id,
        as_of=AS_OF,
    )

    listed = list_matches_for_job(db, user_id=user.id, job_id=job_row.id)

    assert [item.id for item in listed][0] == newer.id


def test_a_match_that_does_not_exist_is_a_404(db, user):
    with pytest.raises(NotFoundError):
        get_match(db, user_id=user.id, match_id=999999)
