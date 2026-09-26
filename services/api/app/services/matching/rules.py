"""The deterministic matching rules. No model, no embeddings, no similarity.

Every verdict in this module is reached by a rule a person can read, apply by
hand, and disagree with in review. That is the entire point: before anything
semantic is introduced, there has to be a baseline whose mistakes are
explainable rather than merely plausible.

Two things changed in matching_v2, both because the real-data benchmark showed
matching_v1 getting them wrong in ways its own regression suite could not see.

**A requirement's subject is resolved against a technology vocabulary, not
against whatever is left after removing stopwords.** "3+ years building
production services with Django" used to keep `building` as a subject; against
a profile whose unrelated role said "building credit-risk models" the engine
reported seven years of Django experience for a candidate who had never used
it. Subjects now come from `vocabulary.is_technology` — a curated canonical
list, unioned with whatever the profile itself declares — and a requirement
that resolves none is either a question about total employment or is not
answered at all. It is never answered by a coincidence.

**Evidence is graded rather than binned.** matching_v1 knew "declared in a
field" and "everything else", so six years of daily React described in a
current role scored UNCERTAIN while a graduate's uncorroborated skills-list
entry scored MATCHED at 0.95. `evidence.py` now distinguishes declared-and-
corroborated, demonstrated-in-paid-work, declared-only and merely-mentioned,
and the bar a requirement sets is read from the requirement's own words.

The verdict follows from the evidence tier against the requirement's standard,
not from a score crossing a threshold:

    tier meets the standard        -> MATCHED
    evidence exists, tier too weak -> UNCERTAIN
    no evidence at all             -> MISSING

`confidence` is confidence IN THE VERDICT, not a match strength. A MISSING
with nothing at all in the profile is a confident verdict (0.9); an UNCERTAIN
is by construction an unconfident one (0.3-0.5). Reading it as "how good a
match" would invert its meaning on every MISSING row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date

from app.models.match import (
    CATEGORY_CERTIFICATION,
    CATEGORY_EDUCATION,
    CATEGORY_EXPERIENCE,
    CATEGORY_QUALIFICATION,
    CATEGORY_SKILL,
    CATEGORY_TECHNOLOGY,
    STATUS_MATCHED,
    STATUS_MISSING,
    STATUS_UNCERTAIN,
)
from app.services.matching.evidence import (
    STANDARD_ANY_EVIDENCE,
    STANDARD_PROFESSIONAL_USE,
    TIER_DECLARED_CORROBORATED,
    TIER_DECLARED_ONLY,
    TIER_EMPLOYMENT_DESCRIBED,
    TIER_MENTIONED,
    Evidence,
    TokenEvidence,
    demonstrates_title,
    demonstrates_v5,
    graded,
    satisfies,
    standard_for,
    why_not,
    why_qualifies,
)
from app.services.matching.geography import (
    WORKPLACE_REMOTE,
    RequiredPlaces,
    is_location_requirement,
    parse_location_field,
    places_in_text,
)
from app.services.matching.morphology import action_lemma
from app.services.matching.normalization import canonical, phrase_variants
from app.services.matching.profile_index import ProfileIndex
from app.services.matching.vocabulary import is_generic, is_technology, sentences

#: Degree vocabulary, ordered by rank. Deliberately a local copy of the same
#: real-world vocabulary `app/services/job/structuring.py` matches on, rather
#: than an import of that module's private table: reading a free-text degree
#: into a level is matching's own concern, and making the job domain export
#: internals to the matching domain would couple them the wrong way round for
#: four regexes.
_DEGREE_RANKS: dict[str, int] = {
    "associate": 1,
    "bachelor": 2,
    "master": 3,
    "doctorate": 4,
}

_DEGREE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("doctorate", re.compile(r"\b(ph\.?\s?d|doctorate|dphil)\b", re.IGNORECASE)),
    ("master", re.compile(r"\b(master'?s?|m\.?s\.?c?|mba|m\.?eng|m\.?tech)\b", re.IGNORECASE)),
    (
        "bachelor",
        re.compile(r"\b(bachelor'?s?|b\.?s\.?c?|b\.?a\.?|b\.?eng|b\.?tech|b\.?e\.?)\b", re.IGNORECASE),
    ),
    ("associate", re.compile(r"\b(associate'?s?|a\.?a\.?s?)\b", re.IGNORECASE)),
)

#: "Bachelor's degree in Computer Science or related field" -> "computer science"
_FIELD_FROM_TEXT = re.compile(
    r"\b(?:in|of)\s+([A-Za-z][A-Za-z&/\- ]{2,60})",
    re.IGNORECASE,
)
_FIELD_TAIL = re.compile(
    r"\s*\b(or|and)\b\s*(a\s+)?(closely\s+)?(related|similar|equivalent|relevant)\b.*$",
    re.IGNORECASE,
)

#: Words that carry no subject meaning in an experience requirement. Stripped
#: before deciding what a duration requirement is ABOUT, so that "5+ years of
#: professional experience" is recognised as naming no domain at all.
_EXPERIENCE_STOPWORDS = frozenset(
    {
        "years", "year", "yrs", "yr", "experience", "experienced", "professional",
        "professionally", "minimum", "min", "least", "at", "of", "with", "in", "the",
        "a", "an", "and", "or", "to", "for", "plus", "overall", "total", "working",
        "work", "hands", "on", "proven", "track", "record", "strong", "solid",
        "demonstrated", "relevant", "related", "including", "using", "role", "roles",
        "industry", "commercial", "practical",
    }
)

#: Days per year, averaged over the leap cycle. Durations are reported to one
#: decimal place, so the difference from 365 is not cosmetic at the 5-year mark.
_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class RequirementJudgment:
    """One requirement, judged. The engine turns these into rows."""

    category: str
    requirement_index: int
    requirement_label: str
    requirement_key: str | None
    is_required: bool
    status: str
    confidence: float
    explanation: str
    matched_value: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    #: What the requirement own wording demanded of the evidence. Stored so a
    #: reader can see WHY a listed skill was or was not enough, which is the
    #: question the real-data benchmark showed users cannot otherwise answer.
    evidence_standard: str = STANDARD_ANY_EVIDENCE


def _label(text: str | None, fallback: str = "(unnamed requirement)") -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return fallback
    return cleaned if len(cleaned) <= 255 else cleaned[:254] + "…"


# --- skills and technologies --------------------------------------------------


def judge_token_requirement(
    *,
    name: str,
    category: str,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    quotes: tuple[str, ...] = (),
) -> RequirementJudgment:
    """A requirement that names one thing: a skill or a technology.

    A canonical-token lookup, graded. No substring search, no similarity:
    "Python" cannot be satisfied by "Django", and "React" cannot be satisfied
    by "React Native", because those normalize to different tokens and nothing
    in `normalization.ALIASES` claims otherwise.

    What the profile must SHOW depends on what the posting asked for.
    "Kubernetes" in a nice-to-have list is answered by a skills entry;
    "Kubernetes in production, not just in a course" is not. `quotes` carries
    the posting's own verified words for this requirement, which is where that
    distinction lives — the extracted name "Kubernetes" has lost it.
    """
    token = canonical(name)
    label = _label(name)
    standard = standard_for(name, *quotes)

    if not token:
        return RequirementJudgment(
            category=category,
            requirement_index=index,
            requirement_label=label,
            requirement_key=None,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.0,
            explanation="This requirement has no name that could be normalized, so it could not be checked.",
            evidence_standard=standard,
        )

    return _judge_token(
        token=token,
        label=label,
        category=category,
        index=index,
        is_required=is_required,
        profile=profile,
        standard=standard,
    )


#: How sure we are of a MATCHED, by the kind of evidence that produced it.
#: A declaration nobody corroborated is still a match where the posting asked
#: only for the skill — and it is a less certain one, which is what this says.
_MATCH_CONFIDENCE = {
    TIER_DECLARED_CORROBORATED: 0.95,
    TIER_EMPLOYMENT_DESCRIBED: 0.9,
    TIER_DECLARED_ONLY: 0.75,
}

_TIER_PHRASE = {
    TIER_DECLARED_CORROBORATED: "lists {token} and describes paid work using it",
    TIER_EMPLOYMENT_DESCRIBED: "describes paid work using {token}",
    TIER_DECLARED_ONLY: "lists {token}",
    TIER_MENTIONED: "mentions {token}",
}

#: The same four tiers, phrased for a work-action rather than a thing. "paid
#: work using leading" is not English; "paid work involving leading" is. A
#: verdict a person cannot read is not an explainable verdict.
_TIER_PHRASE_ACTION = {
    TIER_DECLARED_CORROBORATED: "lists {token} and describes paid work involving it",
    TIER_EMPLOYMENT_DESCRIBED: "describes paid work involving {token}",
    TIER_DECLARED_ONLY: "lists {token}",
    TIER_MENTIONED: "mentions {token}",
}


def _judge_token(
    *,
    token: str,
    label: str,
    category: str,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    standard: str,
    found: TokenEvidence | None = None,
    display: str | None = None,
) -> RequirementJudgment:
    """The shared body of every verdict about one named concept.

    `found` lets a caller supply evidence it resolved some other way — the
    work-action scan passes the morphology index's answer, so "leading" can be
    satisfied by "Lead four engineers". Default None means the ordinary
    surface-form lookup, which is what every technology requirement uses.

    `display` is the word to put in the explanation when it differs from the
    key: the requirement said "leading" and the concept is filed under "lead",
    and a sentence about "lead" would not read as an answer to what was asked.
    """
    found = profile.evidence_for(token) if found is None else found
    # `display` is set only by the work-action path, which is also the only
    # path whose subject is a verb — so it doubles as the signal for which
    # phrasing reads correctly.
    spoken = display or token
    phrases = _TIER_PHRASE_ACTION if display else _TIER_PHRASE
    tier = found.tier

    if tier is None:
        return RequirementJudgment(
            category=category,
            requirement_index=index,
            requirement_label=label,
            requirement_key=token,
            is_required=is_required,
            status=STATUS_MISSING,
            confidence=0.9,
            explanation=(
                f"No skill, project technology or description in this profile version mentions "
                f"{spoken}. No qualifying evidence was found."
            ),
            evidence_standard=standard,
        )

    cited = [graded(item, tier) for item in found.best()]

    if satisfies(tier, standard):
        return RequirementJudgment(
            category=category,
            requirement_index=index,
            requirement_label=label,
            requirement_key=token,
            is_required=is_required,
            status=STATUS_MATCHED,
            confidence=_MATCH_CONFIDENCE[tier],
            explanation=(
                f"The profile {phrases[tier].format(token=spoken)}. "
                f"{why_qualifies(tier, standard)}"
            ),
            matched_value=token,
            evidence=cited,
            evidence_standard=standard,
        )

    return RequirementJudgment(
        category=category,
        requirement_index=index,
        requirement_label=label,
        requirement_key=token,
        is_required=is_required,
        status=STATUS_UNCERTAIN,
        confidence=0.5 if tier == TIER_DECLARED_ONLY else 0.4,
        explanation=(
            f"The profile {phrases[tier].format(token=spoken)}, which does not settle this "
            f"requirement. {why_not(tier, standard)}"
        ),
        matched_value=token,
        evidence=cited,
        evidence_standard=standard,
    )


# --- certifications ------------------------------------------------------------


def judge_certification(
    *,
    name: str,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    as_of: date,
) -> RequirementJudgment:
    """A named credential.

    One equivalence rule beyond exact match, and it only runs in one direction:
    a profile credential that is a MORE specific form of the required one
    satisfies it ("AWS Certified Developer – Associate" answers a requirement
    for "AWS Certified Developer"), because the extra words name the level of
    the same credential. The reverse does not: a requirement for the Associate
    level is not answered by the unqualified name, so that case is UNCERTAIN
    rather than MATCHED.

    Unrelated credentials from the same vendor never match. "AWS Certified
    Solutions Architect" shares a prefix with nothing in "AWS Certified
    Developer" once both are whole-token compared.
    """
    required_token = canonical(name)
    label = _label(name)

    if not required_token:
        return RequirementJudgment(
            category=CATEGORY_CERTIFICATION,
            requirement_index=index,
            requirement_label=label,
            requirement_key=None,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.0,
            explanation="This certification requirement has no name that could be normalized.",
        )

    exact: list = []
    more_specific: list = []
    less_specific: list = []

    for certification in profile.certifications:
        held = canonical(certification.name)
        if not held:
            continue
        if held == required_token:
            exact.append(certification)
        elif held.startswith(required_token + " "):
            more_specific.append(certification)
        elif required_token.startswith(held + " "):
            less_specific.append(certification)

    def _expired(certification) -> bool:
        return certification.expiration_date is not None and certification.expiration_date < as_of

    for bucket, confidence, kind in ((exact, 0.95, "exactly"), (more_specific, 0.8, "as a more specific form")):
        if not bucket:
            continue
        live = [item for item in bucket if not _expired(item)]
        chosen = live or bucket
        evidence = [profile.certification_evidence(item) for item in chosen]
        if not live:
            expiry = chosen[0].expiration_date.isoformat() if chosen[0].expiration_date else "an earlier date"
            return RequirementJudgment(
                category=CATEGORY_CERTIFICATION,
                requirement_index=index,
                requirement_label=label,
                requirement_key=required_token,
                is_required=is_required,
                status=STATUS_UNCERTAIN,
                confidence=0.4,
                explanation=(
                    f"The profile holds this certification, but it expired on {expiry}. "
                    f"Whether a lapsed credential satisfies the posting is not something "
                    f"the profile can answer."
                ),
                matched_value=canonical(chosen[0].name),
                evidence=evidence,
            )
        return RequirementJudgment(
            category=CATEGORY_CERTIFICATION,
            requirement_index=index,
            requirement_label=label,
            requirement_key=required_token,
            is_required=is_required,
            status=STATUS_MATCHED,
            confidence=confidence,
            explanation=(
                f"The profile holds a certification matching this requirement {kind}: "
                f"{_label(chosen[0].name)}."
            ),
            matched_value=canonical(chosen[0].name),
            evidence=evidence,
        )

    if less_specific:
        held = less_specific[0]
        return RequirementJudgment(
            category=CATEGORY_CERTIFICATION,
            requirement_index=index,
            requirement_label=label,
            requirement_key=required_token,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.4,
            explanation=(
                f"The profile lists {_label(held.name)}, which names the same credential family "
                f"but not the specific level this posting asks for. Whether it qualifies needs a "
                f"person to confirm."
            ),
            matched_value=canonical(held.name),
            evidence=[profile.certification_evidence(held)],
        )

    return RequirementJudgment(
        category=CATEGORY_CERTIFICATION,
        requirement_index=index,
        requirement_label=label,
        requirement_key=required_token,
        is_required=is_required,
        status=STATUS_MISSING,
        confidence=0.9,
        explanation=(
            "No certification in this profile version matches this requirement. No qualifying "
            "evidence was found."
        ),
    )


# --- education -----------------------------------------------------------------


def degree_level(text: str | None) -> str | None:
    """The level a free-text degree names, or None if it names none."""
    if not text:
        return None
    for level, pattern in _DEGREE_PATTERNS:
        if pattern.search(text):
            return level
    return None


def field_of_study_from_text(text: str | None) -> str | None:
    """The subject a requirement names, when it names one in prose.

    The deterministic job structurer never fills `field_of_study` — it records
    the level and the sentence — so without this, "Bachelor's in Computer
    Science" and "Bachelor's degree" would be the same requirement, and §10's
    whole concern (a bachelor's in an unrelated subject) could never arise.
    """
    if not text:
        return None
    match = _FIELD_FROM_TEXT.search(text)
    if not match:
        return None
    captured = _FIELD_TAIL.sub("", match.group(1)).strip(" .,;")
    # "degree in ..." captures the word "degree" when the sentence is phrased
    # the other way round; that is not a field of study.
    if not captured or canonical(captured) in {"a", "the", "degree", "study", "studies"}:
        return None
    return captured


def judge_education(
    *,
    description: str,
    degree_level_required: str | None,
    field_required: str | None,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    as_of: date | None = None,
) -> RequirementJudgment:
    """A degree requirement.

    Two independent questions, and conflating them is exactly the mistake §10
    warns about: does the profile reach the required LEVEL, and is it in the
    required FIELD? A B.Tech in Mechanical Engineering answers the first for a
    bachelor's requirement and does not answer the second — which is UNCERTAIN,
    not MATCHED, because "or related field" is a judgment the profile schema
    holds no information to make.
    """
    label = _label(description)
    required_level = (degree_level_required or "").strip().lower() or None
    required_field = field_required or field_of_study_from_text(description)
    required_rank = _DEGREE_RANKS.get(required_level or "", 0)

    if not profile.education:
        return RequirementJudgment(
            category=CATEGORY_EDUCATION,
            requirement_index=index,
            requirement_label=label,
            requirement_key=required_level,
            is_required=is_required,
            status=STATUS_MISSING,
            confidence=0.9,
            explanation="This profile version records no education, so no qualifying evidence was found.",
        )

    if required_rank == 0:
        return RequirementJudgment(
            category=CATEGORY_EDUCATION,
            requirement_index=index,
            requirement_label=label,
            requirement_key=None,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.3,
            explanation=(
                "The posting's education requirement does not state a degree level this matcher "
                "recognizes, so it could not be checked against the profile."
            ),
            evidence=[profile.education_evidence(item) for item in profile.education[:3]],
        )

    qualifying = []
    # matching_v5_1: a degree whose end date is still ahead of `as_of` is being
    # studied for, not held. It used to count as held — a BSc ending in 2027
    # answered "Bachelor's degree required" in 2026 as MATCHED.
    in_progress = []
    for item in profile.education:
        level = degree_level(item.degree) or degree_level(item.institution if item.degree is None else None)
        rank = _DEGREE_RANKS.get(level or "", 0)
        if rank >= required_rank:
            # Ahead of `as_of` at either end: the resume structurer stores
            # "Expected May 2027" as a start date with no end, and that is a
            # degree being studied for just as much as an explicit range is.
            if as_of is not None and any(
                value is not None and value > as_of for value in (item.end_date, item.start_date)
            ):
                in_progress.append((item, level, rank))
            else:
                qualifying.append((item, level, rank))

    if not qualifying and in_progress:
        item, level, _ = in_progress[0]
        return RequirementJudgment(
            category=CATEGORY_EDUCATION,
            requirement_index=index,
            requirement_label=label,
            requirement_key=required_level,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.4,
            explanation=(
                f"The profile records a {level} degree that is still in progress, expected "
                f"{(item.end_date or item.start_date).year}. It does not yet meet the requirement; whether the "
                f"posting accepts a degree in progress is not something the profile states."
            ),
            matched_value=level,
            evidence=[profile.education_evidence(entry) for entry, _, _ in in_progress[:3]],
        )

    if not qualifying:
        return RequirementJudgment(
            category=CATEGORY_EDUCATION,
            requirement_index=index,
            requirement_label=label,
            requirement_key=required_level,
            is_required=is_required,
            status=STATUS_MISSING,
            confidence=0.8,
            explanation=(
                f"No education entry in this profile version reaches {required_level} level. "
                f"What the profile does record is shown as evidence."
            ),
            evidence=[profile.education_evidence(item) for item in profile.education[:3]],
        )

    if not required_field:
        item, level, _ = qualifying[0]
        return RequirementJudgment(
            category=CATEGORY_EDUCATION,
            requirement_index=index,
            requirement_label=label,
            requirement_key=required_level,
            is_required=is_required,
            status=STATUS_MATCHED,
            confidence=0.9,
            explanation=(
                f"The posting asks for a {required_level} degree and names no subject; the "
                f"profile records a {level} degree."
            ),
            matched_value=level,
            evidence=[profile.education_evidence(item) for item, _, _ in qualifying[:3]],
        )

    required_field_token = canonical(required_field)
    for item, level, _ in qualifying:
        held_tokens = phrase_variants(item.field_of_study) | phrase_variants(item.degree)
        if required_field_token in held_tokens:
            return RequirementJudgment(
                category=CATEGORY_EDUCATION,
                requirement_index=index,
                requirement_label=label,
                requirement_key=required_level,
                is_required=is_required,
                status=STATUS_MATCHED,
                confidence=0.9,
                explanation=(
                    f"The profile records a {level} degree in {_label(item.field_of_study or item.degree)}, "
                    f"which is the subject the posting names."
                ),
                matched_value=f"{level}:{required_field_token}",
                evidence=[profile.education_evidence(item)],
            )

    item, level, _ = qualifying[0]
    held = _label(item.field_of_study or item.degree or item.institution)
    return RequirementJudgment(
        category=CATEGORY_EDUCATION,
        requirement_index=index,
        requirement_label=label,
        requirement_key=required_level,
        is_required=is_required,
        status=STATUS_UNCERTAIN,
        confidence=0.4,
        explanation=(
            f"The profile reaches the required {required_level} level, but in {held} rather than "
            f"{_label(required_field)}. Whether that counts as a related field is not something "
            f"this profile version states."
        ),
        matched_value=level,
        evidence=[profile.education_evidence(item) for item, _, _ in qualifying[:3]],
    )


# --- experience ----------------------------------------------------------------


@dataclass(frozen=True)
class _Span:
    start: date
    end: date


def merge_years(spans: list[_Span]) -> float:
    """Total years covered by a set of possibly-overlapping periods.

    Two concurrent jobs from 2020 to 2022 are two years of experience, not
    four. Summing durations would double-count exactly the people most likely
    to have overlapping rows — contractors, anyone with a side role — so the
    intervals are merged first.
    """
    if not spans:
        return 0.0
    ordered = sorted(spans, key=lambda span: span.start)
    merged: list[list[date]] = [[ordered[0].start, ordered[0].end]]
    for span in ordered[1:]:
        current = merged[-1]
        if span.start <= current[1]:
            if span.end > current[1]:
                current[1] = span.end
        else:
            merged.append([span.start, span.end])
    days = sum((end - start).days for start, end in merged)
    return round(max(days, 0) / _DAYS_PER_YEAR, 1)


def _spans_for(experiences, as_of: date) -> list[_Span]:
    """The dated periods of these roles, up to `as_of` and never past it.

    matching_v5_1: an end date after `as_of` — a fixed-term contract's agreed
    end, an "expected" date — used to be counted in full, so a role from
    January 2024 "to" January 2028 answered "3+ years" in September 2026 with
    about four. Years that have not happened are not experience; the period is
    clamped to `as_of`, and a role that has not started yet contributes nothing.
    """
    spans = []
    for item in experiences:
        if item.start_date is None:
            continue
        if item.start_date > as_of:
            # Dated, and measured: zero years so far. Skipping it instead would
            # leave "records employment, but none of it carries dates" — which
            # is not what the profile says.
            spans.append(_Span(start=as_of, end=as_of))
            continue
        end = min(item.end_date or as_of, as_of)
        if end < item.start_date:
            continue
        spans.append(_Span(start=item.start_date, end=end))
    return spans


def _employment_citations(profile: ProfileIndex, token: str | None, roles) -> list[Evidence]:
    """The roles a duration verdict counted, each with the sentence that tied it.

    matching_v5_1: these used to be `experience_evidence(role)` — a title and a
    date range, no quote — so a verdict counting a role toward "3+ years of
    Django" could not show the sentence that made it a Django role, and a
    reader could not see when that sentence was wrong. The quote-bearing item
    the index filed for this token and role is cited instead, with the role's
    date range kept as the detail, because the dates are what was counted.
    """
    by_role: dict[int, Evidence] = {}
    if token is not None:
        for item in profile.evidence_for(token).employment:
            if item.source_id is not None and item.source_id not in by_role:
                by_role[item.source_id] = item
    cited: list[Evidence] = []
    for role in roles[:5]:
        dated = profile.experience_evidence(role)
        quoted = by_role.get(role.id)
        item = dated if quoted is None else replace(quoted, detail=dated.detail)
        cited.append(graded(item, TIER_EMPLOYMENT_DESCRIBED))
    return cited


def _content_words(description: str | None) -> set[str]:
    """The words of a requirement that carry any meaning about its subject.

    Scaffolding is removed — the numerals, and the words every duration
    requirement is made of ("years", "of", "professional", "minimum"). What
    remains is either a technology, a description of the KIND of work, or
    something this engine cannot interpret; `resolve_technology_subject`
    decides which.
    """
    words: set[str] = set()
    for token in phrase_variants(description):
        parts = token.split()
        # A curated multi-word technology keeps its own glue word: "ruby on
        # rails" was dropped for its "on", so "3+ years of Ruby on Rails or
        # Django" resolved ruby + rails + django, joined by " on ", and read as
        # all-of — a confident MISSING for a Django developer (matching_v5_1).
        if any(_is_scaffolding(part) for part in parts) and not (
            len(parts) > 1 and is_technology(token)
        ):
            continue
        words.add(token)
    return words


def _is_scaffolding(part: str) -> bool:
    """A word that frames a duration requirement rather than saying anything.

    `.rstrip("+")` is load-bearing: `"5+".isdigit()` is False, so without it
    "5+ years of professional experience" keeps "5+" as a content word and is
    never recognised as a question about total employment.
    """
    return part in _EXPERIENCE_STOPWORDS or part.rstrip("+").replace(".", "", 1).isdigit()


def _ordered(tokens: set[str]) -> list[str]:
    """Tokens, most specific first, in an order that never varies.

    A set is iterated in hash order and Python salts string hashes per
    process, so picking "the first token that matches" would choose
    differently on a different run and two identical evaluations could store
    different keys. Sorting is what buys reproducibility.

    Most specific first because "machine learning" is a better answer than
    "learning" when both are present; length and then alphabetical order are
    tie-breaks that exist only to make the ordering total.
    """
    return sorted(tokens, key=lambda token: (-token.count(" "), -len(token), token))


def resolve_technology_subject(
    description: str | None, profile: ProfileIndex
) -> str | None:
    """The technology a requirement is about, or None.

    THE fix the real-data benchmark demanded. matching_v1 took whatever
    survived a stopword filter, so "3+ years building production services with
    Django" resolved `building`, and any role whose description happened to
    contain that word was counted as Django experience — seven years of it,
    for a candidate who had never used Django.

    A token is a subject here only if `vocabulary.is_technology` says so: it is
    in the curated canonical list, or the profile itself declares it as a skill
    or a project technology. Generic words describing the act or the kind of
    work are never subjects, however many of them a requirement contains.

    When several technologies are named, the most specific is chosen
    deterministically — and nothing is invented when none resolves.
    """
    ordered = _ordered(_subject_candidates(description, profile))
    return ordered[0] if ordered else None


def _subject_candidates(description: str | None, profile: ProfileIndex) -> set[str]:
    declared = frozenset(profile.declared_tokens)
    return {
        token
        for token in _content_words(description)
        if is_technology(token, declared=declared)
        and _stands_as_a_subject(token, description or "")
    }


#: A one- or two-letter technology written this way in the requirement text is
#: part of another word, not a subject: "R&D", "C-level", "C-suite". The
#: hyphen is refused only before those idioms, so "Go-based services" still
#: names Go.
_SHORT_SUBJECT = "(?<![\\w&-]){token}(?![\\w&])(?!-(?:level|suite)\\b)"


def _stands_as_a_subject(token: str, description: str) -> bool:
    """Whether a very short technology token is really named here.

    matching_v5_1. The curated vocabulary holds "c", "r" and "go", so "5+ years
    of R&D experience" resolved `r`, "3+ years in C-level stakeholder
    management" resolved `c`, and "features that go to production" resolved
    `go` — each then judged against the candidate's R, C or Go. A token of one
    or two letters is kept only where the requirement writes it as a word of
    its own and capitalised ("5+ years of Go", "C and C++"), which is how every
    posting names those languages. Longer tokens are unaffected.

    A short token the text never writes at all came from an alias — "Golang"
    is `go`, "continuous integration" is `ci`, "CSharp" is `c#` — and the alias
    is the long, unambiguous spelling, so it stands. (Refusing those was the
    first version of this rule, and it turned "5+ years of Golang" UNCERTAIN and
    let "continuous integration" fall back to the generic word "integration".)
    """
    # A short token with a digit in it ("k6") is no English word, and is written
    # in lower case by its own project — the capital rule would drop it, and
    # with it the second half of "3+ years with k6 and JMeter".
    if len(token) > 2 or any(character.isdigit() for character in token):
        return True
    pattern = re.compile(_SHORT_SUBJECT.format(token=re.escape(token)), re.IGNORECASE)
    literal = list(pattern.finditer(description))
    anywhere = re.search(
        rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", description, re.IGNORECASE
    )
    if not literal and anywhere is None:
        return True
    return any(match.group(0)[:1].isupper() for match in literal)


#: How two technologies in one requirement are joined, read from the words
#: between them. "or" and a slash offer alternatives ("Python or Go",
#: "C/C++"); a bare comma is a list separator whose meaning the LAST joiner
#: decides ("Docker, Helm, or Kubernetes"); anything else — "and", "&", or
#: other words — means all of them.
_OR_JOIN = re.compile(r"^\s*,?\s*(?:or|/|and\s*/\s*or)\s*$", re.IGNORECASE)
#: The word "or" (with "and/or"), as opposed to a bare slash. Only a WORD may
#: close a comma list as alternatives: in "React, TypeScript, HTML/CSS" the
#: slash is inside the last item, and reading it as the list's "or" credited a
#: React-only profile with the whole list.
_OR_WORD_JOIN = re.compile(r"^\s*,?\s*(?:or|and\s*/\s*or)\s*$", re.IGNORECASE)
_COMMA_JOIN = re.compile(r"^\s*,\s*$")
#: Punctuation a joiner may carry that says nothing about and/or: "Python (or
#: Go)", "Java or .NET" — the parenthesis and the dot of ".NET" used to land in
#: the joiner and make a plain "or" read as all-of.
_JOINER_NOISE = re.compile(r"[()\[\]]|\.(?=\S*$)")
_SUBJECT_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#]*")


def _subject_spans(description: str, subjects: list[str]) -> dict[str, tuple[int, int]]:
    """Where each subject is written, as the first 1-3 word window that
    canonicalises to it. A subject the text cannot be located in is left out,
    and the caller then reads the requirement as needing all of them."""
    words = [(m.start(), m.end()) for m in _SUBJECT_WORD.finditer(description)]
    wanted = set(subjects)
    spans: dict[str, tuple[int, int]] = {}
    for size in (3, 2, 1):
        for start in range(len(words) - size + 1):
            begin, end = words[start][0], words[start + size - 1][1]
            token = canonical(description[begin:end])
            if token in wanted and token not in spans:
                spans[token] = (begin, end)
    return spans


def _offers_alternatives(description: str, subjects: list[str]) -> bool:
    """Whether the requirement will accept any one of these technologies.

    Read from how the technologies are JOINED, never from an "or" anywhere in
    the sentence: the first version of this rule did the latter, and "5 or more
    years with Kubernetes and Go", "…or equivalent experience" and "…in a startup
    or enterprise environment" were all read as "either will do" — so a profile
    with Go and no Kubernetes was MATCHED.
    """
    spans = _subject_spans(description, subjects)
    if len(spans) < len(subjects):
        return False
    ordered = sorted(spans.values())
    joiners = [
        _JOINER_NOISE.sub(" ", description[left[1] : right[0]])
        for left, right in zip(ordered, ordered[1:])
    ]
    if not joiners:
        return False
    if not all(_OR_JOIN.match(text) or _COMMA_JOIN.match(text) for text in joiners):
        return False
    if any(_COMMA_JOIN.match(text) for text in joiners):
        # A comma list is alternatives only when a WORD closes it.
        return bool(_OR_WORD_JOIN.match(joiners[-1]))
    return all(_OR_JOIN.match(text) for text in joiners)


def resolve_technology_subjects(
    description: str | None, profile: ProfileIndex
) -> tuple[list[str], bool]:
    """Every technology a requirement names, and whether ANY one of them will do.

    matching_v5_1. `resolve_technology_subject` keeps the single most specific
    token, which is right for "3+ years building production services with
    Django" and wrong for "5+ years with Kubernetes and Go": a profile with
    eight years of Kubernetes and no Go at all was MATCHED. Here every named
    technology is returned — minus any that is only part of a longer one
    ("django" inside "django rest framework") — with `any_of` True when the
    requirement offers them as alternatives ("Python or Go", "either … or").
    The default is that ALL are required, because that is the reading that
    can never credit a candidate with something the posting asked for and
    the profile does not show.
    """
    candidates = _subject_candidates(description, profile)
    maximal = [
        token
        for token in _ordered(candidates)
        if not any(
            other != token and f" {token} " in f" {other} " for other in candidates
        )
    ]
    any_of = len(maximal) > 1 and _offers_alternatives(description or "", maximal)
    return maximal, any_of


def _combine_subjects(
    judgments: list[RequirementJudgment],
    *,
    any_of: bool,
    label: str,
    category: str,
    index: int,
    is_required: bool,
    standard: str,
) -> RequirementJudgment:
    """One verdict for a requirement naming several technologies.

    All-of: MATCHED only if every technology is; MISSING if any is; otherwise
    UNCERTAIN. Any-of: MATCHED if one is; MISSING only if every one is. Each
    technology's own explanation is kept, so the reader sees which one fell
    short rather than a verdict about a word they cannot find.
    """
    statuses = [item.status for item in judgments]
    if any_of:
        if STATUS_MATCHED in statuses:
            status = STATUS_MATCHED
        elif all(value == STATUS_MISSING for value in statuses):
            status = STATUS_MISSING
        else:
            status = STATUS_UNCERTAIN
    else:
        if all(value == STATUS_MATCHED for value in statuses):
            status = STATUS_MATCHED
        elif STATUS_MISSING in statuses:
            status = STATUS_MISSING
        else:
            status = STATUS_UNCERTAIN

    keys = [item.requirement_key for item in judgments if item.requirement_key]
    joiner = " or " if any_of else " and "
    lead = (
        "Any one of these would answer it. "
        if any_of
        else "The posting names all of these, so each is judged. "
    )
    # The explanations that decided the verdict go first: the stored column is
    # 500 characters, and a list of five technologies overflows it — the first
    # version put them in posting order and cut the one sentence that said
    # which technology was missing.
    # The decisive verdict is the combined one: for an any-of UNCERTAIN it is the
    # uncertain technology that kept the verdict off MISSING, and putting the
    # MISSING ones first (the previous rule) cut that sentence off at 500.
    ordered = sorted(judgments, key=lambda item: item.status != status)
    # Under an any-of MATCHED only the technologies that matched are its
    # evidence. The others fell short, and `service` marks every item of a
    # MATCHED verdict as qualifying — so citing them would show a shortfall to
    # the user as support, the contradiction v5_1 exists to remove.
    cited = (
        [item for item in ordered if item.status == STATUS_MATCHED]
        if any_of and status == STATUS_MATCHED
        else ordered
    )
    evidence: list[Evidence] = []
    for item in cited:
        for piece in item.evidence:
            if len(evidence) < 5 and piece not in evidence:
                evidence.append(piece)
    matched = [item.matched_value for item in judgments if item.status == STATUS_MATCHED and item.matched_value]
    return RequirementJudgment(
        category=category,
        requirement_index=index,
        requirement_label=label,
        requirement_key="+".join(keys) or None,
        is_required=is_required,
        status=status,
        confidence=min(item.confidence for item in judgments),
        explanation=lead + " ".join(item.explanation for item in ordered),
        matched_value=joiner.join(matched) if status == STATUS_MATCHED and matched else None,
        evidence=evidence,
        evidence_standard=standard,
    )


def field_words(description: str | None) -> set[str]:
    """The words naming the KIND of work a duration requirement asks about.

    "5+ years of professional software engineering experience" names software
    engineering; "5+ years of professional experience" names nothing at all.
    The difference decides whether total employment can answer the question
    outright — see `_judge_total_employment`.
    """
    return {token for token in _content_words(description) if is_generic(token)}


def asks_about_total_employment(description: str | None) -> bool:
    """Whether a duration requirement is a question about employment as such.

    True when every content word describes the kind of work rather than a
    thing worked with. False when a word remains that is neither a technology
    nor generic — "5+ years of fintech experience" asks something real that
    this engine has no way to check, and answering it with total employment
    would be inventing an answer.
    """
    return all(is_generic(token) for token in _content_words(description))


def _field_is_confirmed(fields: set[str], profile: ProfileIndex) -> bool:
    """Whether any recorded role identifies itself with the field asked about.

    Total employment is an UPPER BOUND on employment in any particular field,
    which is what makes the asymmetry in `_judge_total_employment` sound.
    """
    return not fields or bool(roles_in_field(fields, profile))


def _field_phrases(fields: set[str]) -> set[str]:
    """The most specific way the requirement names its field.

    `field_words` returns every 1-, 2- and 3-gram of generic words, so "software
    development" arrives as {"software", "development", "software
    development"}. Only the phrases no longer phrase contains are kept: a role
    has to say "software development", not merely "development".
    """
    return {
        phrase
        for phrase in fields
        if not any(other != phrase and f" {phrase} " in f" {other} " for other in fields)
    }


#: "IT" as a field of work. Written in capitals it is information technology;
#: lower-cased it is the pronoun, which is why `field_words` never keeps it and
#: "5+ years of IT experience" was answered by total employment alone — a
#: fifteen-year chef MATCHED it.
_IT = re.compile(r"(?<![A-Za-z])IT(?![A-Za-z])")
_INFORMATION_TECHNOLOGY = re.compile(r"information technology", re.IGNORECASE)


def _names_it(description: str | None) -> bool:
    return bool(_IT.search(description or ""))


def _it_roles(profile: ProfileIndex) -> list:
    """Roles whose title, or an affirmed clause, says IT or information technology."""
    roles = []
    for experience in profile.experiences:
        clauses: list[tuple[str, bool]] = []
        if experience.job_title:
            clauses.append((experience.job_title, demonstrates_title(experience.job_title)))
        clauses.extend(
            (clause, demonstrates_v5(experience.description, clause))
            for clause in sentences(experience.description)
        )
        if any(
            shows and (_IT.search(clause) or _INFORMATION_TECHNOLOGY.search(clause))
            for clause, shows in clauses
        ):
            roles.append(experience)
    return roles


def roles_in_field(fields: set[str], profile: ProfileIndex) -> list:
    """The roles that identify themselves with the kind of work named.

    matching_v5_1. The previous test intersected EVERY field word with the
    whole of a role's title and description, so a single generic word settled
    it: "Business Development Manager" confirmed "software development", and a
    career of mechanical-engineering roles that "led engineering reviews"
    confirmed "software engineering" — the case docs/matching.md says must be
    UNCERTAIN. It also read denials as confirmation ("No software engineering
    experience").

    A role now confirms the field when its title, or a description clause that
    `demonstrates_v5` affirms, says one of the field's phrases of two or more
    words, or one of its single words that names a discipline on its own
    ("software", "front", "web", "data") — never a word that only does so with
    a qualifier ("development", "engineering", "management"; see
    `_QUALIFIER_DEPENDENT`). Everything else about the asymmetry is unchanged.

    A stricter first attempt required the field's most specific phrase and
    was measured against matching_real_v1 before release: it turned c08's
    "4+ years building production web front-ends" UNCERTAIN, because that
    phrase is longer than the 3-grams `phrase_variants` produces. The
    qualifier-dependent list is what fixed the real defect without that cost.
    """
    forms = {form for form in (_field_form(phrase) for phrase in fields) if form}
    # A phrase confirms only if one of its words names a discipline on its own:
    # "end develop" (from "front end development") is two qualifier-dependent
    # words, and "end-to-end development of seasonal menus" confirmed it for a
    # head chef.
    phrases = {
        form
        for form in forms
        if any(word not in _FIELD_MODIFIERS for word in form.split())
        and any(word not in _QUALIFIER_DEPENDENT for word in form.split())
    }
    if not phrases:
        # The posting names ONLY a qualifier-dependent word — "5+ years of
        # engineering experience". Then that word IS the field, and a role
        # naming it ("Engineering Manager") is the evidence there is; refusing
        # it left the requirement UNCERTAIN for every profile, with an
        # explanation saying no role identified with engineering.
        multiword = {form for form in forms if " " in form}
        if multiword:
            # "front end development" is qualifier-dependent words only. Then
            # the field is the phrase as it LEADS — "front end", "front end
            # develop" — never a single word of it, and never a phrase from its
            # middle: "end" alone and "end develop" both confirmed it from
            # "end-to-end development of seasonal menus", while "front end" is
            # exactly what a "Frontend Engineer" says.
            longest = {
                form
                for form in multiword
                if not any(other != form and f" {form} " in f" {other} " for other in multiword)
            }
            phrases = {
                form
                for form in multiword
                if any(whole == form or whole.startswith(form + " ") for whole in longest)
            }
        else:
            phrases = {
                form
                for form in forms
                if form in _QUALIFIER_DEPENDENT and form not in _FIELD_MODIFIERS
            }
    if not phrases:
        return []
    confirming = []
    for experience in profile.experiences:
        clauses: list[tuple[str, bool]] = []
        if experience.job_title:
            clauses.append((experience.job_title, demonstrates_title(experience.job_title)))
        clauses.extend(
            (clause, demonstrates_v5(experience.description, clause))
            for clause in sentences(experience.description)
        )
        for clause, shows in clauses:
            if not shows:
                continue
            said = f" {_field_form(clause)} "
            if any(f" {phrase} " in said for phrase in phrases):
                confirming.append(experience)
                break
    return confirming


#: The forms one field of work is written in, reduced to one. A posting asks for
#: "software engineering" or "project management"; a role calls itself a
#: "Software Engineer" or a "Project Manager". Closed and short on purpose: it
#: equates the person with the discipline and nothing else, so it cannot make
#: "Business Development Manager" read as "software development".
_FIELD_FORMS = {
    "engineering": "engineer",
    "engineers": "engineer",
    "development": "develop",
    "developer": "develop",
    "developers": "develop",
    "developing": "develop",
    "programming": "program",
    "programmer": "program",
    "programmers": "program",
    "management": "manage",
    "manager": "manage",
    "managers": "manage",
    "managing": "manage",
    "administration": "administer",
    "administrator": "administer",
    "analysis": "analyse",
    "analyst": "analyse",
    "analytics": "analyse",
    "testing": "test",
    "tester": "test",
    "design": "design",
    "designer": "design",
    # Closed compounds of the two-word forms a posting writes: a "Frontend
    # Developer" is the "front end development" the posting asks for.
    "frontend": "front end",
    "backend": "back end",
    "fullstack": "full stack",
    "ends": "end",
    "frontend": "front end",
    "backend": "back end",
    "fullstack": "full stack",
}

#: Field words, in `_field_form`, that name a discipline only together with a
#: qualifier. Alone, each is shared by unrelated fields — "development" by
#: business and software, "engineering" by mechanical and software, "management"
#: by every one of them — so a role saying only this word has not identified
#: itself with the field the posting names. A phrase of two or more words that
#: contains one ("software development") is unaffected.
_QUALIFIER_DEPENDENT = frozenset(
    {
        "engineer",
        "develop",
        "manage",
        "administer",
        "analyse",
        "test",
        "design",
        "program",
        "build",
        "building",
        "production",
        "operations",
        "operation",
        "business",
        "technical",
        "technology",
        "professional",
        "support",
        "service",
        "services",
        "system",
        "systems",
        "solution",
        "solutions",
        "product",
        "industry",
        "research",
        "delivery",
        "lead",
        "leadership",
        # Half of a compound: "front end", "back end", "full stack" name a
        # discipline only whole ("end" alone is "end-to-end tests").
        "front",
        "back",
        "end",
        "full",
        "stack",
    }
)

#: Words that grade or scope a field rather than name one. Unlike the list
#: above they confirm NOTHING, even as a whole phrase: the first version let
#: them confirm, so "5+ years of enterprise software development" was confirmed
#: by a sales role that "grew enterprise accounts", "senior software
#: engineering" by any title with "Senior" in it, and "large scale software
#: development" by "at large scale".
_FIELD_MODIFIERS = frozenset(
    {
        "senior",
        "junior",
        "principal",
        "staff",
        "large",
        "scale",
        "enterprise",
        "commercial",
        "relevant",
        "related",
        "proven",
        "solid",
        "strong",
        "extensive",
        "significant",
        "practical",
        "hands",
        "modern",
        "complex",
        "high",
        "level",
        "paid",
        "total",
        "overall",
        "general",
        "real",
        "world",
        "recent",
        "direct",
    }
)

_FIELD_WORD = re.compile(r"[a-z0-9+#]+")


def _field_form(text: str | None) -> str:
    """Lower-cased words, each reduced through `_FIELD_FORMS`, space-joined."""
    return " ".join(
        _FIELD_FORMS.get(word, word) for word in _FIELD_WORD.findall((text or "").lower())
    )


def judge_experience(
    *,
    description: str,
    minimum_years: float | None,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    as_of: date,
    quotes: tuple[str, ...] = (),
) -> RequirementJudgment:
    """A requirement about having done something, for some length of time.

    Four paths, and which one runs is decided by what the requirement is ABOUT
    rather than by what words survive a stopword filter:

    1. **No stated minimum.** The requirement asks whether the work has been
       done at all, so it is judged exactly as a skill requirement is.
    2. **A minimum, and a technology resolves.** Duration is measured from
       paid roles whose own description demonstrates that technology — and
       from nothing else. A side project is not professional duration, and the
       way that stays true is that `employment_mentioning` cannot return one.
    3. **A minimum, and the requirement names only a kind of work.** Total
       employment answers it, subject to the upper-bound asymmetry below.
    4. **A minimum, and the subject cannot be resolved at all.** UNCERTAIN.
       Nothing is invented.

    Overlapping roles are merged, never summed: two concurrent engagements
    from 2020 to 2022 are two years, not four.
    """
    label = _label(description)
    standard = standard_for(description, *quotes)
    subjects, any_of = resolve_technology_subjects(description, profile)
    subject = subjects[0] if subjects else None

    # --- 1. no measurable duration -------------------------------------------
    if minimum_years is None:
        if len(subjects) > 1:
            return _combine_subjects(
                [
                    _judge_token(
                        token=token,
                        label=label,
                        category=CATEGORY_EXPERIENCE,
                        index=index,
                        is_required=is_required,
                        profile=profile,
                        standard=standard,
                    )
                    for token in subjects
                ],
                any_of=any_of,
                label=label,
                category=CATEGORY_EXPERIENCE,
                index=index,
                is_required=is_required,
                standard=standard,
            )
        if subject is not None:
            return _judge_token(
                token=subject,
                label=label,
                category=CATEGORY_EXPERIENCE,
                index=index,
                is_required=is_required,
                profile=profile,
                standard=standard,
            )
        return _judge_unmeasured_non_technology(
            description=description,
            label=label,
            index=index,
            is_required=is_required,
            profile=profile,
            standard=standard,
        )

    # --- 2. a technology, for a stated number of years ------------------------
    if len(subjects) > 1:
        return _combine_subjects(
            [
                _judge_technology_duration(
                    subject=token,
                    label=label,
                    minimum_years=minimum_years,
                    index=index,
                    is_required=is_required,
                    profile=profile,
                    as_of=as_of,
                    standard=standard,
                )
                for token in subjects
            ],
            any_of=any_of,
            label=label,
            category=CATEGORY_EXPERIENCE,
            index=index,
            is_required=is_required,
            standard=standard,
        )
    if subject is not None:
        return _judge_technology_duration(
            subject=subject,
            label=label,
            minimum_years=minimum_years,
            index=index,
            is_required=is_required,
            profile=profile,
            as_of=as_of,
            standard=standard,
        )

    # --- 3. a kind of work, for a stated number of years ----------------------
    if asks_about_total_employment(description):
        return _judge_total_employment(
            description=description,
            label=label,
            minimum_years=minimum_years,
            index=index,
            is_required=is_required,
            profile=profile,
            as_of=as_of,
            standard=standard,
        )

    # --- 4. unresolvable ------------------------------------------------------
    return RequirementJudgment(
        category=CATEGORY_EXPERIENCE,
        requirement_index=index,
        requirement_label=label,
        requirement_key=None,
        is_required=is_required,
        status=STATUS_UNCERTAIN,
        confidence=0.2,
        explanation=(
            "This requirement asks for a number of years of something this matcher cannot "
            "identify as a technology or as a kind of work, so it could not be checked "
            "against the profile."
        ),
        evidence_standard=standard,
    )


def _judge_technology_duration(
    *,
    subject: str,
    label: str,
    minimum_years: float,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    as_of: date,
    standard: str,
) -> RequirementJudgment:
    """How long the profile shows a named technology being used."""
    found = profile.evidence_for(subject)

    if not found.has_any:
        return RequirementJudgment(
            category=CATEGORY_EXPERIENCE,
            requirement_index=index,
            requirement_label=label,
            requirement_key=subject,
            is_required=is_required,
            status=STATUS_MISSING,
            confidence=0.9,
            explanation=(
                f"Nothing in this profile version mentions {subject}, so there are no years "
                f"of it to count. No qualifying evidence was found."
            ),
            evidence_standard=standard,
        )

    employment = profile.employment_mentioning(subject)
    if employment:
        spans = _spans_for(employment, as_of)
        cited = _employment_citations(profile, subject, employment)
        if spans:
            years = merge_years(spans)
            if years >= minimum_years:
                return RequirementJudgment(
                    category=CATEGORY_EXPERIENCE,
                    requirement_index=index,
                    requirement_label=label,
                    requirement_key=subject,
                    is_required=is_required,
                    status=STATUS_MATCHED,
                    confidence=0.9,
                    explanation=(
                        f"Paid roles whose descriptions show {subject} cover about {years:g} "
                        f"years, meeting the {minimum_years:g} the posting asks for. "
                        f"Overlapping positions are counted once."
                    ),
                    matched_value=subject,
                    evidence=cited,
                    evidence_standard=standard,
                )
            # matching_v5_1. Roles that NAME a technology are a lower bound on
            # time spent with it, not an upper bound — a role that never says
            # "Django" may still have been Django work. So when the profile
            # separately states enough years, a short attributed total
            # contradicts nothing and settles nothing: it is UNCERTAIN. Before
            # this, adding one corroborating role to a profile stating 4 years
            # turned a MATCHED into a MISSING.
            stated_skill = profile.skill_for(subject)
            stated = (
                float(stated_skill.years_of_experience)
                if stated_skill is not None and stated_skill.years_of_experience is not None
                else None
            )
            if stated is not None and stated >= minimum_years:
                return RequirementJudgment(
                    category=CATEGORY_EXPERIENCE,
                    requirement_index=index,
                    requirement_label=label,
                    requirement_key=subject,
                    is_required=is_required,
                    status=STATUS_UNCERTAIN,
                    confidence=0.4,
                    explanation=(
                        f"Paid roles whose descriptions show {subject} cover about {years:g} "
                        f"years, fewer than the {minimum_years:g} the posting asks for, while the "
                        f"profile states {stated:g} years of {subject}. A role that does not name "
                        f"{subject} may still have used it, so the two cannot be reconciled from "
                        f"this profile version alone."
                    ),
                    matched_value=subject,
                    evidence=cited,
                    evidence_standard=standard,
                )
            return RequirementJudgment(
                category=CATEGORY_EXPERIENCE,
                requirement_index=index,
                requirement_label=label,
                requirement_key=subject,
                is_required=is_required,
                status=STATUS_MISSING,
                confidence=0.8,
                explanation=(
                    f"Paid roles whose descriptions show {subject} cover about {years:g} years; "
                    f"the posting asks for {minimum_years:g}. The evidence counted is listed."
                ),
                evidence=cited,
                evidence_standard=standard,
            )

    # The profile's own statement of duration. Believed where the posting did
    # not ask for professional use — it is the candidate asserting a number,
    # which answers "how long have you used this" but not "show me the work".
    skill = profile.skill_for(subject)
    if skill is not None and skill.years_of_experience is not None:
        stated = float(skill.years_of_experience)
        if standard == STANDARD_ANY_EVIDENCE:
            cited = [graded(item, found.tier) for item in found.best()]
            if stated >= minimum_years:
                return RequirementJudgment(
                    category=CATEGORY_EXPERIENCE,
                    requirement_index=index,
                    requirement_label=label,
                    requirement_key=subject,
                    is_required=is_required,
                    status=STATUS_MATCHED,
                    confidence=0.8,
                    explanation=(
                        f"The profile states {stated:g} years of {subject}, which meets the "
                        f"{minimum_years:g} the posting asks for."
                    ),
                    matched_value=subject,
                    evidence=cited,
                    evidence_standard=standard,
                )
            return RequirementJudgment(
                category=CATEGORY_EXPERIENCE,
                requirement_index=index,
                requirement_label=label,
                requirement_key=subject,
                is_required=is_required,
                status=STATUS_MISSING,
                confidence=0.8,
                explanation=(
                    f"The profile states {stated:g} years of {subject}; the posting asks for "
                    f"{minimum_years:g}."
                ),
                evidence=cited,
                evidence_standard=standard,
            )

    tier = found.tier
    return RequirementJudgment(
        category=CATEGORY_EXPERIENCE,
        requirement_index=index,
        requirement_label=label,
        requirement_key=subject,
        is_required=is_required,
        status=STATUS_UNCERTAIN,
        confidence=0.3,
        explanation=(
            f"The profile {_TIER_PHRASE[tier].format(token=subject)}, but no dated paid role "
            f"demonstrates it, so the {minimum_years:g} years the posting asks for can be "
            f"neither confirmed nor ruled out. Project work is not counted as professional "
            f"duration."
        ),
        matched_value=subject,
        evidence=[graded(item, tier) for item in found.best()[:5]],
        evidence_standard=standard,
    )


def _judge_total_employment(
    *,
    description: str,
    label: str,
    minimum_years: float,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    as_of: date,
    standard: str,
) -> RequirementJudgment:
    """How long the profile shows professional employment, in total.

    The asymmetry here is the point, and it is sound rather than merely
    cautious: total employment is an upper bound on employment in any
    particular field. A total below the bar settles the question in the
    negative whatever the field, while a total above it only settles it if the
    profile shows the years were spent on the kind of work the posting names.
    """
    if not profile.experiences:
        return RequirementJudgment(
            category=CATEGORY_EXPERIENCE,
            requirement_index=index,
            requirement_label=label,
            requirement_key=None,
            is_required=is_required,
            status=STATUS_MISSING,
            confidence=0.9,
            explanation=(
                "This profile version records no employment, so no qualifying evidence "
                "was found."
            ),
            evidence_standard=standard,
        )

    spans = _spans_for(profile.experiences, as_of)
    cited = [
        graded(profile.experience_evidence(item), TIER_EMPLOYMENT_DESCRIBED)
        for item in profile.experiences[:5]
    ]

    if not spans:
        return RequirementJudgment(
            category=CATEGORY_EXPERIENCE,
            requirement_index=index,
            requirement_label=label,
            requirement_key=None,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.3,
            explanation=(
                f"This profile version records employment, but none of it carries dates that "
                f"can be measured, so the {minimum_years:g} years the posting asks for could "
                f"not be checked."
            ),
            evidence=cited,
            evidence_standard=standard,
        )

    years = merge_years(spans)
    fields = field_words(description)

    if years < minimum_years:
        return RequirementJudgment(
            category=CATEGORY_EXPERIENCE,
            requirement_index=index,
            requirement_label=label,
            requirement_key=None,
            is_required=is_required,
            status=STATUS_MISSING,
            confidence=0.85,
            explanation=(
                f"All dated employment in this profile version covers about {years:g} years, "
                f"which is less than the {minimum_years:g} the posting asks for. Overlapping "
                f"positions are counted once."
            ),
            evidence=cited,
            evidence_standard=standard,
        )

    # "it" is kept by `field_words` as a generic word, and matched against a
    # role's text it confirmed the field from the pronoun ("kept it spotless").
    # It is taken out here and answered only by `_it_roles`, which reads the
    # capitalised IT or "information technology".
    asks_it = "it" in fields or _names_it(description)
    fields = {phrase for phrase in fields if "it" not in phrase.split()}
    if asks_it:
        # IT narrows the field by itself; "IT systems" and "IT support" are IT
        # work, and the qualifier-dependent word beside IT ("systems") left no
        # phrase at all — "IT Systems Administrator" was UNCERTAIN, with an
        # explanation saying no role identified with systems.
        in_field = _it_roles(profile)
        named = "IT"
    elif fields:
        in_field = roles_in_field(fields, profile)
        named = ", ".join(sorted(_field_phrases(fields)))
    else:
        in_field = list(profile.experiences)
        named = ""
    if named and not in_field:
        return RequirementJudgment(
            category=CATEGORY_EXPERIENCE,
            requirement_index=index,
            requirement_label=label,
            requirement_key=None,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.4,
            explanation=(
                f"Dated employment covers about {years:g} years, which is enough in total, but "
                f"no role in this profile version identifies itself with {named}. Whether "
                f"those years were spent on that kind of work is not something the profile "
                f"states."
            ),
            evidence=cited,
            evidence_standard=standard,
        )

    # Deliberately NOT measured on the in-field roles alone. That was tried for
    # matching_v5_1 and measured against matching_real_v1: human reviewers
    # count a "Backend Engineer" role as professional software employment
    # whether or not its description says "software engineering", and the
    # stricter count turned three of their MATCHED verdicts UNCERTAIN (c01, c02,
    # c08) while fixing none. The residual over-credit — one short in-field role
    # beside a long career in another field crediting every year — is recorded
    # as a known limitation in docs/matching.md rather than traded for those.
    return RequirementJudgment(
        category=CATEGORY_EXPERIENCE,
        requirement_index=index,
        requirement_label=label,
        requirement_key=None,
        is_required=is_required,
        status=STATUS_MATCHED,
        confidence=0.85,
        explanation=(
            f"Dated employment in this profile version covers about {years:g} years of "
            f"professional experience, meeting the {minimum_years:g} the posting asks for. "
            f"Overlapping positions are counted once."
        ),
        matched_value="employment",
        evidence=cited,
        evidence_standard=standard,
    )


def _judge_unmeasured_non_technology(
    *,
    description: str,
    label: str,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    standard: str,
) -> RequirementJudgment:
    """A requirement with no duration that names no technology.

    "Internship or placement experience", "Start-up experience". There is no
    number to measure and no technology to look up, so the only honest thing
    available is to ask whether the profile says any of those words anywhere —
    which is a weak test, and is why the verdict it produces is capped at the
    evidence tier that was actually found rather than asserted outright.
    """
    # The short-subject guard applies here too: without it "R&D experience" or
    # "C-level stakeholder management" (no duration, so no technology subject)
    # still looked up the candidate's R or C skill and came back MATCHED.
    subjects = {
        token
        for token in _content_words(description)
        if not is_generic(token) and _stands_as_a_subject(token, description or "")
    }
    for token in _ordered(subjects):
        # Surface form first: it is the stricter test, and a requirement whose
        # own word appears in the profile needs no help.
        found = profile.evidence_for(token)
        key, display = token, None
        if not found.has_any:
            # Then, and only here, the work-action inflection table. "leading"
            # is answered by "Lead four engineers"; a token the table has never
            # heard of gets nothing back, which is almost every token. See
            # morphology.py for why this is a hand-written list of verbs rather
            # than a stemmer, and why no technology requirement can reach it.
            widened = profile.action_evidence_for(token)
            if widened.has_any:
                found, key, display = widened, action_lemma(token) or token, token
        if found.has_any:
            return _judge_token(
                token=key,
                label=label,
                category=CATEGORY_EXPERIENCE,
                index=index,
                is_required=is_required,
                profile=profile,
                standard=standard,
                found=found,
                display=display,
            )

    return RequirementJudgment(
        category=CATEGORY_EXPERIENCE,
        requirement_index=index,
        requirement_label=label,
        requirement_key=None,
        is_required=is_required,
        status=STATUS_MISSING,
        confidence=0.8,
        explanation="Nothing in this profile version corresponds to this requirement.",
        evidence_standard=standard,
    )


# --- location -------------------------------------------------------------------


def judge_location(
    *,
    description: str,
    index: int,
    is_required: bool,
    profile: ProfileIndex,
    posting: "JobPosting | None" = None,
) -> RequirementJudgment:
    """A requirement about WHERE the job is.

    matching_v4 answers two questions and declines every other: is the
    candidate in the city the posting named, and failing that, are they in the
    same country. City-or-country equality is what reproduces the human
    judgments in the real-data benchmark; reading the "within two hours of CET"
    clause those postings also contain would have flipped two correct verdicts
    to wrong ones, so the timezone half of such a requirement is deliberately
    not interpreted.

    **MISSING is never returned.** Not as caution — as accuracy. The profile
    schema has no residence field and no relocation field; the only geographic
    signal is where past JOBS were. "Their last job was in Dublin" is not
    "they cannot work in Berlin", and a matcher that said MISSING would be
    asserting something the profile never states. Out-of-area is UNCERTAIN,
    which is what a person reading the same two documents concluded.

    The evidence rule is strict: a MATCHED must cite the employment row that
    placed the candidate there. A verdict that cannot be grounded in a profile
    row is not allowed to be MATCHED, and the posting is never cited as if it
    were evidence about the candidate.
    """
    label = _label(description)

    # --- what the posting asks for -------------------------------------------
    required = places_in_text(description)
    source = "the requirement"
    if required.is_empty and posting is not None:
        # The requirement named no place this vocabulary knows. The posting's
        # own location field often does — "Berlin, Germany (hybrid)" — and it
        # is write-once, so reading it keeps the result reproducible.
        from_field = parse_location_field(posting.location_raw)
        if from_field is not None:
            required = RequiredPlaces(
                cities=frozenset({from_field.city} if from_field.city else set()),
                countries=frozenset({from_field.country}),
            )
            source = "the posting's location"

    if required.is_empty:
        return _location_verdict(
            label=label,
            index=index,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.2,
            key=None,
            explanation=(
                "This requirement is about where the job is, but it names no place this "
                "matcher recognizes, so it could not be checked against the profile."
            ),
        )

    if required.conflicting:
        return _location_verdict(
            label=label,
            index=index,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.2,
            key=None,
            explanation=(
                "This requirement names places in more than one country. Which one the "
                "posting means is not something this matcher decides."
            ),
        )

    # --- postings the engine declines to judge on location --------------------
    if posting is not None and posting.workplace_type == WORKPLACE_REMOTE:
        return _location_verdict(
            label=label,
            index=index,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.2,
            key=None,
            explanation=(
                "This posting is remote, so where the candidate is may not be the "
                "question the requirement is really asking. Remote eligibility is not "
                "evaluated by this matcher."
            ),
        )

    # --- where the candidate's employment places them -------------------------
    candidate = profile.current_location()
    if candidate is None:
        return _location_verdict(
            label=label,
            index=index,
            is_required=is_required,
            status=STATUS_UNCERTAIN,
            confidence=0.3,
            key=_required_key(required),
            explanation=(
                "This profile version records no employment location that could be "
                "read, or records several that disagree, so where this candidate is "
                "cannot be established. A profile does not state where someone lives "
                "or is willing to move."
            ),
        )

    cited = [profile.location_evidence(item) for item in profile.experience_at(candidate)[:3]]

    if candidate.city is not None and candidate.city in required.cities:
        return _location_verdict(
            label=label,
            index=index,
            is_required=is_required,
            status=STATUS_MATCHED,
            confidence=0.9,
            key=candidate.city,
            matched_value=candidate.city,
            explanation=(
                f"Employment in this profile version is in {_title(candidate.city)}, which is "
                f"the place {source} names."
            ),
            evidence=cited,
        )

    if candidate.country in required.countries:
        where = _title(candidate.city) if candidate.city else _title(candidate.country)
        return _location_verdict(
            label=label,
            index=index,
            is_required=is_required,
            status=STATUS_MATCHED,
            confidence=0.75,
            key=candidate.country,
            matched_value=candidate.country,
            explanation=(
                f"Employment in this profile version is in {where}, which is in "
                f"{_title(candidate.country)} — the country {source} names. The city differs."
            ),
            evidence=cited,
        )

    return _location_verdict(
        label=label,
        index=index,
        is_required=is_required,
        status=STATUS_UNCERTAIN,
        confidence=0.4,
        key=_required_key(required),
        explanation=(
            f"Employment in this profile version is in {_title(candidate.country)}; "
            f"{source} names {_places_phrase(required)}. A profile does not state where "
            f"someone is willing to live, so this is open rather than a refusal."
        ),
        evidence=cited,
    )


def _location_verdict(
    *,
    label: str,
    index: int,
    is_required: bool,
    status: str,
    confidence: float,
    key: str | None,
    explanation: str,
    matched_value: str | None = None,
    evidence: list[Evidence] | None = None,
) -> RequirementJudgment:
    """One location verdict, with its evidence graded consistently.

    Evidence is stamped `employment_described` because that is what it is — a
    paid role's own record of where it was. A MATCHED grades its citations as
    qualifying; an UNCERTAIN keeps them and marks them as not, so the result
    shows what was weighed rather than an empty list.
    """
    items = evidence or []
    graded_items = [graded(item, TIER_EMPLOYMENT_DESCRIBED) for item in items]
    if status == STATUS_MATCHED and not graded_items:
        # The evidence rule, enforced rather than trusted: a location MATCHED
        # that cannot point at the employment it was read from is not allowed
        # to be a MATCHED at all.
        status = STATUS_UNCERTAIN
        confidence = 0.3
        matched_value = None
        explanation = (
            "Where this candidate is could not be traced to a specific employment "
            "record, so this requirement could not be settled."
        )

    return RequirementJudgment(
        category=CATEGORY_QUALIFICATION,
        requirement_index=index,
        requirement_label=label,
        requirement_key=key,
        is_required=is_required,
        status=status,
        confidence=confidence,
        explanation=explanation,
        matched_value=matched_value,
        evidence=graded_items,
        evidence_standard=STANDARD_ANY_EVIDENCE,
    )


def _required_key(required: RequiredPlaces) -> str | None:
    """A stable key for what the posting asked for, for later grouping."""
    if required.cities:
        return sorted(required.cities)[0]
    if required.countries:
        return sorted(required.countries)[0]
    return None


def _places_phrase(required: RequiredPlaces) -> str:
    if required.cities:
        return ", ".join(_title(city) for city in sorted(required.cities))
    return ", ".join(_title(country) for country in sorted(required.countries))


def _title(name: str) -> str:
    """"united kingdom" -> "United Kingdom", for an explanation a person reads."""
    return " ".join(part.capitalize() for part in name.split())


# --- qualifications -------------------------------------------------------------


def judge_qualification(
    *, description: str, index: int, is_required: bool
) -> RequirementJudgment:
    """A free-text requirement: work authorization, travel, on-call, clearance.

    Recorded rather than dropped. V1 has no profile field that could answer any
    of these, and silently leaving them out would make coverage look better
    than it is by shrinking the denominator — the one way a score here could
    mislead without any individual verdict being wrong.
    """
    return RequirementJudgment(
        category=CATEGORY_QUALIFICATION,
        requirement_index=index,
        requirement_label=_label(description),
        requirement_key=None,
        is_required=is_required,
        status=STATUS_UNCERTAIN,
        confidence=0.0,
        explanation=(
            "This matcher does not evaluate free-text qualifications such as work authorization, "
            "travel or clearance: the profile holds no field that could answer them. This needs "
            "a person to read."
        ),
    )


__all__ = [
    "RequirementJudgment",
    "degree_level",
    "field_of_study_from_text",
    "judge_certification",
    "judge_education",
    "judge_experience",
    "judge_qualification",
    "judge_location",
    "judge_token_requirement",
    "merge_years",
    "asks_about_total_employment",
    "field_words",
    "resolve_technology_subject",
    "CATEGORY_SKILL",
    "CATEGORY_TECHNOLOGY",
]
