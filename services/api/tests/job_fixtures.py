"""Realistic job postings for the job domain tests.

A single builder with sensible defaults, the same shape
`tests/resume_fixtures.py` and `tests/factories.py` use elsewhere in this
suite: every test overrides only the field it cares about, so a test reads as
"what's different here" rather than restating a whole payload.
"""

from typing import Any

REALISTIC_DESCRIPTION = """We are hiring a Senior Backend Engineer to help scale our platform.

Requirements:
- 5+ years of experience in backend development
- Strong proficiency in Python and Django
- Experience with PostgreSQL and Redis
- Bachelor's degree in Computer Science or related field

Preferred:
- Experience with Kubernetes and Docker
- 2-4 years of experience with GraphQL
- AWS Certified Solutions Architect

Responsibilities:
- Design and build scalable APIs
- Mentor junior engineers
- Participate in an on-call rotation

Nice to have:
- Experience with Kafka
- Master's degree
"""


def make_job_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_type": "linkedin",
        "source_url": "https://www.linkedin.com/jobs/view/123456",
        "external_job_id": "123456",
        "job_title": "Senior Backend Engineer",
        "company_name": "Acme Corp",
        "location_raw": "San Francisco, CA (Hybrid)",
        "employment_type": "Full-time",
        "workplace_type": "Hybrid",
        "description_text": REALISTIC_DESCRIPTION,
        "salary_min": 150000,
        "salary_max": 190000,
        "salary_currency": "usd",
        "salary_period": "yearly",
    }
    payload.update(overrides)
    return payload


def make_minimal_job_payload(**overrides: Any) -> dict[str, Any]:
    """Only what the schema actually requires — every optional field absent,
    exactly the shape a sparse careers-page scrape would produce."""
    payload: dict[str, Any] = {
        "source_type": "company_careers",
        "source_url": "https://example.com/careers/42",
        "job_title": "Support Engineer",
    }
    payload.update(overrides)
    return payload
