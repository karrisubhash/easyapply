"""Checking a generated document against the snapshot it claims to stand on.

This module exists because of one asymmetry: the generator is the thing with an
interest in the document looking good, and it is also the thing that decides
what the document says. Asking it to mark its own work — "only use facts from
the profile", and then trusting that it did — is not validation. So nothing here
reads the generator's own reasoning, its confidence, or anything it asserts
about its output. It reads the document, it reads the snapshot, and it decides
independently.

The four questions it asks, hardest first:

  1. **Does this entry exist?** Every experience, project, education entry and
     certification names a `source_id`. It must resolve to a row of that kind in
     THIS profile version. An id from another version, or from nowhere, means
     the document is describing a career the user does not have.

  2. **Were the copied fields copied?** A company name, a job title, a degree,
     a date — these are transcribed, not written. Any difference beyond
     whitespace and case is an invented fact wearing the clothes of a copied
     one, and it is the single most dangerous thing a resume generator can do.

  3. **Is the quote real?** Every generated line cites `source_quote`, which is
     supposed to be verbatim from a named column of the named row. If that text
     is not actually there, the citation is fabricated — and a fabricated
     citation is worse than none, because it is what makes the rest of the line
     look checked.

  4. **Does the line say more than its source?** A number that is not in the
     source is an invented number: "led a team of 12" from a source that never
     counted anyone — and "twelve", "a decade" and "doubled" are numbers too.
     A number or a rank ("principal", "tech lead") must be in the line's OWN
     quote or its row's identity fields; the rest of the row's prose never
     licenses one. A proper noun that appears nowhere in the whole snapshot is
     an invented employer, product or technology — and a known technology is a
     name in any case and at any position, as is any token in a script with no
     letter case. All of these are errors. A reworded verb is not — "Built"
     for "Developed" changes the prose, not the facts — and it is reported as a
     warning, once per line, so a reader can still see it.

  5. **Is it a whole, honest resume?** Every job once and in the profile's
     order, none left bulletless when its row has text; every contact column
     the profile holds; no sentence or skill printed twice; no empty line; a
     headline that is one of the candidate's job titles; skill headings that
     are the profile's own categories. None of these is a fabrication in the
     narrow sense, but each is a way a document misrepresents a career it
     otherwise quotes correctly — and each is an error the repair pass can
     address by removing, restoring or reordering, or (a missing job, a job
     with nothing under it) cannot, which withholds the document.

ERRORS withhold the artifact. WARNINGS are shown and do not. That line is drawn
where it is because an error means the document asserts something the user
cannot back up in an interview, and a warning means the document says a true
thing differently.

Nothing in here is a model, and nothing in here is a similarity score. Every
check is an exact or set-membership test over text the user has already
confirmed, which is why its verdict can be stored and stood behind.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from app.models import ProfileVersion
from app.models.tailoring import SEVERITY_ERROR, SEVERITY_WARNING, TailoringIssue
from app.schemas.tailored_resume import (
    TailoredCertification,
    TailoredContact,
    TailoredEducation,
    TailoredExperience,
    TailoredLine,
    TailoredProject,
    TailoredResumeDocument,
)
from app.services.matching.vocabulary import TECHNOLOGIES

# --- the vocabulary of findings ------------------------------------------------
#
# Our own words, from a fixed set. Never str(exception) and never the generated
# text on its own: a reason has to be readable by the person deciding whether to
# send this document.

REASON_UNKNOWN_SOURCE = "This entry points at career data that is not in the selected profile version."
REASON_FIELD_ALTERED = "This was copied from your profile but does not match what your profile says."
REASON_QUOTE_NOT_FOUND = "The text this line claims to be based on is not in your profile."
# "The text it cites", not "your profile": a number from elsewhere in the same
# role is an error too (it is not what this line's source says), and telling
# the user their profile lacks a number it contains would be false.
REASON_INVENTED_NUMBER = "This line states a number the text it cites does not contain."
REASON_INVENTED_NAME = "This line names something that does not appear anywhere in your profile."
REASON_BORROWED_NAME = "This line names something from a different part of your profile than the one it cites."
# Deliberately not "the facts behind it are in your profile": a warning is what
# a line gets when grounding could NOT tell, so it must not vouch for it.
REASON_REWORDED = (
    "This wording is not in your profile. Check that it says only what your profile says."
)
REASON_NEGATION_ADDED = (
    "This line says something did not happen, or was not so, where your profile says it was."
)
REASON_UNLISTED_SKILL = "This skill is not listed in your profile."
REASON_UNLISTED_TECHNOLOGY = "This technology is not listed on that project in your profile."
REASON_EMPTY_DOCUMENT = "The generated resume has no experience, projects, education or certifications in it."
REASON_UNTARGETED = "This requirement is not one this job posting actually lists."
REASON_CONTACT_ALTERED = "This contact detail does not match what your profile says."
REASON_CONTACT_MISSING = "Your profile has this contact detail and the resume leaves it out."
REASON_UNKNOWN_LINK = "This link is not one your profile holds."
REASON_UNKNOWN_SKILL_GROUP = "This skills heading is not a category your profile uses."
REASON_SKILL_UNDER_WRONG_HEADING = (
    "A skill here is listed under a category your profile does not give it."
)
REASON_INVENTED_SENIORITY = "This line gives a seniority or title the text it cites does not state."
REASON_HEADLINE_NOT_A_TITLE = "The headline is copied from one of your job titles, and this is not any of them."
REASON_DUPLICATE_ENTRY = "This entry appears on the resume more than once."
REASON_MISSING_EXPERIENCE = "A job in your profile is missing from this resume."
REASON_EXPERIENCE_OUT_OF_ORDER = "These jobs are not in the order your profile holds them in."
REASON_NO_BULLETS = "This job has nothing under it, although your profile has text for it."
REASON_DUPLICATE_LINE = "This sentence is already printed earlier on this resume."
REASON_DUPLICATE_SKILL = "This skill is already listed earlier on this resume."
REASON_EMPTY_LINE = "This line has no text."

#: The heading the posting's own skills are grouped under. MIRRORS
#: `generation.LEAD_SKILL_GROUP` rather than importing it, so that this module
#: keeps depending on nothing that ranks or generates; a test pins the two equal.
LEAD_SKILL_GROUP = "Relevant to this role"
#: The heading `DeterministicTailor._skills` gives a skill with no stored
#: category. Neutral on purpose: it asserts nothing about the skills under it.
UNCATEGORISED_SKILL_GROUP = "Other"
#: Headings that, like "Other", say only "more skills" and nothing about them.
#: A closed list, and it is here because the run2/run3 replay showed models
#: writing exactly these ("Additional", "Other skills", "Additional Skills")
#: for the tail group: refusing them costs a repair on a heading that claims
#: nothing. Anything with content — a level, a year count, an employer, a
#: domain — is still an error.
#: The contact columns a document must carry whenever the version holds them,
#: in the order they are checked and restored.
CONTACT_FIELDS = ("full_name", "email", "phone", "location")

NEUTRAL_SKILL_HEADINGS = frozenset(
    {"other", "other skills", "additional", "additional skills", "skills"}
)

#: Words that carry no factual claim on their own. Used only to decide whether
#: an unmatched word is worth a WARNING; it is never used to excuse a number or
#: a proper noun, both of which are checked before this set is consulted.
_CONNECTIVE = frozenset(
    """
    a an and or the of to in on at for with by from as is are was were be been
    being this that these those it its into over under across through during
    while when where which who whom whose what how why not no nor but so than
    then there here also both each few more most other some such only own same
    too very can will just should now up down out off about above below between
    within without via per including include includes included using used use
    uses new work works worked working built build builds building developed
    develop develops developing led lead leads leading created create creates
    creating designed design designs designing delivered deliver delivers
    delivering managed manage manages managing improved improve improves
    improving maintained maintain maintains maintaining supported support
    supports supporting implemented implement implements implementing owned own
    owns owning ran run runs running wrote write writes writing
    """.split()
)

#: Splits text into comparable tokens. Keeps internal dots, plus signs and
#: hyphens so "Node.js", "C++" and "CI-CD" survive as single tokens rather than
#: being shredded into fragments that match anything.
#:
#: Unicode letters and digits, not ASCII. The ASCII-only pattern this replaced
#: made "１２" (full-width digits) and every CJK sentence produce NO tokens at
#: all — so no findings at all — and split "Ørsted" into "rsted", a lowercase
#: word no name check could see. `[^\W_]` is "a letter or digit in any script";
#: text is NFC-composed first (`_composed`) so an accent typed as a combining
#: mark does not split a word in two either.
_TOKEN = re.compile(r"[^\W_][\w+.#/-]*")

#: Trailing characters the pattern above deliberately over-matches. A sentence
#: ends in a full stop and a list ends in a comma, so "FastAPI." would otherwise
#: be a different token from "FastAPI" and every technology at the end of a
#: sentence would read as invented. `+` and `#` are NOT stripped: "C++" and "C#"
#: end that way on purpose.
_TRAILING = "._-/"

#: Where a hyphenated or slashed token is split to look for a number word or a
#: seniority word inside it: "twenty-five", "senior-level", "co-founder".
_COMPOUND_PART = re.compile(r"[-/]")

#: Numbers spelled as words. A closed set, and a token counts when it IS one or
#: a hyphenated part of it is one ("twenty-five", "two-person", "zero-downtime").
#: These get exactly the treatment a digit gets, because "Ten years", "managed
#: twelve engineers", "a decade" and "doubled throughput" are the same claims as
#: "10", "12", "10 years" and "2x" — and used to pass as REWORDED warnings whose
#: text told the user the facts were in their profile.
#:
#: Two deliberate omissions:
#:   * "one" — far more often a determiner ("one of the", "no one", "one-off")
#:     than a count, and a claim of ONE of something is never an inflation.
#:   * ordinals ("first", "second", "third") — "first" and "second" are
#:     ordinary prose, "third" is mostly "third-party". A fabricated ranking
#:     ("ranked first") is a known limitation.
#: "half" IS included: "cut costs in half" states the same quantity as "50%".
_NUMBER_WORDS = frozenset(
    """
    zero two three four five six seven eight nine ten eleven twelve thirteen
    fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty
    fifty sixty seventy eighty ninety hundred hundreds thousand thousands
    million millions billion billions trillion trillions dozen dozens decade
    decades half halved halves halving twice thrice double doubled doubles
    doubling triple tripled triples tripling quadruple quadrupled quadrupling
    twofold threefold fourfold fivefold tenfold hundredfold
    """.split()
)

#: Seniority and title words that are claims on their own, wherever they sit
#: in a sentence and whatever their case. Nouns and titles only: the verbs
#: "led", "managed", "directed" and "headed" are different tokens and are not
#: here, because they describe what was done rather than a rank that was held.
_SENIORITY_TITLES = frozenset(
    """
    senior sr snr junior jr principal director vp svp evp avp chief ceo cto cfo
    coo cio ciso cpo cmo manager mgr president founder cofounder co-founder
    founding supervisor
    """.split()
)
#: Title words matched only as a whole token, never as part of a compound:
#: "head of engineering" is a title, "head-to-head" is not.
_SENIORITY_WHOLE_ONLY = frozenset({"head"})
#: Words that are a TITLE only beside a role noun and are otherwise prose.
#: "Lead the migration" is a verb and "trained clinical staff" is a noun for
#: other people; "tech lead", "Lead Backend Engineer" and "Staff Software
#: Engineer" are ranks. So these count when one of the next two tokens is a
#: role noun, or — for "lead" — when the token before it is a team qualifier.
_SENIORITY_QUALIFIED = frozenset({"lead", "staff"})
_ROLE_NOUNS = frozenset(
    """
    engineer developer architect scientist designer analyst programmer
    consultant researcher sre swe dev
    """.split()
)
_LEAD_QUALIFIERS = frozenset(
    "tech technical team engineering project product program programme squad".split()
)

#: Technology names that are also ordinary English words. They are excluded
#: from the any-case, any-position technology rule below because lowercase
#: "react to incidents", "the rest of the team", "go live", "in spring 2022",
#: "less downtime", "maintenance windows", "at the helm", "in flux" are prose,
#: and an error on prose would remove good lines from a model's document. For
#: these the older rule still applies — capitalised mid-line (or internally
#: capitalised) reads as a name — which is exactly as strong as before.
#: `test_the_english_word_exclusions_are_all_real_vocabulary_entries` pins that
#: every entry is really in the vocabulary, so a typo cannot silently exclude
#: nothing.
_TECHNOLOGIES_THAT_ARE_ENGLISH_WORDS = frozenset(
    """
    agile airflow babel backbone bamboo beam bootstrap chai chef confluence
    consul cucumber cypress dart echo elixir ember emotion envoy express fiber
    flask flutter flux gin go groovy helm hibernate hive ionic jasmine jest
    karma less locust mercurial mocha nomad oracle packer parcel phoenix
    playwright prefect presto pulsar puppet puppeteer pyramid railway react relay
    remix render rest rollup sentry snowflake soap solidity spark spinnaker
    spring storybook struts subversion superset svelte swift tableau tailwind
    temporal thrift tornado transformers vagrant vault windows
    """.split()
)


def _technology_names() -> frozenset[str]:
    """The curated technology vocabulary, as single grounding tokens.

    Borrowed from `matching.vocabulary.TECHNOLOGIES` — the reviewed list of
    things that are technologies and nothing else — rather than kept as a
    second list that would drift from it. Only single-word entries: a
    multi-word name ("github actions") is covered by its distinctive word
    where that word is itself an entry. Single letters ("c", "r") and entries
    carrying a digit ("s3", "k6") are left out: the first are too ambiguous to
    call names, and the second are already held to the stricter number rule.
    """
    return frozenset(
        name
        for name in TECHNOLOGIES
        if " " not in name
        and len(name) >= 2
        and not any(char.isdigit() for char in name)
        and name not in _TECHNOLOGIES_THAT_ARE_ENGLISH_WORDS
    )


#: A token that is a known technology is a NAME in any case and at any
#: position: "in rust and FastAPI" and "Rust mentoring: ..." both used to pass,
#: the first because lowercase never looks like a name, the second because a
#: line's first word is skipped by the capitalisation heuristic.
_TECHNOLOGY_NAMES = _technology_names()


def _states_a_number(token: str) -> bool:
    """Whether a token is a quantity: any numeric character, or a number word.

    `str.isnumeric` rather than `\\d`: it covers every script's digits (the
    full-width "１２" included), vulgar fractions, superscripts, Roman numeral
    code points and CJK numeral ideographs — any of which states a number as
    plainly as "12" does.
    """
    if any(char.isnumeric() for char in token):
        return True
    return token in _NUMBER_WORDS or any(
        part in _NUMBER_WORDS for part in _COMPOUND_PART.split(token)
    )


#: What a number word says, so a faithful rewrite that spells a number another
#: way is not an invented one: "Mentored four engineers" citing "Mentored 4
#: engineers", "with no downtime… a zero-downtime migration", "team of two"
#: citing "a two-person team". Multipliers are kept apart from counts ("2x" is
#: not "2"). Vague words ("dozens", "hundreds") state no exact quantity and are
#: left to the exact-token rule.
_NUMBER_VALUES = {
    "zero": "0", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
    "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
    "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
    "seventy": "70", "eighty": "80", "ninety": "90", "hundred": "100",
    "thousand": "1000", "million": "1000000", "billion": "1000000000",
    "dozen": "12", "decade": "10",
    "twice": "2x", "double": "2x", "doubled": "2x", "doubles": "2x", "doubling": "2x",
    "twofold": "2x", "thrice": "3x", "triple": "3x", "tripled": "3x", "triples": "3x",
    "tripling": "3x", "threefold": "3x", "quadruple": "4x", "quadrupled": "4x",
    "quadrupling": "4x", "fourfold": "4x", "fivefold": "5x", "tenfold": "10x",
    "hundredfold": "100x", "halved": "0.5x", "halving": "0.5x",
}
_PLAIN_NUMBER = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d+(?:\.\d+)?$")
_MULTIPLIER = re.compile(r"^(\d+(?:\.\d+)?)[x×]$")


def _quantities(token: str) -> set[str] | None:
    """The exact quantities a token states, in one spelling — or None when it
    states none this can compare (units such as "63%" or "4M", vague words),
    and exact matching decides."""
    if _PLAIN_NUMBER.match(token):
        return {token.replace(",", "")}
    multiplier = _MULTIPLIER.match(token)
    if multiplier:
        return {multiplier.group(1) + "x"}
    if token in _NUMBER_VALUES:
        return {_NUMBER_VALUES[token]}
    parts = [part for part in _COMPOUND_PART.split(token) if part]
    if len(parts) > 1:
        values = [_NUMBER_VALUES.get(part) for part in parts]
        known = [value for value in values if value is not None]
        if not known:
            return None
        # "twenty-five" is one number; "zero-downtime" and "two-person" are one
        # number beside an ordinary word.
        if len(known) == 2 and all(value.isdigit() for value in known) and int(known[0]) % 10 == 0 and 0 < int(known[1]) < 10:
            return {str(int(known[0]) + int(known[1]))}
        return set(known)
    return None


def _number_licensed(token: str, strict: set[str]) -> bool:
    """Whether the quantity a token states is stated by its licensing text.

    Exact first. Otherwise the token's quantities are compared with every
    quantity the licensing tokens state, in one spelling — never looser than
    that: a quantity the quote does not state is still an invention however it
    is written."""
    if token in strict:
        return True
    wanted = _quantities(token)
    if not wanted:
        return False
    licensed: set[str] = set()
    for other in strict:
        licensed |= _quantities(other) or set()
    return wanted <= licensed


#: A rank word that is part of a technical name, not a title held: "AWS Secrets
#: Manager", "package manager", "principal component analysis", "supervisor
#: trees". Closed, and each entry is the name of a thing. The rule below also
#: accepts any such two-word phrase the line's own row already writes.
_TECHNICAL_COMPOUNDS = frozenset(
    {
        ("secrets", "manager"), ("package", "manager"), ("window", "manager"),
        ("task", "manager"), ("resource", "manager"), ("certificate", "manager"),
        ("entity", "manager"), ("volume", "manager"), ("service", "manager"),
        ("manager", "pattern"), ("principal", "component"), ("principal", "components"),
        ("supervisor", "tree"), ("supervisor", "trees"), ("supervisor", "process"),
        ("security", "principal"), ("service", "principal"),
    }
)


def _is_technical_compound(tokens: list[str], position: int, context: str) -> bool:
    """Whether the rank word at `position` is part of a technical name.

    Two ways, both narrow: the pair is a known technical name, or the same
    two-word phrase appears in the text this line may draw on (its quote, its
    row's identity fields and description). A rewrite naming "Secrets Manager"
    from a description that says "stored credentials in AWS Secrets Manager" is
    not a promotion; the same word in a line whose row never writes the phrase
    is still judged as a rank."""
    token = tokens[position]
    pairs = []
    if position > 0:
        pairs.append((tokens[position - 1], token))
    if position + 1 < len(tokens):
        pairs.append((token, tokens[position + 1]))
    return any(
        pair in _TECHNICAL_COMPOUNDS
        # Never beside a role noun: "principal engineer" in a description is
        # somebody's title, and borrowing it is exactly the promotion to catch.
        or (not (set(pair) & _ROLE_NOUNS) and f" {pair[0]} {pair[1]} " in context)
        for pair in pairs
    )


def _states_seniority(tokens: list[str], position: int) -> bool:
    """Whether the token at `position` claims a rank or a title."""
    token = tokens[position]
    if token in _SENIORITY_WHOLE_ONLY or token in _SENIORITY_TITLES:
        return True
    if any(part in _SENIORITY_TITLES for part in _COMPOUND_PART.split(token)):
        return True
    if token in _SENIORITY_QUALIFIED:
        if any(following in _ROLE_NOUNS for following in tokens[position + 1 : position + 3]):
            return True
        if token == "lead" and position > 0 and tokens[position - 1] in _LEAD_QUALIFIERS:
            return True
    return False


def _is_caseless(token: str) -> bool:
    """Whether a token is written in a script with no letter case (CJK, Thai,
    Arabic, Hebrew...). Such a token gives the name heuristic nothing to read,
    so it has to be in the profile to be excused — see `_check_line`."""
    return any(unicodedata.category(char) in ("Lo", "Lm") for char in token)


def _composed(value: str) -> str:
    """NFC: one code point per accented letter, so "é" typed as e + U+0301 and
    "é" typed as one character are the same token. Canonical equivalence only —
    it never merges two different characters."""
    return unicodedata.normalize("NFC", value)


def _column_length(name: str) -> int:
    return TailoringIssue.__table__.c[name].type.length


#: The lengths of the `tailoring_issues` columns a Finding is stored in, read
#: off the model rather than restated, so the two cannot drift apart.
ISSUE_SECTION_LIMIT = _column_length("section")
ISSUE_FIELD_PATH_LIMIT = _column_length("field_path")
ISSUE_CLAIM_LIMIT = _column_length("claim")
ISSUE_REASON_LIMIT = _column_length("reason")


def _clip(value: str | None, limit: int) -> str | None:
    return value if value is None or len(value) <= limit else value[:limit]


@dataclass(frozen=True)
class Finding:
    """One thing validation found. Becomes a TailoringIssue row verbatim.

    Every text field is truncated to its column's length HERE, at
    construction, rather than at each call site. Call sites used to slice
    `claim[:600]` by hand and some did not: a skill name, a project technology
    and a targeted-requirement label reached the worker at whatever length the
    model wrote them, and the insert raised DataError — failing a run in the
    one stage whose contract is that it never fails one. Truncated, never
    paraphrased: the claim is still the generator's own text, cut short.
    """

    severity: str
    section: str
    field_path: str | None
    claim: str | None
    reason: str

    def __post_init__(self) -> None:
        for name, limit in (
            ("section", ISSUE_SECTION_LIMIT),
            ("field_path", ISSUE_FIELD_PATH_LIMIT),
            ("claim", ISSUE_CLAIM_LIMIT),
            ("reason", ISSUE_REASON_LIMIT),
        ):
            object.__setattr__(self, name, _clip(getattr(self, name), limit))


@dataclass(frozen=True)
class GroundingReport:
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == SEVERITY_ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == SEVERITY_WARNING)

    @property
    def passed(self) -> bool:
        """Whether the artifact may be rendered.

        Errors only. A document full of warnings is still a document whose every
        factual claim traces to the profile.
        """
        return not self.errors


# --- comparing text ------------------------------------------------------------


def _normalise(value: str | None) -> str:
    """Case-folded, whitespace-collapsed. The comparison every check uses.

    Deliberately narrow: it forgives formatting and nothing else. Stripping
    punctuation here would let "5 years" match "5+ years", and the whole point
    of the field checks is that a copied field was copied.
    """
    if value is None:
        return ""
    return " ".join(value.split()).casefold()


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _token_at(match: re.Match[str]) -> str:
    """One comparable token, folded and stripped of sentence punctuation."""
    return match.group(0).casefold().rstrip(_TRAILING)


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        token for match in _TOKEN.finditer(_composed(value)) if (token := _token_at(match))
    }


def _looks_like_a_name(token: str, original: str) -> bool:
    """Whether this token is a proper noun or a product name.

    Judged from the ORIGINAL casing, before folding: a resume capitalises
    employers, technologies and products, and lowercases ordinary prose. Tokens
    with internal punctuation ("node.js", "ci/cd") count too — nothing in
    ordinary English prose is spelled that way.

    A heuristic, and it is allowed to be one because of which way it errs. A
    false positive costs a finding the user can read and dismiss; a false
    negative only ever demotes an error to the warning that the unmatched-word
    check raises anyway. Neither direction lets an unsupported claim through
    silently.
    """
    if len(token) < 2:
        return False
    if any(char in token for char in "./+#_-"):
        return True
    # Find how the token was actually written, and ask whether it was
    # capitalised somewhere other than the start of a sentence.
    for match in _TOKEN.finditer(original):
        word = match.group(0).rstrip(_TRAILING)
        if word.casefold() != token:
            continue
        if word[0].isupper() and match.start() != 0:
            return True
        if any(char.isupper() for char in word[1:]):
            return True
    return False


# --- what the snapshot actually says --------------------------------------------


@dataclass(frozen=True)
class SnapshotIndex:
    """Everything the validator is allowed to consider true.

    Built once from the profile version, and it is the ONLY source of truth
    consulted. Nothing here comes from the job posting, from the match, or from
    the generator — which is what makes a pass mean "the user can back this up"
    rather than "it looked plausible against the inputs".
    """

    experiences: dict[int, object]
    projects: dict[int, object]
    education: dict[int, object]
    certifications: dict[int, object]
    #: Every skill name and every project technology, folded.
    skill_names: frozenset[str]
    #: Every token appearing anywhere in the version. A name found here but not
    #: in the cited row is a borrowed name — a warning, not an invention.
    vocabulary: frozenset[str]
    #: The candidate's own summary paragraph, which lives on the version rather
    #: than on any child row. A summary line may quote it, so it has to be a
    #: haystack — without this, a document quoting the profile's own summary
    #: verbatim was reported as citing text that is not in the profile.
    summary: str | None
    #: The contact columns and the link rows, for `_check_contact`. Held as the
    #: version's values rather than as a comparison result: this module decides,
    #: it is not told.
    contact: dict[str, str | None]
    links: dict[str, str | None]
    #: Folded skill name -> the category the profile files it under, exactly as
    #: stored (None when it has none). First row wins; names are unique per
    #: version case-insensitively, so "first" only matters for whitespace
    #: variants. Project technologies are absent: they have no category.
    skill_categories: dict[str, str | None] = field(default_factory=dict)
    #: Every heading a skill group may carry, folded: the lead group, "Other",
    #: and each category a skill row actually holds. A heading is printed in
    #: bold above the skills it groups, so it is a claim like any other — an
    #: invented one ("Expert, 10+ years") is invented text in the largest type
    #: on the page after the name.
    skill_headings: frozenset[str] = frozenset()


def index_snapshot(version: ProfileVersion) -> SnapshotIndex:
    vocabulary: set[str] = set()
    skill_names: set[str] = set()
    skill_categories: dict[str, str | None] = {}
    skill_headings: set[str] = {
        _normalise(LEAD_SKILL_GROUP),
        _normalise(UNCATEGORISED_SKILL_GROUP),
        *NEUTRAL_SKILL_HEADINGS,
    }

    for experience in version.experiences:
        vocabulary |= _tokens(experience.company)
        vocabulary |= _tokens(experience.job_title)
        vocabulary |= _tokens(experience.location)
        vocabulary |= _tokens(experience.description)

    for entry in version.education:
        vocabulary |= _tokens(entry.institution)
        vocabulary |= _tokens(entry.degree)
        vocabulary |= _tokens(entry.field_of_study)
        vocabulary |= _tokens(entry.description)

    for skill in version.skills:
        skill_names.add(_normalise(skill.name))
        skill_categories.setdefault(_normalise(skill.name), skill.category)
        if skill.category and skill.category.strip():
            skill_headings.add(_normalise(skill.category))
        vocabulary |= _tokens(skill.name)
        vocabulary |= _tokens(skill.category)

    for project in version.projects:
        vocabulary |= _tokens(project.name)
        vocabulary |= _tokens(project.role)
        vocabulary |= _tokens(project.description)
        for technology in project.technologies:
            skill_names.add(_normalise(technology.name))
            vocabulary |= _tokens(technology.name)

    for certification in version.certifications:
        vocabulary |= _tokens(certification.name)
        vocabulary |= _tokens(certification.issuing_organization)

    vocabulary |= _tokens(version.summary)
    vocabulary |= _tokens(version.full_name)
    vocabulary |= _tokens(version.location)

    return SnapshotIndex(
        experiences={row.id: row for row in version.experiences},
        projects={row.id: row for row in version.projects},
        education={row.id: row for row in version.education},
        certifications={row.id: row for row in version.certifications},
        skill_names=frozenset(skill_names),
        vocabulary=frozenset(vocabulary),
        summary=version.summary,
        contact={
            "full_name": version.full_name,
            "email": version.email,
            "phone": version.phone,
            "location": version.location,
        },
        links={row.url: row.label for row in version.links},
        skill_categories=skill_categories,
        skill_headings=frozenset(skill_headings),
    )


# --- the checks -------------------------------------------------------------------


def _check_copied_field(
    findings: list[Finding],
    *,
    section: str,
    path: str,
    generated: str | None,
    stored: str | None,
    reason: str = REASON_FIELD_ALTERED,
) -> None:
    """A field that was supposed to be transcribed, not written.

    `reason` is overridable only so the finding names the KIND of thing that was
    altered. The check itself does not vary: a contact detail is compared
    exactly, as a company name is, because both are transcribed and neither is
    the generator's to improve.
    """
    if generated is None:
        return
    if _normalise(generated) == _normalise(stored):
        return
    findings.append(
        Finding(
            severity=SEVERITY_ERROR,
            section=section,
            field_path=path,
            claim=generated[:600],
            reason=reason,
        )
    )


def _check_copied_date(
    findings: list[Finding],
    *,
    section: str,
    path: str,
    generated: date | None,
    stored: date | None,
) -> None:
    if generated == stored:
        return
    findings.append(
        Finding(
            severity=SEVERITY_ERROR,
            section=section,
            field_path=path,
            claim=generated.isoformat() if generated else None,
            reason=REASON_FIELD_ALTERED,
        )
    )


def _check_copied_precision(
    findings: list[Finding],
    *,
    section: str,
    path: str,
    generated: str | None,
    stored: str | None,
) -> None:
    """A date's precision, where OMITTING it is an alteration too.

    Every other copied field gets a free pass when the document leaves it out: a
    missing location is a formatting choice and says nothing untrue. Precision is
    not like that, because `rendering._format_period` reads it directly and
    prints a MONTH when it finds nothing. So a document carrying 2015-01-01 and
    no precision puts "Jan 2015" on a resume whose source said "2015" — a month
    nobody wrote, introduced after grounding has stopped looking, which is the
    worst place in this pipeline to introduce one.

    Hence no `is None` early return, and hence a separate function rather than a
    flag on `_check_copied_field`: the free pass is right for every other
    transcribed field and wrong for exactly this one.
    """
    if _normalise(generated) == _normalise(stored):
        return
    findings.append(
        Finding(
            severity=SEVERITY_ERROR,
            section=section,
            field_path=path,
            claim=generated,
            reason=REASON_FIELD_ALTERED,
        )
    )


def _check_line(
    findings: list[Finding],
    index: SnapshotIndex,
    line: TailoredLine,
    *,
    section: str,
    path: str,
    haystack: str | None,
    identity: tuple[str | None, ...] = (),
    context: tuple[str | None, ...] = (),
    titles: frozenset[str] = frozenset(),
) -> None:
    """One generated sentence, against the text it cites.

    `haystack` is the column the quote is supposed to be verbatim from. The rest
    of what the line is entitled to say comes in two strengths, and the split is
    the point of this function:

      * `identity` — the transcribed identity fields of the same row (title,
        company, location; a project's name, role and technologies). These
        license EVERYTHING, numbers included: a bullet on "Ledger Engine 2" may
        say "2", and a bullet on a Stripe job may name Stripe.
      * `context` — the prose of the same row outside the quote (its
        description). This licenses NAMES and ordinary words only. It never
        licenses a number. It used to, and that was the hole: "Mentored 63
        engineers" citing "Mentored 4 engineers" passed with no finding at
        all, because 63 was elsewhere in the description — as a latency
        percentage. A number is a claim about one specific thing, and only the
        sentence that states it says which thing.

    Widening the haystack would let a line cite text from a column it is not
    about; widening `identity` would let it attach facts from a row it is not
    about; letting `context` license numbers lets a line attach a true number
    to the wrong claim. None of the three is another's job.

    Seniority is held to the number rule — a rank is a claim about one job, and
    only that job's title or the cited sentence can state it. `titles` widens
    that for lines that stand on the whole career (the summary): tokens of the
    job titles the candidate actually holds, so "Senior backend engineer" may
    open a summary for someone whose title says Senior. It never licenses a
    number.
    """
    if not any(character.isalnum() for character in line.text):
        # Nothing printable but punctuation — "", "—", "•", "...": a bullet
        # glyph with nothing after it. Not a false claim, but not a
        # line either — and a quote check on it would pass whenever the quote
        # is real, since an empty sentence says nothing the quote does not.
        findings.append(
            Finding(SEVERITY_ERROR, section, path, line.text, REASON_EMPTY_LINE)
        )
        return

    quote = _normalise(line.source_quote)

    if not quote or quote not in _normalise(haystack):
        findings.append(
            Finding(
                severity=SEVERITY_ERROR,
                section=section,
                field_path=f"{path}.source_quote",
                claim=line.source_quote[:600],
                reason=REASON_QUOTE_NOT_FOUND,
            )
        )
        # The quote is the baseline the sentence is measured against. Without a
        # real one there is nothing to measure against, and every token below
        # would be reported as invented — a cascade of findings that all say the
        # same thing. One accurate error is more use than twenty derived ones.
        return

    # `strict` licenses numbers and seniority; `supported` licenses the rest.
    strict = _tokens(line.source_quote)
    for value in identity:
        strict |= _tokens(value)
    supported = set(strict)
    for value in context:
        supported |= _tokens(value)
    ranks = strict | titles
    context_text = " " + " ".join(
        token
        for value in (line.source_quote, *identity, *context)
        for token in (_token_at(match) for match in _TOKEN.finditer(_composed(value or "")))
        if token
    ) + " "

    # One finding per reason per line. The finding's claim is the whole line,
    # so a second token with the same problem produces a finding identical to
    # the first — and three reworded words became "3 lines are worth a second
    # look" about one line. The count of findings is what the review screen
    # reads, so it has to count lines, not tokens.
    reported: set[str] = set()

    def report(severity: str, reason: str) -> None:
        if reason in reported:
            return
        reported.add(reason)
        findings.append(
            Finding(
                severity=severity,
                section=section,
                field_path=path,
                claim=line.text,
                reason=reason,
            )
        )

    # A negation the quote does not carry inverts it. "not", "no", "without"
    # and "nor" are connectives to the token check below — they assert no fact
    # of their own — so "Did not build the Kafka pipeline", cited to "Built the
    # Kafka pipeline", used to pass with one reworded warning. Counted as a
    # family, so "with no downtime" for "without downtime" is a rewording, not
    # an inversion; any negator the quote does not account for is an error.
    if _negations(line.text) > _negations(line.source_quote):
        report(SEVERITY_ERROR, REASON_NEGATION_ADDED)

    text = _composed(line.text)
    tokens = [token for match in _TOKEN.finditer(text) if (token := _token_at(match))]
    for position, token in enumerate(tokens):
        # Order matters: a number or a rank is judged against `strict` BEFORE
        # the looser `supported` set is consulted, so the rest of the row's
        # prose can never excuse one.
        if _states_a_number(token):
            if not _number_licensed(token, strict):
                report(SEVERITY_ERROR, REASON_INVENTED_NUMBER)
            continue

        if _states_seniority(tokens, position) and not _is_technical_compound(
            tokens, position, context_text
        ):
            if not _rank_licensed(token, ranks):
                report(SEVERITY_ERROR, REASON_INVENTED_SENIORITY)
            continue

        if token in supported:
            continue

        # A name: capitalised mid-line, internally punctuated, a known
        # technology in any case at any position, or written in a script with
        # no case to judge by. Somewhere else in the profile, or nowhere at
        # all — the first is a reader's judgment call, the second an invention.
        if (
            _is_caseless(token)
            or token in _TECHNOLOGY_NAMES
            or _looks_like_a_name(token, text)
        ):
            if token in index.vocabulary:
                report(SEVERITY_WARNING, REASON_BORROWED_NAME)
            else:
                report(SEVERITY_ERROR, REASON_INVENTED_NAME)
            continue

        if token in _CONNECTIVE:
            continue

        report(SEVERITY_WARNING, REASON_REWORDED)


#: Words and contractions that deny what they attach to.
_NEGATION = re.compile(
    # "zero" counts: "with zero downtime" rewritten "with no downtime" is the
    # same statement, and without it the rewrite was an added negation.
    r"\b(?:not|no|never|none|neither|nor|without|cannot|nothing|nobody|zero)\b|n['’]t\b",
    re.IGNORECASE,
)


def _negations(value: str | None) -> int:
    return len(_NEGATION.findall(value or ""))


def _rank_licensed(token: str, ranks: set[str]) -> bool:
    """A seniority token is licensed when it, or every rank word inside a
    compound of it ("senior-level", "co-founder"), is in the licensing set."""
    if token in ranks:
        return True
    parts = [part for part in _COMPOUND_PART.split(token) if part in _SENIORITY_TITLES]
    return bool(parts) and all(part in ranks for part in parts)


def _check_contact(
    findings: list[Finding], index: SnapshotIndex, contact: TailoredContact
) -> None:
    """Who the resume says it is from, against who the profile says it is.

    Every field here is transcribed, so every field is checked the way a company
    name is checked: exact bar case and whitespace. That is what makes carrying
    contact details safe at all. A generator that "tidies" a phone number into
    international format, or title-cases a name the resume shouted, has changed
    a fact about the person the document is addressed from — and a resume with
    someone else's email on it is worse than one with no email at all.

    And every contact column the version HOLDS must be present. Before, a
    document could leave any of them — or the whole block — out and pass, which
    is the resume-with-no-name defect this schema was widened to end. Links are
    not required: which of them belong in a header is a selection.

    Links are checked by URL against the version's own link rows. A label may be
    dropped, because a header without a caption is a formatting choice; a label
    that CONTRADICTS the stored one is an error, because "GitHub" pointing at a
    stranger's profile is the same class of mistake as an invented employer.
    """
    for name in CONTACT_FIELDS:
        if getattr(contact, name) is None and _has_text(index.contact[name]):
            # Omission is not a free pass here, as it is for a job's location:
            # a resume without the candidate's name or email cannot be replied
            # to, and every one of these columns is the user's own, confirmed.
            findings.append(
                Finding(
                    SEVERITY_ERROR,
                    "contact",
                    f"contact.{name}",
                    None,
                    REASON_CONTACT_MISSING,
                )
            )
            continue
        _check_copied_field(
            findings,
            section="contact",
            path=f"contact.{name}",
            generated=getattr(contact, name),
            stored=index.contact[name],
            reason=REASON_CONTACT_ALTERED,
        )

    for position, link in enumerate(contact.links):
        if link.url not in index.links:
            findings.append(
                Finding(
                    SEVERITY_ERROR,
                    "contact",
                    f"contact.links[{position}]",
                    link.url[:600],
                    REASON_UNKNOWN_LINK,
                )
            )
            continue
        _check_copied_field(
            findings,
            section="contact",
            path=f"contact.links[{position}].label",
            generated=link.label,
            stored=index.links[link.url],
            reason=REASON_CONTACT_ALTERED,
        )


def stored_titles(index: SnapshotIndex) -> dict[str, str]:
    """Folded job title -> the title exactly as stored, for every experience.

    Public because the repair pass restores a headline from it: the headline is
    a COPIED field, and the snapshot is the only place its right value lives.
    """
    titles: dict[str, str] = {}
    for row in index.experiences.values():
        if row.job_title:
            titles.setdefault(_normalise(row.job_title), row.job_title)
    return titles


def _check_headline(findings: list[Finding], index: SnapshotIndex, headline: TailoredLine) -> None:
    """The line under the name, which is a job title the candidate holds.

    It used to be checked as prose — quoted from somewhere, reworded at will —
    and "Principal Backend Engineer" citing "Senior Backend Engineer" was a
    WARNING: a promotion, in the second-largest type on the page, that did not
    withhold the file. A headline is a title, and a title is transcribed. So
    its text must equal (bar case and whitespace) one of the stored job titles.
    Which one is not pinned: the headline stands on the whole career rather
    than on one row, and "most recent" is the generator's choice to make.

    When it is a stored title, the citation is still checked like any line's:
    a real title with a fabricated citation is still a fabricated citation.
    """
    titles = stored_titles(index)
    title = titles.get(_normalise(headline.text))
    if title is None:
        findings.append(
            Finding(
                SEVERITY_ERROR,
                "headline",
                "headline.text",
                headline.text,
                REASON_HEADLINE_NOT_A_TITLE,
            )
        )
        # One accurate error. Word-by-word findings on a line whose whole text
        # is the wrong kind of thing would all say the same thing again.
        return
    _check_line(
        findings, index, headline,
        section="headline",
        path="headline",
        haystack=_snapshot_field(index, headline),
        identity=(title,),
    )


def _check_experience(
    findings: list[Finding], index: SnapshotIndex, entry: TailoredExperience, position: int
) -> None:
    path = f"experience[{position}]"
    row = index.experiences.get(entry.source_id)
    if row is None:
        findings.append(
            Finding(SEVERITY_ERROR, "experience", path, entry.job_title, REASON_UNKNOWN_SOURCE)
        )
        return

    _check_copied_field(
        findings, section="experience", path=f"{path}.company",
        generated=entry.company, stored=row.company,
    )
    _check_copied_field(
        findings, section="experience", path=f"{path}.job_title",
        generated=entry.job_title, stored=row.job_title,
    )
    _check_copied_field(
        findings, section="experience", path=f"{path}.location",
        generated=entry.location, stored=row.location,
    )
    _check_copied_date(
        findings, section="experience", path=f"{path}.start_date",
        generated=entry.start_date, stored=row.start_date,
    )
    _check_copied_date(
        findings, section="experience", path=f"{path}.end_date",
        generated=entry.end_date, stored=row.end_date,
    )
    # Experience precision was the hole education's presence here made obvious.
    # `DeterministicTailor` copies it off the row, so this has never fired for
    # the default generator; a model shown a start date and no precision has
    # nothing to copy, and the renderer fills the gap with a month.
    _check_copied_precision(
        findings, section="experience", path=f"{path}.start_date_precision",
        generated=entry.start_date_precision, stored=row.start_date_precision,
    )
    _check_copied_precision(
        findings, section="experience", path=f"{path}.end_date_precision",
        generated=entry.end_date_precision, stored=row.end_date_precision,
    )

    columns: dict[str, str | None] = {
        "description": row.description,
        "job_title": row.job_title,
        "company": row.company,
        "location": row.location,
    }
    identity = (row.job_title, row.company, row.location)
    for bullet_position, bullet in enumerate(entry.bullets):
        _check_line(
            findings, index, bullet,
            section="experience",
            path=f"{path}.bullets[{bullet_position}]",
            haystack=columns.get(bullet.source_field),
            identity=identity,
            context=(row.description,),
        )


def _check_project(
    findings: list[Finding], index: SnapshotIndex, entry: TailoredProject, position: int
) -> None:
    path = f"projects[{position}]"
    row = index.projects.get(entry.source_id)
    if row is None:
        findings.append(
            Finding(SEVERITY_ERROR, "projects", path, entry.name, REASON_UNKNOWN_SOURCE)
        )
        return

    _check_copied_field(
        findings, section="projects", path=f"{path}.name",
        generated=entry.name, stored=row.name,
    )
    _check_copied_field(
        findings, section="projects", path=f"{path}.role",
        generated=entry.role, stored=row.role,
    )

    listed = {_normalise(technology.name) for technology in row.technologies}
    for technology in entry.technologies:
        if _normalise(technology) in listed:
            continue
        findings.append(
            Finding(
                SEVERITY_ERROR, "projects", f"{path}.technologies",
                technology, REASON_UNLISTED_TECHNOLOGY,
            )
        )

    columns: dict[str, str | None] = {
        "description": row.description,
        "name": row.name,
        "role": row.role,
    }
    # Technologies are part of what a project bullet may name: they are the
    # project's own transcribed data, exactly as a company name is an
    # experience's. The description is context only — see `_check_line`.
    identity = (row.name, row.role) + tuple(t.name for t in row.technologies)
    for bullet_position, bullet in enumerate(entry.bullets):
        _check_line(
            findings, index, bullet,
            section="projects",
            path=f"{path}.bullets[{bullet_position}]",
            haystack=columns.get(bullet.source_field),
            identity=identity,
            context=(row.description,),
        )


def _check_education(
    findings: list[Finding], index: SnapshotIndex, entry: TailoredEducation, position: int
) -> None:
    path = f"education[{position}]"
    row = index.education.get(entry.source_id)
    if row is None:
        findings.append(
            Finding(SEVERITY_ERROR, "education", path, entry.institution, REASON_UNKNOWN_SOURCE)
        )
        return

    _check_copied_field(
        findings, section="education", path=f"{path}.institution",
        generated=entry.institution, stored=row.institution,
    )
    _check_copied_field(
        findings, section="education", path=f"{path}.degree",
        generated=entry.degree, stored=row.degree,
    )
    _check_copied_field(
        findings, section="education", path=f"{path}.field_of_study",
        generated=entry.field_of_study, stored=row.field_of_study,
    )
    _check_copied_date(
        findings, section="education", path=f"{path}.start_date",
        generated=entry.start_date, stored=row.start_date,
    )
    _check_copied_date(
        findings, section="education", path=f"{path}.end_date",
        generated=entry.end_date, stored=row.end_date,
    )
    # The precision travels with the date and is checked with it. A document
    # that promotes "2015" to "January 2015" states a month the profile does
    # not hold, and it does so in the one field a renderer reads directly.
    _check_copied_precision(
        findings, section="education", path=f"{path}.start_date_precision",
        generated=entry.start_date_precision, stored=row.start_date_precision,
    )
    _check_copied_precision(
        findings, section="education", path=f"{path}.end_date_precision",
        generated=entry.end_date_precision, stored=row.end_date_precision,
    )


def _check_certification(
    findings: list[Finding], index: SnapshotIndex, entry: TailoredCertification, position: int
) -> None:
    path = f"certifications[{position}]"
    row = index.certifications.get(entry.source_id)
    if row is None:
        findings.append(
            Finding(SEVERITY_ERROR, "certifications", path, entry.name, REASON_UNKNOWN_SOURCE)
        )
        return

    _check_copied_field(
        findings, section="certifications", path=f"{path}.name",
        generated=entry.name, stored=row.name,
    )
    _check_copied_field(
        findings, section="certifications", path=f"{path}.issuing_organization",
        generated=entry.issuing_organization, stored=row.issuing_organization,
    )
    _check_copied_date(
        findings, section="certifications", path=f"{path}.issue_date",
        generated=entry.issue_date, stored=row.issue_date,
    )


def validate(
    document: TailoredResumeDocument,
    version: ProfileVersion,
    *,
    job_requirement_labels: frozenset[str] | None = None,
) -> GroundingReport:
    """Check a generated document against the snapshot it was generated from.

    `job_requirement_labels` is optional and is used for one thing only:
    `targeted_requirements` claims the document aimed at certain requirements,
    and a label that is not in the posting is a claim about the JOB rather than
    about the candidate. It is a warning, never an error — being wrong about
    what you optimised for does not make the document untrue.
    """
    index = index_snapshot(version)
    findings: list[Finding] = []

    has_content = bool(
        document.experience
        or document.projects
        or document.education
        or document.certifications
    )
    if not has_content:
        findings.append(
            Finding(SEVERITY_ERROR, "document", None, None, REASON_EMPTY_DOCUMENT)
        )

    if document.contact is not None:
        _check_contact(findings, index, document.contact)
    elif any(_has_text(value) for value in index.contact.values()):
        # One error for the block rather than one per column: the repair is one
        # action (restore the block), and four findings would say it four times.
        findings.append(
            Finding(SEVERITY_ERROR, "contact", "contact", None, REASON_CONTACT_MISSING)
        )

    if document.headline is not None:
        _check_headline(findings, index, document.headline)

    # A summary stands on the whole career, so a rank it states may come from
    # any title the candidate holds — never a number, which stays per quote.
    titles = frozenset(
        token for row in index.experiences.values() for token in _tokens(row.job_title)
    )
    for position, line in enumerate(document.summary):
        _check_line(
            findings, index, line,
            section="summary",
            path=f"summary[{position}]",
            haystack=_snapshot_field(index, line),
            titles=titles,
        )

    shown_skills: set[str] = set()
    stored_categories = {
        _normalise(category) for category in index.skill_categories.values() if category and category.strip()
    }
    for group_position, group in enumerate(document.skills):
        # The heading first, addressed by POSITION: its text is generator
        # output and may contain anything, including the dots and brackets the
        # repair pass reads addresses with. The names below keep the
        # `skills.<category>` path they have always had.
        heading = _normalise(group.category)
        if heading not in index.skill_headings:
            findings.append(
                Finding(
                    SEVERITY_ERROR,
                    "skills",
                    f"skills[{group_position}].category",
                    group.category,
                    REASON_UNKNOWN_SKILL_GROUP,
                )
            )
        elif heading in stored_categories and any(
            (stored := index.skill_categories.get(_normalise(name))) is not None
            and _normalise(stored) != heading
            for name in group.skills
        ):
            # A stored category is a claim about the skills under it. Profiles
            # read from labelled lists store proficiency as the category
            # ("Expert", "Familiar"), and "Expert: Python, Go" for a Go stored
            # as Familiar claimed expert Go — the "Expert, 10+ years" invention
            # the heading check exists for, through a heading it accepted. The
            # same regrouping repair files each skill under its own category.
            findings.append(
                Finding(
                    SEVERITY_ERROR,
                    "skills",
                    f"skills[{group_position}].category",
                    group.category,
                    REASON_SKILL_UNDER_WRONG_HEADING,
                )
            )
        for skill_position, name in enumerate(group.skills):
            key = _normalise(name)
            if key in shown_skills:
                # Addressed by position, not by name: the name is the same as
                # the copy that stays, and only this occurrence is to go.
                findings.append(
                    Finding(
                        SEVERITY_ERROR,
                        "skills",
                        f"skills[{group_position}].skills[{skill_position}]",
                        name,
                        REASON_DUPLICATE_SKILL,
                    )
                )
            shown_skills.add(key)
            if key in index.skill_names:
                continue
            findings.append(
                Finding(SEVERITY_ERROR, "skills", f"skills.{group.category}", name, REASON_UNLISTED_SKILL)
            )

    sections = (
        ("experience", document.experience, _check_experience),
        ("projects", document.projects, _check_project),
        ("education", document.education, _check_education),
        ("certifications", document.certifications, _check_certification),
    )
    for section, entries, check in sections:
        seen: set[int] = set()
        for position, entry in enumerate(entries):
            if entry.source_id in seen:
                # The same row twice is one true entry printed twice — or, when
                # the second copy carries another job's text under the first
                # job's id, a job the repair pass would "restore" into a clone.
                # Reported once, on the later copy, and not checked further:
                # its own findings would only describe a copy that is going.
                findings.append(
                    Finding(
                        SEVERITY_ERROR,
                        section,
                        f"{section}[{position}]",
                        _entry_label(entry),
                        REASON_DUPLICATE_ENTRY,
                    )
                )
                continue
            seen.add(entry.source_id)
            check(findings, index, entry, position)

    _check_career(findings, index, document.experience, _printed_lines(document))
    _check_repeated_lines(findings, document)

    if job_requirement_labels is not None:
        listed = {_normalise(label) for label in job_requirement_labels}
        for label in document.targeted_requirements:
            if _normalise(label) in listed:
                continue
            findings.append(
                Finding(SEVERITY_WARNING, "targeting", "targeted_requirements", label, REASON_UNTARGETED)
            )

    return GroundingReport(findings=tuple(findings))


def _check_repeated_lines(findings: list[Finding], document: TailoredResumeDocument) -> None:
    """The same sentence printed twice anywhere on the page.

    The first quality defect this domain ever diagnosed: the summary was three
    of the bullets printed two inches below it. Nothing false, and every copy
    grounded — but a document that repeats itself has wasted the two seconds a
    reader gives it, and a model told not to still does.

    Which copy is kept matters, because the error is repaired by removing the
    copy it sits on. Bullets are kept over the summary, and among bullets the
    earlier on the page (experience before projects). The summary is the one
    optional, connective part of the page: putting the error on the BULLET, as
    the first version did, removed a job's only bullet and left the job
    bulletless — an error no repair can fix, so a sendable document was
    withheld to save a summary sentence.

    A line that already carries an error of its own is never the copy kept: the
    repair removes it anyway, and counting it as "printed" then removed the
    grounded copy too — a true sentence gone from the page, with a note telling
    the user it "already appears earlier".

    Compared by `_normalise`, the same comparison the evaluation's
    no-duplicate-lines criterion uses. Empty lines are skipped here: they carry
    their own error, and two blanks are not "the same sentence".
    """
    lines: list[tuple[str, str, TailoredLine]] = []
    for section, entries in (("experience", document.experience), ("projects", document.projects)):
        entries_seen: set[int] = set()
        for position, entry in enumerate(entries):
            # A later copy of a whole entry is REASON_DUPLICATE_ENTRY and goes
            # whole; its bullets repeating the first copy's is the same fact.
            if entry.source_id in entries_seen:
                continue
            entries_seen.add(entry.source_id)
            lines.extend(
                (section, f"{section}[{position}].bullets[{bullet}]", line)
                for bullet, line in enumerate(entry.bullets)
            )
    lines.extend(
        ("summary", f"summary[{position}]", line)
        for position, line in enumerate(document.summary)
    )

    doomed = [
        finding.field_path
        for finding in findings
        if finding.severity == SEVERITY_ERROR and finding.field_path
    ]

    def removed_anyway(path: str) -> bool:
        return any(
            other == path or path.startswith(other + ".") or path.startswith(other + "[")
            or other.startswith(path + ".")
            for other in doomed
        )

    printed: set[str] = set()
    for section, path, line in lines:
        key = _normalise(line.text)
        if not key:
            continue
        if key in printed:
            findings.append(
                Finding(SEVERITY_ERROR, section, path, line.text, REASON_DUPLICATE_LINE)
            )
            continue
        if not removed_anyway(path):
            printed.add(key)


def experience_ranks(index: SnapshotIndex) -> dict[int, int]:
    """Experience id -> its place in the profile's own order.

    The profile's order is the order of `version.experiences` — most recent
    start first, the order every profile screen and the prompt show. Jobs that
    started on the same date and sit next to each other share a rank: the
    profile has no order between them to be out of. Public because the repair
    pass sorts by exactly this, and two notions of "in order" would disagree.
    """
    ranks: dict[int, int] = {}
    previous = None
    rank = -1
    for position, row in enumerate(index.experiences.values()):
        if previous is None or row.start_date != previous.start_date:
            rank = position
        ranks[row.id] = rank
        previous = row
    return ranks


def _check_career(
    findings: list[Finding],
    index: SnapshotIndex,
    entries: list[TailoredExperience],
    printed: frozenset[str] = frozenset(),
) -> None:
    """The career as a whole: every job, once, in order, each with something under it.

    The per-entry checks cannot see any of this. Each job can be individually
    impeccable while the document omits one, prints another twice, or reverses
    them — and the repair pass made the last case worse, not better: a document
    whose two jobs swapped ids had every identity field "restored" from the
    wrong row and every bullet removed as a citation of the wrong description,
    and was delivered as two bulletless jobs in reverse order, as "passed".

    Four errors, two of which no repair can address by design:

      * a MISSING job cannot be added back — adding is the one thing the repair
        pass may never do — so the document fails. A gap in a career reads as
        one being hidden;
      * OUT OF ORDER is repaired by permuting the entries the document already
        has into the profile's order (`experience_ranks`);
      * a job with NO BULLETS, when its row holds quotable text, cannot be given
        some — so a repair that would leave one bulletless is not applied, and
        the model's document is withheld rather than delivered hollow;
      * a DUPLICATE is reported where the entries are walked, above.
    """
    ranks = experience_ranks(index)
    present: list[tuple[int, TailoredExperience]] = []
    seen: set[int] = set()
    for position, entry in enumerate(entries):
        if entry.source_id in seen or entry.source_id not in ranks:
            continue
        seen.add(entry.source_id)
        present.append((position, entry))

    for row in index.experiences.values():
        if row.id not in seen:
            findings.append(
                Finding(
                    SEVERITY_ERROR,
                    "experience",
                    "experience",
                    f"{row.job_title}, {row.company}",
                    REASON_MISSING_EXPERIENCE,
                )
            )

    order = [ranks[entry.source_id] for _, entry in present]
    if any(earlier > later for earlier, later in zip(order, order[1:])):
        findings.append(
            Finding(
                SEVERITY_ERROR,
                "experience",
                "experience.order",
                ", ".join(entry.company for _, entry in present),
                REASON_EXPERIENCE_OUT_OF_ORDER,
            )
        )

    for position, entry in present:
        if not entry.bullets and _has_quotable_content(
            index.experiences[entry.source_id], printed
        ):
            findings.append(
                Finding(
                    SEVERITY_ERROR,
                    "experience",
                    f"experience[{position}].bullets",
                    entry.company,
                    REASON_NO_BULLETS,
                )
            )


def _printed_lines(document: TailoredResumeDocument) -> frozenset[str]:
    """Every sentence the document prints, as the duplicate-line check compares them."""
    lines = [line.text for line in document.summary]
    lines += [bullet.text for entry in document.experience for bullet in entry.bullets]
    lines += [bullet.text for project in document.projects for bullet in project.bullets]
    return frozenset(_normalise(text) for text in lines if text and text.strip())


def _has_quotable_content(row, printed: frozenset[str] = frozenset()) -> bool:
    """Whether a stored job holds any text a bullet could quote that the page does
    not already print.

    The second half matters because the duplicate-line rule and this one meet
    on a real profile shape: a sentence written verbatim under two jobs. The
    page may print it once, so the later job can be left with nothing new to
    say — and a job whose only text is already on the page is honestly
    bulletless, not a hollowed-out career. Without it the two rules together
    would withhold the deterministic generator's document for that profile,
    and that generator is never repaired.

    Defined by the deterministic generator's OWN clause splitter, deliberately:
    `DeterministicTailor` keeps at least two of `_clauses(description)` for every
    job, so a job it leaves bulletless is exactly one for which this is False —
    and the no-bullets error can never fire on the generator that copies
    verbatim. A second definition written here (say, "the description is not
    empty") would disagree on a description holding only a fragment, and the
    default generator would start failing grounding on real profiles.

    Imported at call time rather than at module load, so that loading this
    module still pulls in nothing that ranks or generates. The splitter is pure
    text segmentation; nothing about the posting reaches the verdict through it.
    """
    from app.services.tailoring.generation import _clauses

    return any(_normalise(clause) not in printed for clause in _clauses(row.description))


def _entry_label(entry) -> str | None:
    """What to call an entry in a finding: its employer, name or institution."""
    for attribute in ("company", "name", "institution"):
        value = getattr(entry, attribute, None)
        if value:
            return value
    return None


def _snapshot_field(index: SnapshotIndex, line: TailoredLine) -> str:
    """The haystack for a line that cites no single row.

    The headline and the summary are about the career rather than about one job,
    so their quote is looked for across every row's text of that field name. It
    still has to be somewhere — "anywhere in your own profile" is a real bar,
    and it is the widest one this module ever applies.

    `source_field` is generator output, so it is any string of up to 32
    characters — including the name of a column that holds no text (`id`,
    `start_date`) or of a mapper internal (`metadata`, `registry`). Those are
    not haystacks and never were; the guard below is what stops them being
    joined into one. Without it the join raised TypeError, which
    `pipeline._run_stages` could only turn into a FAILED run — and this stage
    is the one stage that must not fail a run, because a document nobody can
    vouch for is a verdict about the document, not a processing error. A
    non-text field now contributes nothing, so the quote is simply not found
    and the line earns REASON_QUOTE_NOT_FOUND like any other bad citation.
    """
    field = line.source_field

    # The version's own summary column. It is not on any child row, so the loop
    # below cannot reach it however many rows it walks — and the candidate's
    # own summary paragraph is exactly what a summary line should be quoting.
    if field == "summary":
        return index.summary or ""

    parts: list[str] = []

    def text_of(row) -> str:
        # A mapped column, never a relationship or a mapper internal: reaching
        # `profile_version` or `technologies` through getattr would also emit a
        # lazy SELECT that untrusted output asked for and nothing reads.
        if field not in type(row).__table__.columns:
            return ""
        value = getattr(row, field, None)
        return value if isinstance(value, str) else ""

    for row in index.experiences.values():
        parts.append(text_of(row))
    for row in index.projects.values():
        parts.append(text_of(row))
    for row in index.education.values():
        parts.append(text_of(row))
    for row in index.certifications.values():
        parts.append(text_of(row))

    return "\n".join(part for part in parts if part)
