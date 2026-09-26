"""Deterministic normalization, with no database and no AI.

Every function here is pure: given the same input it always produces the same
output, and none of it reasons about what a posting MEANS — that distinction
is the whole reason normalization and structuring are separate modules. These
tests hold that line: nothing here should ever need a structurer, a session,
or a running application.
"""

import pytest

from app.schemas.job import JobIngestionRequest
from app.services.job.normalization import (
    canonicalize_url,
    compute_content_hash,
    normalize_employment_type,
    normalize_job_payload,
    normalize_salary_period,
    normalize_salary_range,
    normalize_workplace_type,
)
from tests.job_fixtures import make_job_payload


# --- employment type -----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Full-time", "full_time"),
        ("FULL TIME", "full_time"),
        ("fulltime", "full_time"),
        ("Contract", "contract"),
        ("Contractor", "contract"),
        ("Contract-to-Hire", "contract"),
        ("Internship", "internship"),
        ("Intern", "internship"),
        ("Part Time", "part_time"),
        ("Temp", "temporary"),
        ("Seasonal", "temporary"),
    ],
)
def test_employment_type_aliases_normalize_to_the_closed_vocabulary(raw, expected):
    assert normalize_employment_type(raw) == expected


def test_an_unrecognized_employment_type_becomes_unknown_not_a_guess():
    assert normalize_employment_type("Contingent Worker Category 7") is None


def test_a_missing_employment_type_stays_missing():
    assert normalize_employment_type(None) is None
    assert normalize_employment_type("") is None


# --- workplace type -----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Remote", "remote"),
        ("Fully Remote", "remote"),
        ("100% Remote", "remote"),
        ("Hybrid", "hybrid"),
        ("On-site", "on_site"),
        ("Onsite", "on_site"),
        ("In Office", "on_site"),
        ("In-Person", "on_site"),
    ],
)
def test_workplace_type_aliases_normalize(raw, expected):
    assert normalize_workplace_type(raw) == expected


def test_an_unrecognized_workplace_type_becomes_unknown():
    assert normalize_workplace_type("Mars base") is None


# --- salary ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Annually", "year"),
        ("annual", "year"),
        ("per year", "year"),
        ("Monthly", "month"),
        ("Hourly", "hour"),
        ("per hour", "hour"),
    ],
)
def test_salary_period_aliases_normalize(raw, expected):
    assert normalize_salary_period(raw) == expected


def test_a_reversed_salary_range_is_reordered():
    """A template bug ('$150k - $120k'), not a claim about the job — restoring
    the ordering the two numbers already imply is normalization, not a guess."""
    assert normalize_salary_range(150_000, 120_000) == (120_000, 150_000)


def test_an_already_ordered_range_is_left_alone():
    assert normalize_salary_range(100, 200) == (100, 200)


def test_a_partial_range_is_never_invented():
    assert normalize_salary_range(None, 50_000) == (None, 50_000)
    assert normalize_salary_range(50_000, None) == (50_000, None)
    assert normalize_salary_range(None, None) == (None, None)


# --- URL canonicalization -----------------------------------------------------


def test_tracking_parameters_are_stripped():
    assert canonicalize_url(
        "https://LinkedIn.com/jobs/123?utm_source=email&utm_campaign=x&ref=abc"
    ) == "https://linkedin.com/jobs/123"


def test_non_tracking_parameters_are_kept():
    assert canonicalize_url("https://example.com/jobs/1?dept=eng") == "https://example.com/jobs/1?dept=eng"


def test_two_fetches_of_the_same_posting_canonicalize_identically():
    a = canonicalize_url("https://linkedin.com/jobs/123?utm_source=email&foo=bar")
    b = canonicalize_url("https://linkedin.com/jobs/123/?foo=bar&utm_source=twitter")
    assert a == b


def test_the_fragment_is_dropped():
    assert canonicalize_url("https://example.com/jobs/1#apply") == "https://example.com/jobs/1"


def test_the_host_is_lowercased_but_the_path_is_not():
    assert canonicalize_url("https://EXAMPLE.com/Jobs/ABC123") == "https://example.com/Jobs/ABC123"


# --- content hash --------------------------------------------------------


def test_content_hash_ignores_case_and_whitespace():
    a = compute_content_hash(job_title="Engineer", company_name="Acme", description="Build things")
    b = compute_content_hash(job_title="  ENGINEER  ", company_name="acme", description="build   things")
    assert a == b


def test_content_hash_differs_for_different_content():
    a = compute_content_hash(job_title="Engineer", company_name="Acme", description="Build things")
    b = compute_content_hash(job_title="Designer", company_name="Acme", description="Build things")
    assert a != b


def test_content_hash_is_a_valid_sha256_hex_digest():
    digest = compute_content_hash(job_title="T", company_name=None, description=None)
    assert len(digest) == 64
    int(digest, 16)  # raises if not valid hex


# --- the whole pass --------------------------------------------------------


def test_normalize_job_payload_never_calls_out_to_anything():
    """No structurer, no database — proof by inspection that this stays a
    pure function as the codebase grows around it."""
    payload = JobIngestionRequest.model_validate(make_job_payload())
    result = normalize_job_payload(payload)

    assert result.job_title == "Senior Backend Engineer"
    assert result.employment_type == "full_time"
    assert result.workplace_type == "hybrid"
    assert result.salary_currency == "USD"
    assert result.canonical_url == "https://www.linkedin.com/jobs/view/123456"
    assert len(result.content_hash) == 64


def test_normalization_does_not_interpret_the_description():
    """No AI interpretation belongs here: the description passes through
    completely unexamined by normalization, byte for byte."""
    payload = JobIngestionRequest.model_validate(
        make_job_payload(description_text="Requirements:\n- 5+ years Python\n")
    )
    result = normalize_job_payload(payload)
    assert result.description_text == "Requirements:\n- 5+ years Python\n"
