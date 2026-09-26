"""How strong a piece of evidence is, and what a requirement demands of it.

matching_v1 sorted evidence into two bins — "declared in a field whose purpose
is to declare it" and "everything else" — and the real-data benchmark showed
both bins are wrong at the edges. Six years of daily React, described in a
current paid role but absent from the skills list, came back UNCERTAIN. A
graduate's skills-list entry for Kubernetes, with nothing anywhere in the
profile touching it, came back MATCHED at 0.95 confidence against a posting
that said "in production, not just in a course".

Both are the same defect: a binary split where the domain needs a grade.

    DECLARED_CORROBORATED   the profile declares it AND a paid role
                            independently demonstrates it. The strongest thing
                            a profile can say.
    EMPLOYMENT_DESCRIBED    a paid role demonstrates it, and the skills list
                            does not mention it. Extremely common in real
                            profiles, and evidence of doing the work.
    DECLARED_ONLY           declared, and nothing else in the profile touches
                            it. A claim. True far more often than not, and
                            not the same thing as having shown it.
    MENTIONED               named only in a project, or in a clause that says
                            it was evaluated rather than used. Below all three
                            of the tiers above, and kept distinct because
                            collapsing it into DECLARED_ONLY would let a
                            cancelled proof-of-concept satisfy a production
                            requirement.

Nothing here reads a model, scores a similarity or infers a corroboration that
the profile does not state. A tier is a function of which profile tables
mention a token and, for prose, of whether the clause containing it says the
work was done — all of it inspectable, all of it explainable in one sentence.

**What a requirement demands** is read from the requirement's own words,
including the verified quote the job domain captured for it. A posting that
says "Kubernetes" and one that says "Kubernetes in production, not just in a
course" are asking different questions, and answering both with the same bar
is what produced the benchmark's false matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.matching.negation import negation_fires
from app.services.matching.vocabulary import is_non_doing

# --- the tiers ----------------------------------------------------------------
#
# Ordered strongest first. The integer is for comparison only and is never
# stored; `TIER_*` strings are what reach the database, so a stored result
# stays readable if the ordering is ever revised.

TIER_DECLARED_CORROBORATED = "declared_corroborated"
TIER_EMPLOYMENT_DESCRIBED = "employment_described"
TIER_DECLARED_ONLY = "declared_only"
TIER_MENTIONED = "mentioned"

TIERS = (
    TIER_DECLARED_CORROBORATED,
    TIER_EMPLOYMENT_DESCRIBED,
    TIER_DECLARED_ONLY,
    TIER_MENTIONED,
)

_RANK = {tier: index for index, tier in enumerate(TIERS)}

#: What each tier means, in the words the API and UI use. Held here so the
#: explanation cannot drift from the rule that assigns it.
TIER_DESCRIPTIONS = {
    TIER_DECLARED_CORROBORATED: (
        "listed as a skill and independently demonstrated in paid work"
    ),
    TIER_EMPLOYMENT_DESCRIBED: "demonstrated in the description of a paid role",
    TIER_DECLARED_ONLY: "listed in the profile, with nothing else corroborating it",
    TIER_MENTIONED: (
        "named only in a project, or in a sentence describing something not actually done"
    ),
}


def stronger(left: str, right: str) -> str:
    return left if _RANK[left] <= _RANK[right] else right


# --- what a requirement demands ------------------------------------------------

#: A requirement asking for the work to have been DONE, in a job, for money.
#: Read from the requirement's own wording — never assumed, because assuming it
#: would make every "nice to have: Redis" unsatisfiable by a skills list.
_DEMANDS_EMPLOYMENT = re.compile(
    r"\b("
    r"in\s+production|production|professional(?:ly)?|commercial(?:ly)?|"
    r"industry|enterprise|hands[\s-]?on|real[\s-]?world|at\s+scale|"
    r"demonstrable|demonstrated|proven|track\s+record|shipped|"
    r"not\s+just\s+in\s+a\s+course"
    r")\b",
    re.IGNORECASE,
)

#: What a requirement will accept.
STANDARD_ANY_EVIDENCE = "any_evidence"
STANDARD_PROFESSIONAL_USE = "professional_use"

STANDARDS = (STANDARD_ANY_EVIDENCE, STANDARD_PROFESSIONAL_USE)

STANDARD_DESCRIPTIONS = {
    STANDARD_ANY_EVIDENCE: (
        "The posting asks for the skill without saying how it must have been acquired, "
        "so a profile that lists it answers the question."
    ),
    STANDARD_PROFESSIONAL_USE: (
        "The posting asks for professional or production use, so a skills-list entry on "
        "its own does not answer it — the profile has to show the work."
    ),
}


def standard_for(*quotes: str | None) -> str:
    """The evidence standard a requirement sets, read from its own words.

    Every string the posting used for this requirement is considered: the
    requirement text the structurer produced, and the verified quotes it was
    taken from. The quote is what the posting actually said, and "Kubernetes in
    production, not just in a course" sets a bar that the extracted name
    "Kubernetes" does not carry on its own.
    """
    for quote in quotes:
        if quote and _DEMANDS_EMPLOYMENT.search(quote):
            return STANDARD_PROFESSIONAL_USE
    return STANDARD_ANY_EVIDENCE


def satisfies(tier: str | None, standard: str) -> bool:
    """Whether evidence of this tier answers a requirement of this standard."""
    if tier is None:
        return False
    if standard == STANDARD_PROFESSIONAL_USE:
        return tier in (TIER_DECLARED_CORROBORATED, TIER_EMPLOYMENT_DESCRIBED)
    return tier in (
        TIER_DECLARED_CORROBORATED,
        TIER_EMPLOYMENT_DESCRIBED,
        TIER_DECLARED_ONLY,
    )


def why_qualifies(tier: str, standard: str) -> str:
    """One sentence saying why this evidence answers the requirement."""
    if tier == TIER_DECLARED_CORROBORATED:
        return "The profile lists this and paid work demonstrates it."
    if tier == TIER_EMPLOYMENT_DESCRIBED:
        return "A paid role's description demonstrates this directly."
    if tier == TIER_DECLARED_ONLY:
        return (
            "The profile lists this, and the posting asks for the skill without "
            "requiring professional use."
        )
    return "This evidence was counted toward the requirement."


def why_not(tier: str, standard: str) -> str:
    """One sentence saying why this evidence does NOT answer the requirement.

    Stored alongside the evidence rather than discarded. A result that shows
    only what counted cannot explain a verdict of UNCERTAIN, and "we found
    this and it was not enough" is the most useful thing this engine can tell
    someone about a gap.
    """
    if tier == TIER_DECLARED_ONLY and standard == STANDARD_PROFESSIONAL_USE:
        return (
            "The profile lists this but shows no paid work using it, and the posting "
            "asks for professional or production experience."
        )
    if tier == TIER_MENTIONED:
        return (
            "This appears only in a project, or in a sentence describing work that was "
            "not actually carried out, so it does not show the skill was used."
        )
    return "This evidence does not meet what the posting asks for."


# --- a graded body of evidence --------------------------------------------------


#: Where the work an item describes was done, when the profile says.
SETTING_EMPLOYMENT = "employment"
SETTING_PROJECT = "project"

SETTINGS = (SETTING_EMPLOYMENT, SETTING_PROJECT)


@dataclass(frozen=True)
class Evidence:
    """One profile-version row, as justification for a judgment."""

    source_type: str
    source_id: int | None
    label: str
    detail: str | None = None
    #: How strong this particular item is. Set when the item is filed into a
    #: token's evidence; None for evidence attached by a rule that does not
    #: grade (education, certifications), where the tier would be noise.
    tier: str | None = None
    #: The clause the token was found in, when it came from prose. Quoted so a
    #: reader can judge the claim rather than trusting the tier.
    quote: str | None = None
    #: Where the work happened, when the profile says — matching_v6 Phase 1.
    #: Lets a later rule ask "was this paid?" without re-deriving the answer
    #: from which list the item happens to be filed in. None where the profile
    #: says nothing about setting, which is every skills-list row: a skill
    #: entry asserts the skill, not where it was used.
    setting: str | None = None


@dataclass
class TokenEvidence:
    """Everything the profile says about one canonical token, graded.

    The three lists are the raw material; `tier` is the reading of them. Kept
    separate so a rule can ask for exactly the kind of evidence it needs —
    experience duration wants `employment`, and nothing else will do.
    """

    #: Skill rows and project-technology rows: the profile asserting it.
    declarations: list[Evidence] = field(default_factory=list)
    #: Paid roles whose description demonstrates it.
    employment: list[Evidence] = field(default_factory=list)
    #: Projects, and clauses that say the work was not done.
    mentions: list[Evidence] = field(default_factory=list)

    # --- matching_v6 Phase 1: what the three lists above cannot hold ----------
    #
    # Both are ADDITIONS. `tier`, `has_any`, `best()` and `all()` do not read
    # them, so every verdict this engine reaches is the verdict it reached
    # before they existed. They are here so that a later policy — and the
    # explanation a user reads — can tell apart arrangements the four-way tier
    # flattens into one value. Nothing in matching_v5's behaviour depends on
    # them, and nothing may be added that makes it depend on them without the
    # ALGORITHM_VERSION bump that a behaviour change requires.

    #: Affirmative, non-employment description of the work being done: project
    #: prose that describes using the technology. Before Phase 1 this was
    #: DISCARDED whenever the same project also declared the technology (see
    #: `ProfileIndex._index_projects`), so a project that both listed Docker
    #: and narrated deploying with it was indistinguishable from one that
    #: listed Docker and said nothing at all.
    #:
    #: Deliberately not `mentions`: that list already means "named in a
    #: project, or named in a clause saying the work was not done", and
    #: merging the two would let a cancelled proof-of-concept read as
    #: corroboration — the exact collapse this module's tiers exist to
    #: prevent.
    corroborations: list[Evidence] = field(default_factory=list)

    #: Clauses that explicitly deny, abandon or refute the work: "evaluated
    #: Terraform but deployed by hand", "the Redis layer was never built".
    #:
    #: Filed here IN ADDITION to `mentions`, never instead of it. A clause that
    #: is a disclaimer is still a mention, and moving it would change `tier`
    #: for every profile that contains one. The duplication is the price of
    #: leaving the projection byte-identical.
    disclaimers: list[Evidence] = field(default_factory=list)

    @property
    def tier(self) -> str | None:
        if self.declarations and self.employment:
            return TIER_DECLARED_CORROBORATED
        if self.employment:
            return TIER_EMPLOYMENT_DESCRIBED
        if self.declarations:
            return TIER_DECLARED_ONLY
        if self.mentions:
            return TIER_MENTIONED
        return None

    @property
    def has_any(self) -> bool:
        return bool(self.declarations or self.employment or self.mentions)

    def best(self) -> list[Evidence]:
        """The evidence a verdict should cite, strongest first.

        Corroborated evidence leads with the employment that corroborates it,
        not with the skill row: "Backend Engineer at Harborline — built Django
        REST services" tells a reader more than "Skill: Django", and the
        benchmark scored citing the latter as an evidence error.
        """
        if self.declarations and self.employment:
            return [*self.employment, *self.declarations]
        if self.employment:
            return list(self.employment)
        if self.declarations:
            return list(self.declarations)
        return list(self.mentions)

    def all(self) -> list[Evidence]:
        return [*self.employment, *self.declarations, *self.mentions]


def graded(evidence: Evidence, tier: str) -> Evidence:
    """The same evidence, stamped with the tier it was counted at."""
    return Evidence(
        source_type=evidence.source_type,
        source_id=evidence.source_id,
        label=evidence.label,
        detail=evidence.detail,
        tier=tier,
        quote=evidence.quote,
        # Carried, not dropped: a cited item that lost its setting would make
        # "was this paid work?" unanswerable from the verdict's own evidence.
        setting=evidence.setting,
    )


def demonstrates(text: str | None, sentence: str) -> bool:
    """Whether a clause shows the work was done rather than contemplated.

    A thin wrapper over `vocabulary.is_non_doing`, kept here so the tiering
    rule and its one exception read together.
    """
    return not is_non_doing(sentence)


def demonstrates_v5(text: str | None, sentence: str) -> bool:
    """`demonstrates`, plus explicit plain-English negation.

    Matching V5 step 3: `vocabulary.is_non_doing` only ever recognises a
    curated CONTENT word (`evaluated`, `deprecated`, `familiar`, ...) —
    `NON_DOING_MARKERS` itself is untouched, on purpose, since the words it
    is missing are function words (`not`, `never`, `no`), and a bare negator
    is not safe to add to a flat list the way a content word is (see
    `negation.py`'s module docstring for why). This composes the two as
    separate, independent checks rather than one: a caller that wants only
    the marker-based question keeps calling `demonstrates`; this is the new,
    additional question, layered on top rather than replacing it.
    """
    return demonstrates(text, sentence) and not negation_fires(sentence)


def demonstrates_title(title: str | None) -> bool:
    """Whether a job title should be read as affirming the technologies it
    names.

    Matching V5 step 4. Every one of the ten `job_title` non_doing_v1
    sentence cases expects this to be `True` — there is no case in that
    dataset where a bare title should read as non-doing via an ordinary
    NON_DOING_MARKERS word, because a title's grammar does not carry a
    marker's caveat meaning: "Retired Principal Engineer" does not mean
    "this was merely evaluated" the way "Retired the team's homegrown
    scheduler" does. This deliberately ignores `vocabulary.is_non_doing`
    entirely for title text and defers only to `negation.negation_fires`,
    which CAN still fire on a title — a hypothetical "Kubernetes
    Administrator (Never Certified)" should still read as weakened.

    Kept separate from `demonstrates`/`demonstrates_v5` rather than a
    parameter on either, so a title clause and a description clause can
    never be confused for one another at a call site — see
    `profile_index.py`'s `_index_experience_clause`, the one place this is
    called.
    """
    if not title:
        return True
    return not negation_fires(title)


__all__ = [
    "Evidence",
    "TokenEvidence",
    "SETTINGS",
    "SETTING_EMPLOYMENT",
    "SETTING_PROJECT",
    "TIERS",
    "TIER_DECLARED_CORROBORATED",
    "TIER_DECLARED_ONLY",
    "TIER_EMPLOYMENT_DESCRIBED",
    "TIER_MENTIONED",
    "TIER_DESCRIPTIONS",
    "STANDARDS",
    "STANDARD_ANY_EVIDENCE",
    "STANDARD_PROFESSIONAL_USE",
    "STANDARD_DESCRIPTIONS",
    "demonstrates_title",
    "demonstrates_v5",
    "graded",
    "satisfies",
    "standard_for",
    "stronger",
    "why_not",
    "why_qualifies",
]
