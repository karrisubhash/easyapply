"""Builders for tailoring tests.

Reuses `tests/matching_fixtures.py` for the two sides a tailoring run needs — a
persisted confirmed profile version and a structured job — because they are the
same two sides matching needs and a second copy would be a second thing to keep
in step.

What is added here is a profile with enough PROSE in it to tailor. The matching
fixtures are built around skills and durations, which is what matching reads;
tailoring reads experience descriptions, so a version with no description is a
version this domain has nothing to select from.
"""

from __future__ import annotations

from datetime import date

from app.models import User
from app.schemas.job_candidate import JobCandidate, JobSkillItem, JobTechnologyItem
from tests.matching_fixtures import _cite, a_structured_job, persisted_profile_version

#: Two jobs' worth of description, written the way people actually write the
#: field: several clauses per role, some relevant to the posting below and some
#: not, so selection has something to actually select.
SENIOR_ROLE = (
    "Built and operated the payments ingestion service in Python. "
    "Migrated the reporting pipeline onto PostgreSQL with zero downtime. "
    "Ran the team's on-call rotation. "
    "Organised the office move to the new building."
)

EARLIER_ROLE = (
    "Wrote internal tooling in Python for the support team. "
    "Maintained a Django admin used by twelve people."
)


def a_tailorable_version(db, user: User, **overrides):
    """A confirmed version with prose, skills, a project and a degree."""
    defaults = dict(
        skills=[("Python", 5.0), ("PostgreSQL", 3.0), ("Public speaking", None)],
        experiences=[
            {
                "company": "Acme Corp",
                "job_title": "Senior Backend Engineer",
                "location": "Berlin",
                "start": date(2021, 3, 1),
                "end": date(2024, 6, 1),
                "description": SENIOR_ROLE,
            },
            {
                "company": "Initech",
                "job_title": "Backend Developer",
                "start": date(2019, 1, 1),
                "end": date(2021, 2, 1),
                "description": EARLIER_ROLE,
            },
        ],
        education=[
            {
                "institution": "State University",
                "degree": "BSc",
                "field_of_study": "Computer Science",
            }
        ],
        certifications=["AZ-900"],
        projects=[("EasyApply", ["FastAPI", "PostgreSQL"])],
    )
    defaults.update(overrides)
    return persisted_profile_version(db, user, **defaults)


def a_backend_job(db, user: User, **overrides):
    """A posting that wants Python and PostgreSQL, so selection has a target."""
    candidate = overrides.pop(
        "candidate",
        JobCandidate(
            required_skills=[
                JobSkillItem(name="Python", provenance=[_cite("Strong Python")]),
                JobSkillItem(name="PostgreSQL", provenance=[_cite("PostgreSQL")]),
            ],
            technologies=[
                JobTechnologyItem(name="Django", is_required=False, provenance=[_cite("Django")])
            ],
        ),
    )
    defaults = dict(
        candidate=candidate,
        external_job_id="tailor-1",
        job_title="Senior Backend Engineer",
        description="Requirements:\n- Python\n- PostgreSQL\n",
    )
    defaults.update(overrides)
    return a_structured_job(db, user, **defaults)
