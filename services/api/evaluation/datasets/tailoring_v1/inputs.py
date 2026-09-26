"""The two sides of a tailoring case, built from real project data.

The profile is the golden resume read by the REAL resume pipeline — extraction,
structuring and mapping — rather than a hand-written fixture. That is the point:
a tailoring evaluation built on a convenient fixture measures tailoring against
data no user will ever have. This one measures it against what the application
actually produces from a document, punctuation and all.

Rows are unsaved. Nothing in the tailoring generator, the grounding validator or
the renderer touches a Session, so nothing here needs one, and a dataset that
cannot be run without a database is a dataset that stops being run.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.models import (
    Certification,
    Education,
    Experience,
    Job,
    ProfileVersion,
    Project,
)
from app.models.profile_link import OWNER_PERSONAL, ProfileLink
from app.models.project import ProjectTechnology
from app.models.skill import Skill
from app.schemas.job_candidate import (
    JobCandidate,
    JobCertificationItem,
    JobEducationItem,
    JobExperienceItem,
    JobProvenance,
    JobQualificationItem,
    JobSkillItem,
    JobTechnologyItem,
)
from app.services.resume.extraction import extract
from app.services.resume.mapping import to_snapshot
from app.services.resume.structuring import DeterministicResumeStructurer
from evaluation.datasets.resume_profile_v1.resume_source import build_resume_pdf

HERE = Path(__file__).parent
CASES = HERE / "cases.json"
JOBS = HERE.parent / "matching_real_v1" / "jobs.json"


def cases() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _snapshot():
    document = extract(build_resume_pdf(), media_type="application/pdf")
    candidate = DeterministicResumeStructurer().structure(document).candidate
    return to_snapshot(candidate)[0]


def confirmed_version() -> ProfileVersion:
    """The golden resume as a confirmed profile version.

    Ordering is applied by hand because the relationship's `order_by` is a SQL
    clause: an unsaved list keeps whatever order it was assigned, and tailoring
    reads `experiences[0]` as "the most recent role".
    """
    snapshot = _snapshot()
    version = ProfileVersion(
        id=1,
        profile_id=1,
        version_number=1,
        status="confirmed",
        created_by_type="user",
        is_current=True,
    )
    if snapshot.contact:
        version.full_name = snapshot.contact.full_name
        version.email = snapshot.contact.email
        version.phone = snapshot.contact.phone
        version.location = snapshot.contact.location
    version.summary = snapshot.summary

    version.links = [
        ProfileLink(
            id=index,
            profile_version_id=1,
            url=row.url,
            label=row.label,
            owner_type=OWNER_PERSONAL,
        )
        for index, row in enumerate(snapshot.links, start=1)
    ]
    version.experiences = sorted(
        (
            Experience(
                id=100 + index,
                profile_version_id=1,
                company=row.company,
                job_title=row.job_title,
                location=row.location,
                start_date=row.start_date,
                start_date_precision=row.start_date_precision,
                end_date=row.end_date,
                end_date_precision=row.end_date_precision,
                description=row.description,
            )
            for index, row in enumerate(snapshot.experiences, start=1)
        ),
        key=lambda row: row.start_date,
        reverse=True,
    )
    version.education = [
        Education(
            id=200 + index,
            profile_version_id=1,
            institution=row.institution,
            degree=row.degree,
            field_of_study=row.field_of_study,
            start_date=row.start_date,
            start_date_precision=row.start_date_precision,
            end_date=row.end_date,
            end_date_precision=row.end_date_precision,
            grade=row.grade,
            description=row.description,
        )
        for index, row in enumerate(snapshot.education, start=1)
    ]
    version.skills = sorted(
        (
            Skill(
                id=300 + index,
                profile_version_id=1,
                name=row.name,
                category=row.category,
                proficiency=row.proficiency,
                years_of_experience=row.years_of_experience,
            )
            for index, row in enumerate(snapshot.skills, start=1)
        ),
        key=lambda row: row.name,
    )

    projects = []
    for index, row in enumerate(snapshot.projects, start=1):
        project = Project(
            id=400 + index,
            profile_version_id=1,
            name=row.name,
            description=row.description,
            role=row.role,
            start_date=row.start_date,
            end_date=row.end_date,
            project_url=row.project_url,
        )
        project.technologies = [
            ProjectTechnology(project_id=project.id, name=name)
            for name in row.technologies
        ]
        projects.append(project)
    version.projects = sorted(projects, key=lambda row: row.name)

    version.certifications = sorted(
        (
            Certification(
                id=600 + index,
                profile_version_id=1,
                name=row.name,
                issuing_organization=row.issuing_organization,
                issue_date=row.issue_date,
                expiration_date=row.expiration_date,
                credential_id=row.credential_id,
                credential_url=row.credential_url,
            )
            for index, row in enumerate(snapshot.certifications, start=1)
        ),
        key=lambda row: row.name,
    )
    return version


def _provenance(quote: str) -> list[JobProvenance]:
    return [JobProvenance(quote=quote, confidence=0.9, verified=True)]


def structured_job(job_id: str) -> tuple[Job, JobCandidate]:
    """One posting from `matching_real_v1`, with its reviewer-confirmed requirements.

    Those requirements are used rather than running the job structurer, for the
    reason that dataset states: a structuring mistake must not surface here and
    be diagnosed as a tailoring failure.
    """
    raw = json.loads(JOBS.read_text(encoding="utf-8"))
    row = next(item for item in raw["jobs"] if item["id"] == job_id)

    candidate = JobCandidate()
    for requirement in row["requirements"]:
        label = requirement["label"]
        quote = requirement["source_quote"]
        required = requirement["is_required"]
        category = requirement["category"]

        if category == "skill":
            item = JobSkillItem(name=label, provenance=_provenance(quote))
            target = candidate.required_skills if required else candidate.preferred_skills
            target.append(item)
        elif category == "technology":
            candidate.technologies.append(
                JobTechnologyItem(
                    name=label, is_required=required, provenance=_provenance(quote)
                )
            )
        elif category == "experience":
            item = JobExperienceItem(
                description=label,
                minimum_years=requirement.get("minimum_years"),
                provenance=_provenance(quote),
            )
            target = (
                candidate.required_experience
                if required
                else candidate.preferred_experience
            )
            target.append(item)
        elif category == "education":
            candidate.education.append(
                JobEducationItem(
                    description=label,
                    degree_level=requirement.get("degree_level"),
                    is_required=required,
                    provenance=_provenance(quote),
                )
            )
        elif category == "certification":
            candidate.certifications.append(
                JobCertificationItem(
                    name=label, is_required=required, provenance=_provenance(quote)
                )
            )
        else:
            candidate.qualifications.append(
                JobQualificationItem(
                    description=label, is_required=required, provenance=_provenance(quote)
                )
            )

    job = Job(
        id=1,
        user_id=1,
        external_job_id=job_id,
        job_title=row["job_title"],
        company_name=row.get("company_name"),
        description_text=row["description"],
    )
    return job, candidate


__all__ = ["cases", "confirmed_version", "structured_job"]
