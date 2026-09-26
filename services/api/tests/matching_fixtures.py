"""Builders for matching tests, so a test reads as the case it is about.

Two flavours, for two genuinely different kinds of test:

  * **In-memory** (`a_version`, `a_skill_row`, ...) — plain ORM objects that
    are never added to a session. The matching rules are a pure function of
    (JobCandidate, ProfileVersion, as_of), so the rule tests need no database
    at all, and not having one makes them fast enough to cover every branch.

  * **Persisted** (`persisted_profile_version`, `a_structured_job`) — the real
    rows the service and API layers need, built the way the application builds
    them: snapshot children are written to a DRAFT version and the version is
    confirmed afterwards, because the immutability triggers refuse writes to a
    confirmed one.

Nothing here asserts anything. A fixture that quietly encodes an expectation is
a test that passes for the wrong reason.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import update

from app.models import (
    Certification,
    Education,
    Experience,
    Job,
    JobProcessingRun,
    JobRequirements,
    Profile,
    ProfileLink,
    ProfileVersion,
    Project,
    ProjectTechnology,
    Skill,
    User,
)
from app.models.profile_link import OWNER_PERSONAL
from app.schemas.job_candidate import (
    JOB_CANDIDATE_SCHEMA_VERSION,
    JobCandidate,
    JobCertificationItem,
    JobEducationItem,
    JobExperienceItem,
    JobProvenance,
    JobQualificationItem,
    JobSkillItem,
    JobTechnologyItem,
)

#: A fixed day to measure durations against. Every duration expectation in the
#: suite is written relative to this, so a test that passes today still passes
#: next year — the whole reason `as_of` is a parameter rather than a clock read.
AS_OF = date(2026, 9, 19)


# --- in-memory profile snapshots ------------------------------------------------


def a_skill_row(name: str, *, years: float | None = None, proficiency: str | None = None, id: int = 0) -> Skill:
    return Skill(
        id=id or _next_id(),
        name=name,
        years_of_experience=Decimal(str(years)) if years is not None else None,
        proficiency=proficiency,
    )


def an_experience_row(
    *,
    company: str = "Acme",
    job_title: str = "Backend Developer",
    start: date = date(2020, 1, 1),
    end: date | None = date(2024, 1, 1),
    description: str | None = None,
    employment_type: str | None = None,
    location: str | None = None,
    id: int = 0,
) -> Experience:
    return Experience(
        id=id or _next_id(),
        company=company,
        job_title=job_title,
        start_date=start,
        end_date=end,
        description=description,
        employment_type=employment_type,
        # Free text, exactly as the column holds it — the matcher parses it,
        # so a fixture that pre-parsed it would test the wrong thing.
        location=location,
    )


def an_education_row(
    *,
    institution: str = "State University",
    degree: str | None = "BSc Computer Science",
    field_of_study: str | None = "Computer Science",
    id: int = 0,
) -> Education:
    return Education(
        id=id or _next_id(),
        institution=institution,
        degree=degree,
        field_of_study=field_of_study,
    )


def a_certification_row(
    name: str, *, issuer: str | None = None, expires: date | None = None, id: int = 0
) -> Certification:
    return Certification(
        id=id or _next_id(),
        name=name,
        issuing_organization=issuer,
        expiration_date=expires,
    )


def a_project_row(
    *,
    name: str = "EasyApply",
    technologies: list[str] | None = None,
    description: str | None = None,
    start: date | None = None,
    end: date | None = None,
    id: int = 0,
) -> Project:
    project = Project(
        id=id or _next_id(),
        name=name,
        description=description,
        start_date=start,
        end_date=end,
    )
    project.technologies = [
        ProjectTechnology(project_id=project.id, name=technology)
        for technology in (technologies or [])
    ]
    return project


def a_version(
    *,
    skills: list[Skill] | None = None,
    experiences: list[Experience] | None = None,
    education: list[Education] | None = None,
    certifications: list[Certification] | None = None,
    projects: list[Project] | None = None,
    status: str = "confirmed",
    id: int = 1,
    # Contact and summary default to None so every existing caller is unchanged.
    # Matching never reads them; tailoring does, because a resume has to say who
    # it is from and the version is the only place that is recorded.
    full_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    location: str | None = None,
    summary: str | None = None,
    links: list[tuple[str, str | None]] | None = None,
) -> ProfileVersion:
    """An unsaved profile version. The matching rules never touch a session."""
    version = ProfileVersion(
        id=id,
        profile_id=1,
        version_number=1,
        status=status,
        created_by_type="user",
        full_name=full_name,
        email=email,
        phone=phone,
        location=location,
        summary=summary,
    )
    version.skills = skills or []
    version.experiences = experiences or []
    version.education = education or []
    version.certifications = certifications or []
    version.projects = projects or []
    version.links = [
        ProfileLink(
            id=index,
            profile_version_id=id,
            url=url,
            label=label,
            owner_type=OWNER_PERSONAL,
        )
        for index, (url, label) in enumerate(links or [], start=1)
    ]
    return version


_counter = {"value": 1000}


def _next_id() -> int:
    _counter["value"] += 1
    return _counter["value"]


# --- in-memory job requirements ---------------------------------------------------


def _cite(quote: str = "requirement text", section: str = "required") -> JobProvenance:
    return JobProvenance(source_section=section, quote=quote, confidence=0.9, verified=True)


def a_candidate(
    *,
    required_skills: list[str] | None = None,
    preferred_skills: list[str] | None = None,
    technologies: list[tuple[str, bool]] | None = None,
    required_experience: list[tuple[str, float | None]] | None = None,
    preferred_experience: list[tuple[str, float | None]] | None = None,
    education: list[tuple[str, str | None, str | None, bool]] | None = None,
    certifications: list[tuple[str, bool]] | None = None,
    qualifications: list[tuple[str, bool]] | None = None,
) -> JobCandidate:
    """A structured job posting, in the shape a structurer would produce.

    Tuples rather than keyword objects because a matching test is about the
    RELATIONSHIP between a requirement and a profile, and spelling out full
    Pydantic constructors buries that in punctuation:

        technologies=[("Python", True), ("Redis", False)]
        education=[("Bachelor's in Computer Science", "bachelor", None, True)]
    """
    return JobCandidate(
        required_skills=[JobSkillItem(name=name, provenance=[_cite(name)]) for name in (required_skills or [])],
        preferred_skills=[
            JobSkillItem(name=name, provenance=[_cite(name, "preferred")])
            for name in (preferred_skills or [])
        ],
        technologies=[
            JobTechnologyItem(name=name, is_required=required, provenance=[_cite(name)])
            for name, required in (technologies or [])
        ],
        required_experience=[
            JobExperienceItem(description=text, minimum_years=years, provenance=[_cite(text)])
            for text, years in (required_experience or [])
        ],
        preferred_experience=[
            JobExperienceItem(description=text, minimum_years=years, provenance=[_cite(text, "preferred")])
            for text, years in (preferred_experience or [])
        ],
        education=[
            JobEducationItem(
                description=text,
                degree_level=level,
                field_of_study=field,
                is_required=required,
                provenance=[_cite(text)],
            )
            for text, level, field, required in (education or [])
        ],
        certifications=[
            JobCertificationItem(name=name, is_required=required, provenance=[_cite(name)])
            for name, required in (certifications or [])
        ],
        qualifications=[
            JobQualificationItem(description=text, is_required=required, provenance=[_cite(text)])
            for text, required in (qualifications or [])
        ],
    )


# --- persisted rows, for the service and API layers --------------------------------


def persisted_profile_version(
    db,
    user: User,
    *,
    skills: list[tuple[str, float | None]] | None = None,
    experiences: list[dict] | None = None,
    education: list[dict] | None = None,
    certifications: list[str] | None = None,
    projects: list[tuple[str, list[str]]] | None = None,
    confirmed: bool = True,
    is_current: bool = True,
    version_number: int = 1,
    profile: Profile | None = None,
) -> ProfileVersion:
    """A real profile version with a real snapshot.

    Built draft-first and confirmed afterwards because that is the only order
    the immutability triggers allow — the same sequence
    `tests/conftest.py::confirm` documents.
    """
    if profile is None:
        profile = db.query(Profile).filter_by(user_id=user.id).one_or_none()
        if profile is None:
            profile = Profile(user_id=user.id)
            db.add(profile)
            db.flush()

    version = ProfileVersion(
        profile_id=profile.id,
        version_number=version_number,
        status="draft",
        created_by_type="user",
        created_by_user_id=user.id,
    )
    db.add(version)
    db.flush()

    for name, years in skills or []:
        db.add(
            Skill(
                profile_version_id=version.id,
                name=name,
                years_of_experience=Decimal(str(years)) if years is not None else None,
            )
        )
    for entry in experiences or []:
        end = entry.get("end", date(2024, 1, 1))
        db.add(
            Experience(
                profile_version_id=version.id,
                company=entry.get("company", "Acme"),
                job_title=entry.get("job_title", "Engineer"),
                location=entry.get("location"),
                start_date=entry.get("start", date(2020, 1, 1)),
                start_date_precision="day",
                end_date=end,
                # Paired with the date by a CHECK: a date and its precision are
                # present together or absent together, because a date whose
                # precision was forgotten is a guess recorded as a fact. An
                # ongoing role (end=None) must therefore carry no precision.
                end_date_precision="day" if end is not None else None,
                description=entry.get("description"),
            )
        )
    for entry in education or []:
        db.add(
            Education(
                profile_version_id=version.id,
                institution=entry.get("institution", "State University"),
                degree=entry.get("degree"),
                field_of_study=entry.get("field_of_study"),
            )
        )
    for name in certifications or []:
        db.add(Certification(profile_version_id=version.id, name=name))
    for name, technologies in projects or []:
        project = Project(profile_version_id=version.id, name=name)
        db.add(project)
        db.flush()
        for technology in technologies:
            db.add(ProjectTechnology(project_id=project.id, name=technology))

    db.flush()

    if confirmed:
        version.status = "confirmed"
        version.confirmed_at = datetime.now(timezone.utc)
        db.flush()
        if is_current:
            # Demote first, exactly as `profile_service.confirm_version` does
            # and for the same reason: `uq_profile_versions_current` is a plain
            # partial index checked per row written, so promoting before
            # demoting violates it. A bulk UPDATE keeps the two statements in
            # this order, which dirtying both ORM rows would not guarantee.
            db.execute(
                update(ProfileVersion)
                .where(
                    ProfileVersion.profile_id == profile.id,
                    ProfileVersion.is_current.is_(True),
                )
                .values(is_current=False)
                .execution_options(synchronize_session=False)
            )
            version.is_current = True
            db.flush()

    db.commit()
    # synchronize_session=False leaves a stale is_current on any sibling still
    # in the identity map.
    db.expire_all()
    return version


def a_structured_job(
    db,
    user: User,
    *,
    candidate: JobCandidate | None = None,
    external_job_id: str = "match-1",
    job_title: str = "Senior Backend Engineer",
    description: str = "Requirements:\n- Python\n",
    attempt: int = 1,
    job: Job | None = None,
) -> tuple[Job, JobRequirements]:
    """A job with one completed run and its structured requirements.

    Written directly rather than through the ingestion pipeline: a matching
    test cares about the requirements a job HAS, not about how they got there,
    and going through the structurer would couple every matching expectation to
    the deterministic structurer's own heuristics.

    Pass an existing `job` with a higher `attempt` to add a second completed
    run — which is what reprocessing produces, and therefore how to test that a
    new requirements row makes a genuinely new match.
    """
    if job is None:
        job = Job(
            user_id=user.id,
            source_type="linkedin",
            source_url=f"https://www.linkedin.com/jobs/view/{external_job_id}",
            canonical_url=f"https://www.linkedin.com/jobs/view/{external_job_id}",
            external_job_id=external_job_id,
            content_hash="a" * 64,
            job_title=job_title,
            company_name="Acme Corp",
            description_text=description,
            raw_payload={},
        )
        db.add(job)
        db.flush()

    run = JobProcessingRun(
        job_id=job.id,
        attempt=attempt,
        status="completed",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    requirements = JobRequirements(
        job_processing_run_id=run.id,
        structurer="deterministic",
        schema_version=JOB_CANDIDATE_SCHEMA_VERSION,
        payload=(candidate or a_candidate(required_skills=["Python"])).model_dump(mode="json"),
    )
    db.add(requirements)
    db.flush()
    db.commit()
    return job, requirements


def another_user(db, email: str = "stranger@example.com") -> User:
    """A second account, for the tests that prove one user cannot reach
    another's profile versions, jobs or matches."""
    account = User(email=email)
    db.add(account)
    db.flush()
    db.commit()
    return account
