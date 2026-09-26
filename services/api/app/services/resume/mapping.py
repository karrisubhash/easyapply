"""Turning an AI reading of a resume into something the profile can hold.

The profile snapshot is stricter than the candidate document, deliberately: an
experience must have a company, a title and a start date, and a skill cannot
appear twice. The candidate document has no such rules, because a resume in the
wild does not either.

So this is where the two meet, and the rule is: never fabricate to fit. An item
that cannot satisfy the snapshot is reported as unmapped, with the reason, and
shown to the user on the review screen. Inventing a start date to make a row
insertable would defeat the entire point of the provenance design.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.schemas.resume_candidate import (
    CandidateDate,
    CandidateLink,
    ResumeCandidate,
    UnmappedItem,
)
from app.schemas.snapshot import Snapshot

#: What skills.years_of_experience is declared as: Numeric(4, 1).
_YEARS_MAX = Decimal("999.9")


def _date_fields(value: CandidateDate | None) -> tuple[str | None, str | None]:
    """A date and its precision travel together, or neither travels.

    The snapshot has a CHECK constraint saying exactly this, so a candidate date
    we could not normalize contributes nothing rather than half a pair.
    """
    if value is None or value.value is None or value.precision is None:
        return None, None
    return value.value.isoformat(), value.precision


def _date_value(value: CandidateDate | None) -> str | None:
    """The date alone, for a table that stores no precision beside it.

    `certifications` has neither precision column nor the paired CHECK, so
    `_date_fields`' rule does not apply to it — and applying it anyway threw
    away any certification date a structurer normalized without saying how
    precisely. That loss is not neutral: `rules.judge_certification` reads
    `expiration_date` to tell a lapsed credential from a live one, so a dropped
    expiry turns an expired certification into one that reads as current, which
    is the confident-wrong-MATCHED this domain treats as its worst output.
    """
    if value is None:
        return None
    return value.value.isoformat() if value.value is not None else None


def _ordered(start: str | None, end: str | None) -> bool:
    """Whether a date range is one the snapshot can hold.

    The profile schema has a CHECK for this, and a violation rejects the entire
    INSERT — so one back-to-front range read off a resume would cost the user
    every other item too. Caught here, it costs that item and says so. ISO dates
    sort as text exactly as they sort as dates, so there is nothing to parse.
    """
    return start is None or end is None or end >= start


def _years_of_experience(value: float | None) -> Decimal | None:
    """Fit a model's number into Numeric(4, 1), or contribute nothing.

    The column holds one decimal place, and the schema enforces it: "3.75 years"
    is refused by Pydantic, and that refusal fails the WHOLE snapshot rather than
    this one field. Rounding to the tenth the column can hold keeps the claim
    intact; a magnitude the column cannot represent is dropped rather than
    silently truncated into a different number.
    """
    if value is None:
        return None
    try:
        rounded = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    # is_finite() first: NaN survives quantize, and then every comparison
    # against it raises rather than answering False.
    if not rounded.is_finite() or rounded < 0 or rounded > _YEARS_MAX:
        return None
    return rounded


#: What profile_links.url is declared as.
_MAX_URL = 2048


def _text(value: str | None, *, limit: int | None = None) -> str | None:
    """A trimmed value, or nothing at all.

    An empty string is not a fact. Letting one through would write a blank name
    over a real one on the next draft, and would make the UI render an empty
    contact block as though the resume had stated something.
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:limit] if limit else cleaned


def _contact(candidate: ResumeCandidate) -> dict | None:
    if candidate.contact is None:
        return None
    contact = {
        "full_name": _text(candidate.contact.full_name, limit=255),
        "email": _text(candidate.contact.email, limit=320),
        "phone": _text(candidate.contact.phone, limit=64),
        "location": _text(candidate.contact.location, limit=255),
    }
    # All-empty is absent, not a blank record.
    return contact if any(contact.values()) else None


def _link_rows(
    items: list[CandidateLink], *, section: str, unmapped: list[UnmappedItem]
) -> list[dict]:
    """One owner's links, once each, in the shape `LinkItem` takes.

    De-duplicated on the URL rather than on the pair: the same profile linked
    twice under two labels is one link, and listing it twice would make the
    review screen look like the resume said something it did not.

    Shared by the person's own links and each project's, so a project link is
    held to exactly the rules a personal one is — the same length limit, the
    same "report, never truncate" answer when a URL exceeds it. A truncated URL
    is a different address, and one that looks like the resume stated it.
    """
    links: list[dict] = []
    seen: set[str] = set()
    for item in items:
        url = _text(item.url)
        if not url:
            continue
        if len(url) > _MAX_URL:
            unmapped.append(
                UnmappedItem(
                    section=section,
                    reason="The link was too long to store.",
                    summary=_text(item.label) or url[:80],
                )
            )
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append({"url": url, "label": _text(item.label, limit=100)})
    return links


def _links(candidate: ResumeCandidate, unmapped: list[UnmappedItem]) -> list[dict]:
    """Every link the resume carried in its contact block, once each."""
    if candidate.contact is None:
        return []
    return _link_rows(candidate.contact.links, section="links", unmapped=unmapped)


#: What project_technologies.name is declared as: String(128), part of the
#: table's composite primary key.
_MAX_TECHNOLOGY = 128


def _technologies(item, unmapped: list[UnmappedItem]) -> list[str]:
    """A project's technologies, each one the column can hold, once each.

    De-duplicated because the composite primary key forbids repeats. A name
    longer than the column is REPORTED rather than truncated or passed through:
    passed through, it was a `draft_creation_failed` at INSERT — the whole
    draft lost, and retried three times to the same end — and truncated, it
    would be a technology the resume never named. Neither Pydantic nor the
    candidate schema caught it, because `CandidateProject.technologies` is
    unbounded and the snapshot's list was too; see `ProjectItem.technologies`.
    """
    kept: list[str] = []
    for raw in item.technologies:
        name = raw.strip()
        if not name:
            continue
        if len(name) > _MAX_TECHNOLOGY:
            unmapped.append(
                UnmappedItem(
                    section="projects",
                    reason=(
                        "A technology listed for this project was too long to "
                        "store as one technology."
                    ),
                    summary=f"{item.name}: {name[:80]}",
                )
            )
            continue
        kept.append(name)
    return list(dict.fromkeys(kept))


def to_snapshot(candidate: ResumeCandidate) -> tuple[Snapshot, list[UnmappedItem]]:
    """Map a candidate reading onto the profile snapshot contract."""
    unmapped: list[UnmappedItem] = []

    experiences = []
    for item in candidate.experiences:
        start, start_precision = _date_fields(item.start_date)
        missing = [
            name
            for name, present in (
                ("company", item.company),
                ("job title", item.job_title),
                ("start date", start),
            )
            if not present
        ]
        if missing:
            unmapped.append(
                UnmappedItem(
                    section="experiences",
                    reason=f"The resume did not give a {', '.join(missing)}.",
                    summary=item.job_title or item.company or "Untitled role",
                )
            )
            continue

        end, end_precision = _date_fields(item.end_date)
        if not _ordered(start, end):
            unmapped.append(
                UnmappedItem(
                    section="experiences",
                    reason="The resume gave an end date before the start date.",
                    summary=item.job_title or item.company or "Untitled role",
                )
            )
            continue

        experiences.append(
            {
                "company": item.company,
                "job_title": item.job_title,
                "employment_type": item.employment_type,
                "location": item.location,
                "start_date": start,
                "start_date_precision": start_precision,
                "end_date": end,
                "end_date_precision": end_precision,
                "description": item.description,
            }
        )

    education = []
    for item in candidate.education:
        if not item.institution:
            unmapped.append(
                UnmappedItem(
                    section="education",
                    reason="The resume did not name an institution.",
                    summary=item.degree or "Unnamed qualification",
                )
            )
            continue
        start, start_precision = _date_fields(item.start_date)
        end, end_precision = _date_fields(item.end_date)
        if not _ordered(start, end):
            unmapped.append(
                UnmappedItem(
                    section="education",
                    reason="The resume gave an end date before the start date.",
                    summary=item.degree or item.institution,
                )
            )
            continue

        education.append(
            {
                "institution": item.institution,
                "degree": item.degree,
                "field_of_study": item.field_of_study,
                "start_date": start,
                "start_date_precision": start_precision,
                "end_date": end,
                "end_date_precision": end_precision,
                # Never defaulted. An absent grade stays absent.
                "grade": item.grade,
                "description": item.description,
            }
        )

    # The snapshot has a case-insensitive unique index on (version, name), so a
    # resume that lists "Python" twice would otherwise fail the whole insert.
    skills = []
    seen: set[str] = set()
    for item in candidate.skills:
        if not item.name:
            continue
        key = item.name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        skills.append(
            {
                "name": item.name.strip(),
                "category": item.category,
                "proficiency": item.proficiency,
                "years_of_experience": _years_of_experience(item.years_of_experience),
            }
        )

    projects = []
    for item in candidate.projects:
        if not item.name:
            unmapped.append(
                UnmappedItem(
                    section="projects",
                    reason="The resume did not name the project.",
                    summary=item.description[:80] if item.description else "Unnamed project",
                )
            )
            continue
        start, start_precision = _date_fields(item.start_date)
        end, end_precision = _date_fields(item.end_date)
        if not _ordered(start, end):
            unmapped.append(
                UnmappedItem(
                    section="projects",
                    reason="The resume gave an end date before the start date.",
                    summary=item.name,
                )
            )
            continue

        projects.append(
            {
                "name": item.name,
                "role": item.role,
                "description": item.description,
                "start_date": start,
                "start_date_precision": start_precision,
                "end_date": end,
                "end_date_precision": end_precision,
                "project_url": item.project_url,
                "technologies": _technologies(item, unmapped),
                # The project's own Demo/GitHub links. Left out, they were read
                # off the page, tied to this project by the structurer, and then
                # dropped here without a word: the snapshot had a `links` field
                # for them and nothing ever filled it.
                "links": _link_rows(item.links, section="projects", unmapped=unmapped),
            }
        )

    certifications = []
    for item in candidate.certifications:
        if not item.name:
            unmapped.append(
                UnmappedItem(
                    section="certifications",
                    reason="The resume did not name the certification.",
                    summary=item.issuing_organization or "Unnamed certification",
                )
            )
            continue
        issue = _date_value(item.issue_date)
        expiry = _date_value(item.expiration_date)
        if not _ordered(issue, expiry):
            unmapped.append(
                UnmappedItem(
                    section="certifications",
                    reason="The resume gave an expiry date before the issue date.",
                    summary=item.name,
                )
            )
            continue

        certifications.append(
            {
                "name": item.name,
                "issuing_organization": item.issuing_organization,
                "issue_date": issue,
                "expiration_date": expiry,
                "credential_id": item.credential_id,
                "credential_url": item.credential_url,
            }
        )

    snapshot = Snapshot.model_validate(
        {
            "contact": _contact(candidate),
            "summary": _text(candidate.summary.text) if candidate.summary else None,
            "links": _links(candidate, unmapped),
            "experiences": experiences,
            "education": education,
            "skills": skills,
            "projects": projects,
            "certifications": certifications,
        }
    )
    return snapshot, unmapped
