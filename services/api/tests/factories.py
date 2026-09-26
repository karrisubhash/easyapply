"""Small builders, so the tests read as the thing being tested."""

from datetime import date

from app.models import Certification, Education, Experience, Project, Skill


def an_experience(session, version, flush=True, **kwargs):
    experience = Experience(
        profile_version_id=version.id,
        company=kwargs.pop("company", "APTRANSCO"),
        job_title=kwargs.pop("job_title", "AI Developer Intern"),
        start_date=kwargs.pop("start_date", date(2026, 5, 15)),
        **kwargs,
    )
    session.add(experience)
    if flush:
        session.flush()
    return experience


def a_skill(session, version, name="Python", flush=True, **kwargs):
    skill = Skill(profile_version_id=version.id, name=name, **kwargs)
    session.add(skill)
    if flush:
        session.flush()
    return skill


def an_education(session, version, flush=True, **kwargs):
    education = Education(
        profile_version_id=version.id,
        institution=kwargs.pop(
            "institution", "Anil Neerukonda Institute of Technology & Sciences"
        ),
        **kwargs,
    )
    session.add(education)
    if flush:
        session.flush()
    return education


def a_project(session, version, flush=True, **kwargs):
    project = Project(
        profile_version_id=version.id,
        name=kwargs.pop("name", "EasyApply"),
        **kwargs,
    )
    session.add(project)
    if flush:
        session.flush()
    return project


def a_certification(session, version, flush=True, **kwargs):
    certification = Certification(
        profile_version_id=version.id,
        name=kwargs.pop("name", "AZ-900"),
        **kwargs,
    )
    session.add(certification)
    if flush:
        session.flush()
    return certification
