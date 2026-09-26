"""One narrow inflection table, for work-action words only.

The real-data benchmark found a single requirement that matching_v2 answered
wrongly for a purely grammatical reason: the posting asked for "experience
leading and mentoring a team" and the profile said "Lead four engineers".
`canonical()` compares surface forms, `leading` is not `lead`, and the verdict
came back MISSING for a candidate who plainly does the thing.

**A general stemmer was measured and rejected.** Applying a naive
suffix-stripping rule to `vocabulary.TECHNOLOGIES` and `normalization.ALIASES`
destroys both:

    spring     -> spr          the Java framework, gone
    redis      -> redi
    kubernetes -> kubernete
    pandas     -> panda
    express    -> expres
    postgres   -> postgre      every Postgres alias, gone
    k8s        -> k8
    js         -> j
    websockets -> websocket    two vocabulary entries collapsed into one

Forty-two technology entries and fourteen alias keys end in -s, -ed or -ing.
Stemming is the wrong instrument for a vocabulary of proper nouns, and the
damage would be silent: a technology that stopped being recognised produces a
MISSING nobody traces back to a suffix rule.

So this module is not a stemmer. It is an explicit table of the inflections of
a small set of WORK-ACTION words — the verbs a posting uses to describe what
someone did, which are exactly the words that vary and exactly the words no
technology is named after. Every entry is listed by hand and every entry is a
claim that two spellings are one concept, reviewable one line at a time, in the
same posture `normalization.ALIASES` takes toward equivalence.

**Where it applies, and where it must not.** This table is consulted from one
place: the scan for a requirement that names no technology at all
(`rules._judge_unmeasured_non_technology`). Skill matching, technology
matching, subject resolution, aliases, certifications and education never see
it. That is structural rather than conventional — the auxiliary index lives on
its own attribute and nothing else reads it — and
`test_matching_morphology.py` asserts the boundary both ways.
"""

from __future__ import annotations

#: The inflections, spelled out. Keys are surface forms as `canonical()`
#: produces them; values are the lemma the concept is filed under.
#:
#: The bar for adding an entry, and it is deliberately high:
#:
#:   1. It must be a verb (or a directly derived noun) describing what a person
#:      DID at work — the vocabulary of role descriptions, not of tools.
#:   2. Neither the key nor the lemma may be, or resemble, a technology name.
#:      `assert_disjoint_from_technologies()` enforces this at import.
#:   3. The two forms must mean the same thing in a CV with no reading on which
#:      they differ. "led" and "lead" pass. "learning" and "learn" do NOT —
#:      "machine learning" is a field and "learning Rust" is an admission, and
#:      `vocabulary.NON_DOING_MARKERS` already treats the latter as a weakening.
ACTION_LEMMAS: dict[str, str] = {
    # --- leading people ---
    "leading": "lead",
    "leads": "lead",
    "led": "lead",
    "leadership": "lead",
    # --- managing ---
    "managing": "manage",
    "manages": "manage",
    "managed": "manage",
    "management": "manage",
    # --- mentoring ---
    "mentoring": "mentor",
    "mentors": "mentor",
    "mentored": "mentor",
    "mentorship": "mentor",
    # --- coaching and supervising ---
    "coaching": "coach",
    "coached": "coach",
    "coaches": "coach",
    "supervising": "supervise",
    "supervised": "supervise",
    "supervises": "supervise",
    # --- building and developing ---
    "building": "build",
    "builds": "build",
    "built": "build",
    "developing": "develop",
    "develops": "develop",
    "developed": "develop",
    # --- designing and architecting ---
    "designing": "design",
    "designs": "design",
    "designed": "design",
    "architecting": "architect",
    "architected": "architect",
    # --- maintaining and owning ---
    "maintaining": "maintain",
    "maintains": "maintain",
    "maintained": "maintain",
    "owning": "own",
    "owns": "own",
    "owned": "own",
    # --- delivering and shipping ---
    "delivering": "deliver",
    "delivers": "deliver",
    "delivered": "deliver",
    "shipping": "ship",
    "ships": "ship",
    "shipped": "ship",
    # --- the people and groups those verbs act on ---
    "teams": "team",
    "engineers": "engineer",
    "developers": "developer",
    "reports": "report",
}

#: The lemmas themselves, so a profile that already says "lead" is filed under
#: the same key as one that says "leading".
LEMMA_TARGETS: frozenset[str] = frozenset(ACTION_LEMMAS.values())

#: Every form this table knows, in either direction.
KNOWN_FORMS: frozenset[str] = frozenset(ACTION_LEMMAS) | LEMMA_TARGETS


def action_lemma(token: str) -> str | None:
    """The lemma for a work-action word, or None if this is not one.

    None is the common case and the safe one: a token this table has never
    heard of is left exactly as it is, so the only words morphology can affect
    are the ones written down above.
    """
    if not token or " " in token:
        # Multi-word phrases are never inflected as a unit here. "machine
        # learning" must stay "machine learning".
        return None
    if token in ACTION_LEMMAS:
        return ACTION_LEMMAS[token]
    if token in LEMMA_TARGETS:
        return token
    return None


def same_action(left: str, right: str) -> bool:
    """Whether two tokens are the same work-action concept."""
    left_lemma, right_lemma = action_lemma(left), action_lemma(right)
    return left_lemma is not None and left_lemma == right_lemma


def assert_disjoint_from_technologies(forms: frozenset[str] | set[str] = KNOWN_FORMS) -> None:
    """No form in this table may collide with a technology or an alias.

    Checked at import rather than only in a test, because the failure it
    prevents is silent: a table entry that shadowed a technology name would
    change verdicts in a path nobody is looking at, and the import is the last
    moment at which both vocabularies are certainly loaded.

    `forms` is a parameter so a test can prove the guard actually fires without
    mutating this module's globals — a mutation that leaked into a later test
    is how the first version of that test failed.
    """
    from app.services.matching.normalization import ALIASES
    from app.services.matching.vocabulary import TECHNOLOGIES

    collisions = set(forms) & (TECHNOLOGIES | set(ALIASES) | set(ALIASES.values()))
    if collisions:
        raise AssertionError(
            f"morphology table collides with the technology vocabulary: {sorted(collisions)}. "
            f"A work-action word must never be a technology name."
        )


assert_disjoint_from_technologies()


__all__ = [
    "ACTION_LEMMAS",
    "assert_disjoint_from_technologies",
    "KNOWN_FORMS",
    "LEMMA_TARGETS",
    "action_lemma",
    "same_action",
]
