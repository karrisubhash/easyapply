"""Deterministic transformations from a validated payload to canonical fields.

Everything here is a pure function: given the same `JobIngestionRequest`, it
always produces the same `NormalizedJob`. No network call, no database read, no
randomness — which is what makes it testable without a database and safe to
run on every ingestion regardless of whether AI structuring is enabled.

This is normalization, not interpretation. "Full-time" becomes `full_time`
because it is the same word with different punctuation, not because anything
here reasoned about what the posting means — that distinction is the whole
point of keeping this module separate from `structuring.py`. Deciding that "5+
years of backend experience" implies a required skill in backend development is
interpretation, and belongs to the structurer, not here.
"""

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.job import EMPLOYMENT_TYPES, WORKPLACE_TYPES
from app.schemas.job import JobIngestionRequest

# --- employment type -----------------------------------------------------

_EMPLOYMENT_ALIASES: dict[str, str] = {
    "full time": "full_time",
    "fulltime": "full_time",
    "part time": "part_time",
    "parttime": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "contract to hire": "contract",
    "c2h": "contract",
    "internship": "internship",
    "intern": "internship",
    "freelance": "freelance",
    "temporary": "temporary",
    "temp": "temporary",
    "seasonal": "temporary",
}

_WORKPLACE_ALIASES: dict[str, str] = {
    "remote": "remote",
    "fully remote": "remote",
    "100% remote": "remote",
    "hybrid": "hybrid",
    "on site": "on_site",
    "onsite": "on_site",
    "in office": "on_site",
    "in person": "on_site",
}

_SALARY_PERIOD_ALIASES: dict[str, str] = {
    "year": "year",
    "years": "year",
    "yearly": "year",
    "annual": "year",
    "annually": "year",
    "per year": "year",
    "yr": "year",
    "month": "month",
    "monthly": "month",
    "per month": "month",
    "mo": "month",
    "hour": "hour",
    "hourly": "hour",
    "per hour": "hour",
    "hr": "hour",
}

# Query parameters that identify how a visitor reached the page, not which
# page it is. Stripped so the same posting fetched via two different marketing
# links still normalizes to the same canonical_url. Prefixed matches
# (`utm_*`) are handled separately from exact matches below.
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = frozenset(
    {
        "ref",
        "refid",
        "referrer",
        "trk",
        "trackingid",
        "gh_src",
        "lever-source",
        "lever-origin",
        "src",
        "source",
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
    }
)


def _fold(value: str) -> str:
    """Case- and punctuation-insensitive key for alias lookups.

    "Contract-to-Hire", "contract to hire" and "CONTRACT_TO_HIRE" are the same
    word choice by three different site templates, and the alias tables above
    are written once, not three times per entry.
    """
    return re.sub(r"[^a-z0-9%]+", " ", value.lower()).strip()


def normalize_employment_type(raw: str | None) -> str | None:
    """A site's own phrasing, mapped onto the vocabulary the schema enforces.

    An unrecognized phrase becomes None rather than being rejected: the
    posting is still worth having even if we cannot classify one field of it,
    and "unknown remains unknown" is the domain's own rule, not a shortcut.
    """
    if not raw:
        return None
    mapped = _EMPLOYMENT_ALIASES.get(_fold(raw))
    return mapped if mapped in EMPLOYMENT_TYPES else None


def normalize_workplace_type(raw: str | None) -> str | None:
    if not raw:
        return None
    mapped = _WORKPLACE_ALIASES.get(_fold(raw))
    return mapped if mapped in WORKPLACE_TYPES else None


def normalize_salary_period(raw: str | None) -> str | None:
    if not raw:
        return None
    return _SALARY_PERIOD_ALIASES.get(_fold(raw))


def normalize_salary_range(
    minimum: float | None, maximum: float | None
) -> tuple[float | None, float | None]:
    """Order a range that arrived backwards.

    A source occasionally lists "$150,000 - $120,000" (a template bug, not a
    claim about the job), and swapping is normalization — restoring the
    ordering the two numbers already imply — not a guess about a fact neither
    number states. Anything else about the numbers is left alone: this
    function never invents a missing bound.
    """
    if minimum is not None and maximum is not None and minimum > maximum:
        return maximum, minimum
    return minimum, maximum


def canonicalize_url(raw_url: str) -> str:
    """Strip what identifies the visit, keep what identifies the page.

    Lowercases the host (hosts are case-insensitive; paths are not, so those
    are left alone — a path-based job id is often case-sensitive), drops the
    fragment (never sent to a server, never part of the resource), and removes
    known tracking parameters while keeping everything else in a stable order
    so the same underlying query string always normalizes identically.
    """
    parts = urlsplit(raw_url.strip())

    kept_params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if _fold(key).replace(" ", "_") not in _TRACKING_PARAM_NAMES
        and not any(_fold(key).replace(" ", "_").startswith(p) for p in _TRACKING_PARAM_PREFIXES)
    ]
    kept_params.sort()

    path = parts.path.rstrip("/") or "/"
    query = urlencode(kept_params)

    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def compute_content_hash(*, job_title: str, company_name: str | None, description: str | None) -> str:
    """A fingerprint for future duplicate-detection heuristics.

    Deliberately not a uniqueness constraint — see `app/models/job.py` — so
    this only ever needs to be consistent with itself, never collision-proof
    against a determined adversary. Case- and whitespace-folded so trivial
    reformatting of the same posting still fingerprints the same.
    """
    normalized = "\x1f".join(
        _fold(part) if part else ""
        for part in (job_title, company_name or "", description or "")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NormalizedJob:
    """The deterministic result of reading a validated payload.

    Every field here lands on a `Job` column of the same name. Keeping this as
    its own return type — rather than mutating a `Job` in place — is what
    makes `normalize_job_payload` testable with no ORM and no session at all.
    """

    job_title: str
    company_name: str | None
    location_raw: str | None
    employment_type: str | None
    workplace_type: str | None
    description_text: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    canonical_url: str
    content_hash: str


def normalize_job_payload(payload: JobIngestionRequest) -> NormalizedJob:
    """The one entry point: a validated payload in, canonical fields out."""
    salary_min, salary_max = normalize_salary_range(payload.salary_min, payload.salary_max)

    return NormalizedJob(
        job_title=payload.job_title,
        company_name=payload.company_name,
        location_raw=payload.location_raw,
        employment_type=normalize_employment_type(payload.employment_type),
        workplace_type=normalize_workplace_type(payload.workplace_type),
        description_text=payload.description_text,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=payload.salary_currency,
        salary_period=normalize_salary_period(payload.salary_period),
        canonical_url=canonicalize_url(payload.source_url),
        content_hash=compute_content_hash(
            job_title=payload.job_title,
            company_name=payload.company_name,
            description=payload.description_text,
        ),
    )
