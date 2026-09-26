"""Step 3 of Matching V5: explicit negation, kept apart from NON_DOING_MARKERS.

`is_non_doing()` answers one question: does this clause contain one of a
curated list of verbs, nouns and adjectives about contemplating, planning or
retiring a technology (`evaluated`, `considered`, `deprecated`, `familiar`,
...)? The `non_doing_v1` benchmark's `explicit_non_use` cases showed that
question has a blind spot no amount of vocabulary curation of THAT list can
safely close: "Never used Kubernetes outside of a single tutorial" and "Zero
production hours with Rust to date" name no word from `NON_DOING_MARKERS` at
all, and read as demonstrating the work.

These are a genuinely different kind of signal, not a missing entry in the
same list, and the difference is worth stating precisely because the wrong
fix is the obvious one:

    marker-based non-doing   a CONTENT word naming an act of contemplation
                             or a stage of a technology's lifecycle. Fixed,
                             enumerable, closed under `NON_DOING_MARKERS`.

    negation                 a FUNCTION word (`not`, `never`, `no`, `zero`,
                             `nothing`, `unable`) that can attach to ANY
                             predicate, including affirmative ones —
                             "This is not a hobby project, it has been in
                             production for six years" contains "not" and is
                             unambiguously affirmative. A bare negator is
                             therefore never safe to add to a flat word list
                             the way `retired` or `curious` were: it has to
                             be recognised only when it scopes a specific,
                             small set of USE-predicates ("used", "worked
                             with", "experience", "shipped", ...), which is
                             a bigram/trigram PATTERN shape, not a word.

    magnitude of experience  a THIRD, separate question this module does not
                             answer: given a mention that IS non-doing (by
                             either of the above), how much of it was there —
                             "no production work with it" and "three weeks;
                             the rollout was not continued" are both explicit
                             about there being NONE or NOT ENOUGH, which a
                             careful reviewer reads as settling a duration
                             requirement (MISSING), while the tier model
                             today can only say UNCERTAIN. This needs a new
                             field on `Evidence`/`TokenEvidence` and a new
                             branch in `rules._judge_technology_duration`,
                             not a text pattern — see
                             `docs/matching-v5-implementation-design.md` §2.5.
                             Deliberately NOT attempted here.

`EXPLICIT_NEGATION_PATTERNS` is checked as an ADDITIONAL, independent boolean
at the span level, unioned with (never merged into, never replacing)
`vocabulary.is_non_doing`. `NON_DOING_MARKERS` itself is untouched.
"""

from __future__ import annotations

import re

#: Six patterns, each a (negator, use-predicate) pair within a bounded
#: window rather than a bare word, precisely because a bare negator is not
#: safe on its own (see the module docstring). Every pattern here exists
#: because a specific non_doing_v1 case needs it; see
#: tests/test_matching_negation.py for the case-by-case mapping and the
#: adversarial controls that must NOT fire.
EXPLICIT_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # sd-056 "Never used Kubernetes...", sd-060 "never wrote a line of it",
    # sd-057 "Has not worked with Rust..."
    re.compile(
        r"\b(never|not)\s+(used|worked(\s+with)?|wrote|touched|tried|shipped|deployed)\b",
        re.IGNORECASE,
    ),
    # sd-058 "No hands-on GraphQL experience", sd-062 "no experience with it",
    # sd-064 "no direct experience with Scala", sd-065 "Zero production hours
    # with Rust"
    re.compile(r"\b(no|zero|none)\b[\w\s-]{0,25}?\b(experience|exposure|hours)\b", re.IGNORECASE),
    # sd-059 "Does not have production experience with Elasticsearch."
    re.compile(r"\b(does|did)\s+not\s+have\b[\w\s-]{0,25}?\bexperience\b", re.IGNORECASE),
    # sd-061 "nothing has been done with it so far."
    re.compile(r"\bnothing\b[\w\s-]{0,20}?\bdone\b", re.IGNORECASE),
    # pl-029's second clause: "no Rust code has shipped yet."
    re.compile(r"\bno\b[\w\s-]{0,20}?\b(has|have)\b[\w\s-]{0,10}?\b(shipped|started|happened)\b", re.IGNORECASE),
    # sd-063 "Unable to find time to try Rust despite several attempts to start."
    re.compile(r"\bunable\s+to\b[\w\s-]{0,25}?\b(try|start|use|work)\b", re.IGNORECASE),
    # --- matching_v5_1: denials the six above missed --------------------------
    #
    # Each was found by an offline probe in which the whole denial was read as
    # PAID WORK: a role saying "Unfamiliar with Django." was cited as
    # "Demonstrated in paid work" for a Django requirement and counted toward
    # 3+ years of it. The patterns below are the same shape as the six above —
    # a negator bound to a small set of use-predicates, never a bare negator —
    # and each has an adversarial control in tests/test_matching_negation.py.
    #
    # An auxiliary with "not"/"n't" and a BASE or past use-verb: "Did not use
    # Django", "Didn't use Django", "Haven't used Django", "Did not work with
    # Django". The first pattern needs "never|not" directly before a PAST
    # form, so the auxiliary forms of English negation — the commonest ones —
    # all passed as affirmative. "try" and "ship" are deliberately absent:
    # "didn't try to hide the complexity" and "hasn't shipped already" are
    # ordinary affirmative prose (the latter is an existing control).
    re.compile(
        r"\b(?:(?:do|does|did|have|has|had)\s+not|(?:do|does|did|have|has|had)n['’]t)"
        r"\s+(?:use|used|work(?:ed)?\s+with|touch(?:ed)?)\b",
        re.IGNORECASE,
    ),
    # "Unfamiliar with Django." / "Not familiar with Kafka." — but only as the
    # opening of the span, which is where a resume bullet puts its (elided)
    # subject. Mid-sentence, "unfamiliar with" is as likely to describe other
    # people — non_doing_v1's control "onboarded new hires unfamiliar with the
    # codebase" must not fire, and does not, because it does not start there.
    re.compile(
        r"^\W*(?:(?:i\s+am|i['’]m|am)\s+)?(?:unfamiliar|not\s+familiar)\s+with\b",
        re.IGNORECASE,
    ),
    # "Lacks Django experience." / "Lacking production exposure to Go." — again
    # anchored at the span's start, so "addressed the team's lack of test
    # experience" (someone else's lack, mid-sentence) stays affirmative.
    re.compile(
        r"^\W*lack(?:s|ed|ing)?\b[\w\s./+#-]{0,30}?\b(?:experience|exposure|knowledge)\b",
        re.IGNORECASE,
    ),
    # "Django was never adopted." / "Kubernetes was not adopted by the team." /
    # "Never had the chance to use Rust." — a use-predicate the first pattern's
    # verb list does not include, and the "chance/opportunity" construction
    # that puts four words between the negator and the verb.
    re.compile(
        # "never been adopted" is deliberately NOT matched: "had never been
        # adopted before I introduced Terraform" is the candidate introducing
        # it, which is affirmative.
        r"\b(?:never|not)\s+adopted\b"
        r"|\bnever\s+had\s+(?:the\s+|an?\s+)?(?:chance|opportunity)\s+to\s+(?:use|work\s+with|try)\b",
        re.IGNORECASE,
    ),
)


def negation_fires(span: str) -> bool:
    """Whether `span` contains an explicit, plain-English denial of use.

    Independent of, and never a substitute for, `vocabulary.is_non_doing` —
    see the module docstring's three-way distinction. A caller that wants
    "does this span read as non-doing, marker or negation" computes
    `is_non_doing(span) or negation_fires(span)`, not one function that
    does both, so that a future reader can ask each question separately.
    """
    if not span:
        return False
    if any(pattern.search(span) for pattern in EXPLICIT_NEGATION_PATTERNS[:_V5_PATTERN_COUNT]):
        return True
    # The matching_v5_1 patterns deny a use-verb wherever it sits, so on their
    # own they also fired inside a relative or purpose clause about something
    # else — "Designed Go services that do not use sticky sessions", "Migrated
    # to Kubernetes, which the company had not adopted before", "Built Django
    # APIs so partner teams did not use the legacy gateway" — and demoted real
    # paid work. A denial the span subordinates to another clause is not a
    # denial of the candidate's use, so these four yield to such a word before
    # them. The six v5 patterns above are untouched.
    for pattern in EXPLICIT_NEGATION_PATTERNS[_V5_PATTERN_COUNT:]:
        for match in pattern.finditer(span):
            if not _SUBORDINATOR.search(span[: match.start()]):
                return True
    return False


#: How many of `EXPLICIT_NEGATION_PATTERNS` shipped in matching_v5; the rest are
#: matching_v5_1's, and only those yield to `_SUBORDINATOR`.
_V5_PATTERN_COUNT = 6

#: A word that makes what follows it a clause about something else.
_SUBORDINATOR = re.compile(
    r"\b(?:which|that|who|whom|whose|where|so|because|since|until|unless|before"
    r"|after|while|whereas|although|though)\b",
    re.IGNORECASE,
)


__all__ = ["EXPLICIT_NEGATION_PATTERNS", "negation_fires"]
