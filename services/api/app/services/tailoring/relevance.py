"""Deciding what a posting is actually asking for, and what answers it.

Split out of `generation.py` because it is a different question with a
different failure mode. Generation asks "what should the document say"; this
module asks "which of the candidate's true sentences speak to this job". Getting
the first wrong produces prose nobody wants to read. Getting the second wrong
produces a resume that leads with the office move — which is what the previous
implementation did, and why this file exists.

That implementation scored a sentence by counting how many of the posting's
words it reused, over a vocabulary built by pouring every requirement into one
set. Against a real posting that set was 75% noise:

    ACTUAL SIGNAL  python django postgresql docker kubernetes redis aws
    STOPWORDS      be in must of or the to with within
    ELIGIBILITY    authorised based berlin cet eu hours legally two work
    FILLER         building certified developer engineering experience
                   production professional services software years

Counting those equally had consequences that were not subtle. A Kafka bullet was
promoted to the top of a resume because "18 hours" matched "within two hours of
CET" — a commute policy deciding which achievement leads. A cost-cutting line
outranked the candidate's flagship data-pipeline work on the strength of the
word "services".

So the model here is not a bag of words. It is:

  * **Requirements stay separate and keep their kind.** A named skill is not the
    same kind of thing as a sentence about years, and a sentence about where you
    may legally live is not a third of the same thing again.

  * **Named things are never stopworded.** "Go" is a two-letter word and a
    required language. A skill, technology or certification NAME is signal by
    definition, whatever it looks like.

  * **Free text contributes only its distinctive words.** Stopwords go, and so
    does the filler every posting and every resume share — "experience",
    "production", "services". A word appearing in all candidates discriminates
    between none of them.

  * **Some requirements cannot be answered by evidence at all.** The right to
    work in a country and the willingness to sit in an office are true
    requirements, and no bullet on a resume is evidence about either. They score
    nothing, and a document never claims to target them.

Weighting is by kind, not by frequency: a required skill outranks a preferred
one, and both outrank a content word from a sentence. Distinct hits, never
occurrences — a clause naming Kubernetes four times is not four times as
relevant, and counting occurrences ranks keyword stuffing above substance.

Nothing here reads the profile's text as authority and nothing here relaxes
anything. Selection cannot make a document true; it can only decide which true
things are shown. Grounding is unchanged and never consults this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.job_candidate import JobCandidate

# --- kinds of requirement ---------------------------------------------------------
#
# The kind decides two things: how heavily the requirement's words weigh in
# ranking, and — more importantly — WHERE a document has to mention something
# before it may claim to have targeted it.

KIND_SKILL = "skill"
KIND_TECHNOLOGY = "technology"
KIND_CERTIFICATION = "certification"
KIND_EXPERIENCE = "experience"
KIND_EDUCATION = "education"
KIND_QUALIFICATION = "qualification"
KIND_RESPONSIBILITY = "responsibility"

#: A requirement naming one thing. Its label IS its term, and it is never
#: stopworded or filtered: a named skill is signal whatever it is spelled like.
_NAMED = frozenset({KIND_SKILL, KIND_TECHNOLOGY, KIND_CERTIFICATION})

#: Where a document must mention something before it may say it targeted the
#: requirement.
#:
#: "prose" means the experience and project bullets, the summary and the job
#: titles — what the document ASSERTS about what the candidate did. "listed"
#: additionally includes the skills groups, project technologies and
#: certification names — what the document LISTS.
#:
#: The distinction carries this module's sharpest judgement. "3+ years building
#: production services with Django" is a claim about employment, and a skills
#: line reading "Django" does not evidence it — a resume saying otherwise tells
#: the reader it has three years of Django on it when it has a word in a list.
#: So an experience requirement is answered by prose only, while the SKILL
#: requirement "Django" from the same posting is answered by the skills line and
#: is claimed there, honestly and separately.
SCOPE_LISTED = "listed"
SCOPE_PROSE = "prose"
SCOPE_EDUCATION = "education"

_EVIDENCE_SCOPE = {
    KIND_SKILL: SCOPE_LISTED,
    KIND_TECHNOLOGY: SCOPE_LISTED,
    KIND_CERTIFICATION: SCOPE_LISTED,
    KIND_EXPERIENCE: SCOPE_PROSE,
    KIND_RESPONSIBILITY: SCOPE_PROSE,
    KIND_QUALIFICATION: SCOPE_PROSE,
    KIND_EDUCATION: SCOPE_EDUCATION,
}

# --- weights ----------------------------------------------------------------------
#
# Deliberately coarse. These separate kinds of evidence, they do not fine-tune a
# ranking: any set of numbers with this ordering produces the same document, and
# a reader should be able to predict the output from the ordering alone.

WEIGHT_REQUIRED_NAME = 4.0
WEIGHT_PREFERRED_NAME = 2.0
WEIGHT_REQUIRED_TEXT = 1.5
WEIGHT_PREFERRED_TEXT = 1.0

#: Added to a term already carried by a requirement a prior match could not
#: evidence. A nudge in ORDER and nothing else: an emphasised requirement is
#: still missing, and no amount of emphasis lets a document claim it.
WEIGHT_EMPHASIS = 1.0


# --- tokens -----------------------------------------------------------------------

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+.#/_-]*")

#: Trailing punctuation the pattern over-matches, stripped so a technology at
#: the end of a sentence is the same token as one in the middle. Plus and hash
#: survive: "C++" and "C#" end that way on purpose. The same adjustment, for the
#: same reason, as `grounding._TRAILING`.
_TRAILING = "._-/"


def tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        token
        for match in _TOKEN.finditer(value)
        if (token := match.group(0).casefold().rstrip(_TRAILING))
    }


#: Words that cannot discriminate between two candidates.
#:
#: Two groups, here for one reason rather than two. The first is ordinary
#: English glue. The second is resume-and-posting filler: "experience",
#: "production", "services", "software" appear in substantially every posting
#: and substantially every bullet, so matching on them is matching on nothing
#: while looking like a hit.
#:
#: This is NOT grounding's connective list, and the duplication is deliberate.
#: That set answers "does this word assert a fact?" — a question about truth,
#: where being wrong lets a claim through. This one answers "does this word tell
#: two candidates apart?" — a question about usefulness, where being wrong costs
#: ordering. Merging them would put a ranking tweak one edit away from a
#: grounding hole.
_UNINFORMATIVE = frozenset(
    """
    a an and or the of to in on at for by from as is are was were be been being
    this that these those it its into over under across through during while
    when where which who whom whose what how why not no nor but so than then
    there here also both each few more most other another some such only own
    same too
    very can will just should now up down out off about above below between
    with within without via per we you our your us they them their
    including include includes included using used use uses
    ability able comfortable familiar familiarity good great solid strong
    knowledge understanding proficient proficiency skills skill
    experience experienced years year
    professional production product professionally
    software engineering engineer engineers engineered developer developers
    development developing develop
    build building built create creating created design designing designed
    deliver delivering delivered work working worked works
    services service systems system platform platforms team teams
    role position candidate candidates applicant
    must need needs needed require required requirements requires
    plus bonus nice desirable preferred essential
    area areas context contexts domain domains environment environments field
    fields industry industries sector sectors setting settings space spaces
    participation involvement exposure background
    """.split()
)

#: A bare quantity, with or without the "or more" plus: "5", "5+", "10".
#:
#: Dropped from what a free-text requirement demands, because no sentence on a
#: resume evidences a duration by containing the characters "5+". Leaving it in
#: would make every requirement that states a number permanently unanswerable —
#: which is a different wrong answer, not a stricter one. What remains is the
#: requirement's TOPIC, which a document genuinely can speak to. The number is
#: still shown to the user: the label is claimed verbatim, numbers and all, and
#: `minimum_years` is what matching — not tailoring — exists to judge.
_QUANTITY = re.compile(r"^\d+\+?$")

#: Where a disjunction splits. "Experience in healthcare or another regulated
#: domain" states two acceptable answers and a candidate needs one of them; a
#: matcher that ignores the word "or" is reading a different requirement.
_ALTERNATIVE = re.compile(r"\b(?:and/or|or)\b", re.IGNORECASE)


def _is_term_of_art(token: str) -> bool:
    """Whether this token is a technical term rather than an English word.

    Internal punctuation, and not a bare quantity: "on-call", "ci/cd",
    "node.js", "front-ends", "c++". The same observation `grounding`'s
    proper-noun check rests on — nothing in ordinary English prose is spelled
    this way — used here for the opposite purpose. There it decides whether an
    unmatched word is an invented name. Here it decides whether a MATCHED word
    is specific enough to stand on its own.

    It is what separates the two single-token matches this module used to treat
    identically. "on-call" names one practice and a document containing it is
    talking about that practice; "regulated" is an adjective that attaches to
    finance, healthcare, aviation and gambling alike, and a document containing
    it has told you nothing about which.
    """
    if _QUANTITY.match(token):
        return False
    return any(character in token for character in "./+#_-")


def _informative(value: str | None) -> set[str]:
    """The words of a free-text requirement that actually name something."""
    return {token for token in tokens(value) if token not in _UNINFORMATIVE}


def _alternatives(label: str) -> tuple[frozenset[str], ...]:
    """What a document must show to have answered this free-text requirement.

    One frozenset per acceptable answer. The requirement is answered when the
    document covers ANY ONE of them COMPLETELY — which is the change that makes
    this module stop claiming things it cannot support.

    The rule it replaces asked for one distinctive word out of however many the
    requirement had. Against the evaluation dataset every free-text claim it
    made, right and wrong alike, rested on exactly one token, and two of them
    rested on the SAME token from the SAME sentence:

        "Experience in regulated financial services"           <- regulated
        "Experience in healthcare or another regulated domain" <- regulated

    So no threshold on count or proportion can separate them — and proportion
    ranks them the wrong way round, since the first matches one word of two and
    the second one word of four. Only the structure of the requirement can, and
    there are two pieces of structure to read:

    **"or" means alternatives.** The second requirement above offers "another
    regulated domain" as an acceptable answer in its own right, and a candidate
    whose profile says "two regulated industries" has answered it completely.
    The first offers no alternatives: "regulated" AND "financial" are both
    required, and a document with only one of them has answered neither.

    **A term of art carries an alternative by itself.** "Participation in an
    on-call rotation" is the named practice "on-call" wrapped in a sentence, and
    a document saying "on-call" is talking about it. Where an alternative
    contains such a token, that token is what must be matched and the ordinary
    words around it are phrasing. Where it contains none, every distinctive word
    must be matched, because there is nothing in it specific enough to stand
    alone.
    """
    out: list[frozenset[str]] = []
    for piece in _ALTERNATIVE.split(label):
        distinctive = {
            token for token in _informative(piece) if not _QUANTITY.match(token)
        }
        if not distinctive:
            continue
        terms_of_art = {token for token in distinctive if _is_term_of_art(token)}
        out.append(frozenset(terms_of_art or distinctive))
    return tuple(out)


# --- requirements a resume cannot answer -------------------------------------------

#: Words marking a requirement about the right to work somewhere, or about being
#: somewhere. A resume can state a location; it cannot evidence a visa, and no
#: achievement is evidence of a commute.
#:
#: A heuristic, and allowed to be one because of which way it errs. A false
#: positive drops a requirement from the RANKING and from what a document claims
#: to target — it can never let an unsupported claim through, because grounding
#: does not consult this module at all. A false negative leaves a noisy term in,
#: which is exactly the behaviour this replaces.
_ELIGIBILITY_WORDS = frozenset(
    """
    authorisation authorised authorization authorized citizen citizenship
    eligibility immigration passport permit residency resident sponsorship
    sponsor visa
    based commutable commute hybrid located location onsite on-site relocate
    relocating relocation office
    """.split()
)

#: Phrases the word list above misses because every word in them is ordinary.
#: "Right to work in Portugal" is four common words and a country.
_ELIGIBILITY_PHRASES = (
    "right to work",
    "authorised to work",
    "authorized to work",
    "legally entitled",
    "work permit",
    "willing to relocate",
    "able to travel",
)


def _is_eligibility(label: str) -> bool:
    folded = " ".join(label.split()).casefold()
    if any(phrase in folded for phrase in _ELIGIBILITY_PHRASES):
        return True
    return bool(tokens(label) & _ELIGIBILITY_WORDS)


# --- the posting, read -------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """One thing the posting asks for, kept whole.

    `terms` is what a document has to mention to have answered it. Empty means
    nothing on a resume can answer it, which is not the same as a requirement
    with no words in it.
    """

    label: str
    kind: str
    required: bool
    #: Every distinctive word, unioned. This is what SCORING uses — what a
    #: bullet is ranked by — and it is deliberately unchanged by the tightening
    #: below: a document's content and ordering are decided here, and only what
    #: the document may CLAIM moved.
    terms: frozenset[str]
    evidenceable: bool
    #: One set per acceptable answer, for free text. See `_alternatives`. A
    #: named requirement has exactly one: its whole name.
    alternatives: tuple[frozenset[str], ...] = ()

    @property
    def scope(self) -> str:
        return _EVIDENCE_SCOPE[self.kind]

    @property
    def named(self) -> bool:
        return self.kind in _NAMED

    def is_answered_by(self, available: frozenset[str]) -> bool:
        """Whether a document showing these tokens has answered this.

        The rule differs by kind, and the difference is the one that stops a
        certification being claimed by accident.

        A NAMED requirement needs every word of its name. "AWS Certified
        Developer" and "AWS Certified Solutions Architect" share two words out
        of three, so any-word matching claims the first from a candidate who
        holds only the second — a fabricated credential, in the field a reviewer
        reads to decide whether the document aimed correctly.

        An EDUCATION requirement needs one distinctive word, and is the one
        kind left permissive. A degree is checked against the education section
        alone — a small, transcribed set of institution, degree and field — so
        "computer" and "science" landing there is not a coincidence in the way a
        word landing anywhere in a page of prose is.

        Any other FREE-TEXT requirement needs one of its `alternatives`
        COMPLETELY. One distinctive word out of several used to be enough, and
        that is what let a document claim "Experience in regulated financial
        services" from a profile that said "regulated industries" — which is
        also, word for word, the only thing supporting the claim that it
        targeted "Experience in healthcare or another regulated domain". Those
        two cannot be told apart by how MUCH matched; they are told apart by
        WHAT the requirement will accept. See `_alternatives`.
        """
        if not self.terms:
            return False
        if self.named:
            return self.terms <= available
        if self.kind == KIND_EDUCATION:
            return bool(self.terms & available)
        return any(
            alternative <= available for alternative in self.alternatives
        )


@dataclass(frozen=True)
class JobTarget:
    """A posting, in the form selection can use.

    `weights` maps a term to what one distinct hit on it is worth. A term named
    by two requirements keeps the higher weight — a skill that is both required
    and mentioned in a responsibility is required.
    """

    requirements: tuple[Requirement, ...]
    weights: dict[str, float]

    @property
    def terms(self) -> frozenset[str]:
        return frozenset(self.weights)


def read_posting(
    requirements: JobCandidate, emphasise: tuple[str, ...] = ()
) -> JobTarget:
    """Turn structured requirements into weighted, typed, checkable targets."""
    found: list[Requirement] = []
    weights: dict[str, float] = {}

    def keep(label: str | None, kind: str, required: bool) -> None:
        if not label or not label.strip():
            return
        named = kind in _NAMED
        # A named thing is signal by definition; free text keeps only the words
        # that tell candidates apart.
        terms = tokens(label) if named else _informative(label)
        evidenceable = bool(terms) and not (not named and _is_eligibility(label))
        found.append(
            Requirement(
                label=label,
                kind=kind,
                required=required,
                terms=frozenset(terms) if evidenceable else frozenset(),
                evidenceable=evidenceable,
                # A named thing is one alternative: the whole name. Free text is
                # split on "or" and reduced to what each answer actually demands.
                alternatives=(
                    ()
                    if not evidenceable
                    else (frozenset(terms),)
                    if named
                    else _alternatives(label)
                ),
            )
        )
        if not evidenceable:
            return
        if named:
            weight = WEIGHT_REQUIRED_NAME if required else WEIGHT_PREFERRED_NAME
        else:
            weight = WEIGHT_REQUIRED_TEXT if required else WEIGHT_PREFERRED_TEXT
        for term in terms:
            weights[term] = max(weights.get(term, 0.0), weight)

    for item in requirements.required_skills:
        keep(item.name, KIND_SKILL, True)
    for item in requirements.technologies:
        keep(item.name, KIND_TECHNOLOGY, item.is_required)
    for item in requirements.required_experience:
        keep(item.description, KIND_EXPERIENCE, True)
    for item in requirements.preferred_skills:
        keep(item.name, KIND_SKILL, False)
    for item in requirements.preferred_experience:
        keep(item.description, KIND_EXPERIENCE, False)
    for item in requirements.certifications:
        keep(item.name, KIND_CERTIFICATION, item.is_required)
    for item in requirements.education:
        keep(item.description, KIND_EDUCATION, item.is_required)
    for item in requirements.responsibilities:
        keep(item.description, KIND_RESPONSIBILITY, True)
    for item in requirements.qualifications:
        keep(item.description, KIND_QUALIFICATION, item.is_required)

    # Emphasis nudges order only. It adds nothing new to the vocabulary: a
    # requirement the match could not evidence is one of the requirements above,
    # and inventing a term for it here would let a missing requirement pull
    # unrelated sentences forward.
    for label in emphasise:
        for term in _informative(label) & set(weights):
            weights[term] += WEIGHT_EMPHASIS

    return JobTarget(requirements=tuple(found), weights=weights)


# --- scoring -----------------------------------------------------------------------


def score(text: str | None, target: JobTarget) -> float:
    """What this text is worth against this posting.

    Distinct terms, weighted by the kind of requirement that contributed them.
    A text answering one required skill outranks one answering two content
    words, which is the ordering a reader of the posting would also apply.
    """
    if not text:
        return 0.0
    return sum(target.weights.get(token, 0.0) for token in tokens(text))


def labels(target: JobTarget) -> list[str]:
    """Every requirement the posting states, required before preferred.

    The order a document should answer them in, and the set grounding checks
    `targeted_requirements` against.
    """
    return [
        item.label
        for item in sorted(target.requirements, key=lambda r: not r.required)
    ]


def rank_names(names: list[str], target: JobTarget) -> list[str]:
    """Named things, ordered by what the posting asks for first.

    Used for the skills group: a reader scanning for the two skills the posting
    led with should find them at the front, not wherever the alphabet put them.
    Ties keep the order given, so the profile's own ordering survives where the
    posting expresses no preference.
    """
    priority: dict[str, int] = {}
    for position, item in enumerate(target.requirements):
        if item.kind not in _NAMED or not item.evidenceable:
            continue
        rank = position if item.required else position + 1000
        for term in item.terms:
            priority[term] = min(priority.get(term, rank), rank)

    def key(pair: tuple[int, str]) -> tuple[int, int]:
        order, name = pair
        best = min((priority.get(term, 10_000) for term in tokens(name)), default=10_000)
        return (best, order)

    return [name for _, name in sorted(enumerate(names), key=key)]


# --- what a document actually answered ----------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """What the finished document says, split by how strongly it says it.

    Built from the document rather than from the profile: the question is what
    THIS resume puts in front of a reader, not what the candidate could have
    shown. A skill dropped for being irrelevant is a skill this document does
    not evidence.
    """

    prose: frozenset[str]
    listed: frozenset[str]
    education: frozenset[str]


def answered(evidence: Evidence, target: JobTarget) -> list[str]:
    """The requirements this document genuinely speaks to, required first.

    A requirement is claimed only when the document mentions it in the place
    that kind of requirement is answered. Anything eligibility-shaped is never
    claimed, because no document evidences it.

    This is the one field in a tailored resume that is a claim about the JOB
    rather than about the candidate, and it is the field a reviewer uses to
    decide whether the document aimed at the right things. A generous rule here
    does not flatter the document, it misleads the person checking it.
    """
    available = {
        SCOPE_PROSE: evidence.prose,
        SCOPE_LISTED: frozenset(evidence.listed | evidence.prose),
        SCOPE_EDUCATION: evidence.education,
    }

    out: list[str] = []
    seen: set[str] = set()
    for item in sorted(target.requirements, key=lambda r: not r.required):
        if not item.evidenceable or item.label in seen:
            continue
        if item.is_answered_by(available[item.scope]):
            out.append(item.label)
            seen.add(item.label)
    return out


__all__ = [
    "Evidence",
    "JobTarget",
    "Requirement",
    "answered",
    "labels",
    "rank_names",
    "read_posting",
    "score",
    "tokens",
]
