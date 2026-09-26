"""Step 1 of Matching V5: dividing a clause into scope-bounded spans.

`vocabulary.is_non_doing` grades one clause — a `.;!?\n`-delimited piece of a
description — as a single boolean, and `profile_index.ProfileIndex` broadcasts
that boolean to every technology token the clause names (see
`profile_index._index_employment`). The `non_doing_v1` benchmark's
`technology_attribution` and `clause_context` cases showed this is too coarse
whenever a clause contains BOTH a caveat and its own resolution — "Deprecated
the legacy PHP monolith while building its replacement in Go, which now
serves all production traffic" is one clause and two claims.

This module divides a clause into **spans** at the first point after each
marker where a new claim plausibly begins — a comma that opens an
independent-looking continuation, or one of a small curated set of
contrastive/subordinating connectors. Nothing here computes a verdict:
`is_non_doing` is still called once per SPAN by the caller, unmodified. This
module only decides where the boundaries are.

Deliberately marker-relative, not "every comma is a new span": a clause with
no marker returns unchanged as a single span, and a marker with no following
scope-reset point also returns unchanged. That is what makes this a strict
refinement of matching_v4's existing behaviour rather than a new source of
invented verdicts — see the module docstring of `profile_index.py` for the
one place this changes, and `docs/matching-v5-implementation-design.md` §2.1
and §3.1 for why unconditional comma-splitting was rejected (it would split
"Redis, evaluated for six months, was never adopted." into "Redis" and
"evaluated for six months", isolating Redis from the marker that actually
describes it).
"""

from __future__ import annotations

import re

from app.services.matching.normalization import canonical
from app.services.matching.vocabulary import (
    _MARKER_BEARING,
    _MULTIWORD_MARKERS,
    _SINGLE_MARKERS,
)

#: Words and phrases that plausibly end one claim and begin another, curated
#: with the same discipline as `vocabulary.NON_DOING_MARKERS`: each entry is
#: here because a specific non_doing_v1 case needs it (see the design doc's
#: §4 table), not because it seemed generally plausible.
#:
#: Deliberately excludes bare "and"/"or". Those are conjunctive — they extend
#: one claim across a list ("Redis and Memcached were both evaluated and
#: rejected") — while every entry below is contrastive or resolving: it
#: introduces a DIFFERENT claim, which is the property a span boundary needs.
SCOPE_RESET_CONNECTORS: frozenset[str] = frozenset(
    {
        "but", "while", "though", "although", "before", "after",
        "instead", "and then", "and ultimately", "in favor of",
    }
)

_WORD = re.compile(r"[a-z]+(?:-[a-z]+)+|[a-z]+")

_MULTIWORD_MARKER_RE = (
    re.compile("|".join(re.escape(m) for m in sorted(_MULTIWORD_MARKERS, key=len, reverse=True)))
    if _MULTIWORD_MARKERS
    else None
)

_CONNECTOR_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(c) for c in sorted(SCOPE_RESET_CONNECTORS, key=len, reverse=True)),
    re.IGNORECASE,
)


def find_marker_span(text: str) -> tuple[int, int] | None:
    """The (start, end) of the first NON_DOING_MARKERS match in `text`, or
    None.

    Reuses `vocabulary`'s own `_SINGLE_MARKERS`, `_MULTIWORD_MARKERS` and
    `_MARKER_BEARING` — the same objects `is_non_doing` reads — so a name
    already protected there (`scikit-learn`, `machine learning`, ...) can
    never be the reason a split happens here either. Positions are into a
    length-preserving casefolded, name-neutralised copy of `text`, so they
    index directly into the original string: `_MARKER_BEARING` matches are
    replaced with spaces of the same length rather than collapsed, which
    `is_non_doing` does not need to do (it only needs a boolean) but this
    function does, to keep offsets valid.

    A simplified relative of `is_non_doing`'s own matching, not a citation of
    it: it skips `vocabulary._SEPARATOR_FOLD`'s exotic-dash handling. That is
    safe here specifically because this function only ever decides WHERE a
    split MIGHT happen — every resulting span is still graded by the real,
    unmodified `is_non_doing`, so an over-cautious miss here simply means no
    split happens (the existing whole-clause behaviour), never a wrong
    answer. See `tests/test_matching_span_split.py` for the case this
    reasoning is pinned against.
    """
    lowered = text.casefold()
    neutralised = _MARKER_BEARING.sub(lambda m: " " * len(m.group(0)), lowered)

    candidates: list[tuple[int, int]] = []
    if _MULTIWORD_MARKER_RE is not None:
        match = _MULTIWORD_MARKER_RE.search(neutralised)
        if match:
            candidates.append((match.start(), match.end()))
    for match in _WORD.finditer(neutralised):
        if match.group(0) in _SINGLE_MARKERS:
            candidates.append((match.start(), match.end()))

    return min(candidates, key=lambda pair: pair[0]) if candidates else None


def find_next_scope_reset(text: str, after: int) -> int | None:
    """The earliest scope-reset point at or after `after`: either the
    character right after a comma, or the start of a `SCOPE_RESET_CONNECTORS`
    match. None if neither occurs in the remainder of `text`."""
    points: list[int] = []

    comma_at = text.find(",", after)
    if comma_at != -1:
        points.append(comma_at + 1)

    match = _CONNECTOR_RE.search(text, after)
    if match:
        points.append(match.start())

    return min(points) if points else None


def split_into_spans(clause: str) -> list[str]:
    """`clause`, divided at the first scope-reset point after each marker it
    contains, applied repeatedly to the remainder.

    A clause with no marker, or whose marker has no scope-reset point
    anywhere after it, comes back as a single-element list holding the
    clause unchanged — the exact input `is_non_doing` would have received
    before this module existed.
    """
    if not clause or not clause.strip():
        return []

    spans: list[str] = []
    remaining = clause
    while True:
        marker = find_marker_span(remaining)
        if marker is None:
            spans.append(remaining)
            break
        split_at = find_next_scope_reset(remaining, after=marker[1])
        if split_at is None:
            spans.append(remaining)
            break
        spans.append(remaining[:split_at])
        remaining = remaining[split_at:]
        if not remaining.strip():
            break

    return [span.strip() for span in spans if span.strip()]


# =============================================================================
# Step 2: nearest-technology-token reattachment
# =============================================================================
#
# A span produced by `split_into_spans` sometimes has no subject of its own:
# "which have handled every internal call in production since 2017" is a
# comment on a noun phrase from an EARLIER span, not an independent claim,
# and `phrase_variants` finds no technology token in it at all — "which" is
# not one. Left alone, the technology that trailing clause is actually about
# (`pl-005`'s gRPC, named only as the object of "on top of the existing...
# services" in the PRECEDING span) never receives this span's own, clean
# verdict.

_REL_OPENER = re.compile(r"^\s*,?\s*(which|that|who)\b", re.IGNORECASE)
_PARTICIPLE_OPENER = re.compile(r"^\s*,?\s*now\s+\w+ing\b", re.IGNORECASE)

#: Word boundaries good enough to rebuild 1/2/3-word windows the same way
#: `normalization.phrase_variants` does, but keeping each window's END
#: position — which `phrase_variants` itself has no reason to keep, since it
#: only ever answers "is this token present," never "where." Approximate by
#: design: it exists only to break a tie among several technologies named in
#: one span, never to decide whether a token is present at all — that
#: question is still answered exactly as everywhere else, by
#: `phrase_variants`.
_RAW_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#]*")


def is_reattachment_opener(span: str) -> bool:
    """Whether `span` opens with a relative pronoun or a bare, subjectless
    present participle — a modifier of a noun phrase from an earlier span,
    not a claim of its own."""
    return bool(_REL_OPENER.match(span) or _PARTICIPLE_OPENER.match(span))


def _token_end_positions(text: str, is_relevant) -> dict[str, int]:
    """canonical token -> end position of its LAST literal occurrence in
    `text`, found the same way `phrase_variants` finds tokens (1/2/3-word
    windows through `canonical()`), but keeping the position instead of
    discarding it, and restricted to tokens `is_relevant` accepts.

    The filter is load-bearing, not decoration: `canonical()` normalizes
    ANY text, including "services", "existing" and "layer" — the generic
    words `vocabulary.is_generic` exists to name — and without it, "on top
    of the existing gRPC **services**" hands back "services" as the nearest
    token, because it is the last word before the comma, not because it is
    a technology. Reattachment exists to find the technology a trailing
    clause modifies, not merely the last word of any kind.
    """
    words = list(_RAW_WORD.finditer(text))
    positions: dict[str, int] = {}
    for size in (1, 2, 3):
        for start in range(len(words) - size + 1):
            window = words[start : start + size]
            token = canonical(" ".join(w.group(0) for w in window))
            if not token or not is_relevant(token):
                continue
            end = window[-1].end()
            if token not in positions or end > positions[token]:
                positions[token] = end
    return positions


def nearest_preceding_token(spans_before: list[str], *, is_relevant) -> str | None:
    """The technology-shaped token a trailing relative clause or participle
    most plausibly refers to: the token with the latest position in the most
    recent EARLIER span (of the same original clause) that names one at
    all, restricted to tokens `is_relevant(token)` accepts. None if no
    earlier span names anything relevant.

    `is_relevant` is the caller's own notion of "a real subject" — in
    `profile_index.py` this is `vocabulary.is_technology`, closed over the
    profile's own declared tokens, exactly as `rules.resolve_technology_subject`
    already uses it. Passed in rather than imported here so this module
    stays free of any opinion about what counts as a technology; it only
    knows how to find and place tokens in text.

    "Nearest" is late closure — a relative clause attaches to the closest
    preceding noun phrase — the standard default for this kind of ambiguity,
    named as a deliberate, imperfect choice rather than a silently arbitrary
    one; see `docs/matching-v5-implementation-design.md` §2.2 and §5 for the
    counter-example it does not resolve (a relative clause that is itself
    the caveat about an affirmative-sounding earlier span).
    """
    for span in reversed(spans_before):
        positions = _token_end_positions(span, is_relevant)
        if positions:
            return max(positions, key=positions.get)
    return None


__all__ = [
    "SCOPE_RESET_CONNECTORS",
    "find_marker_span",
    "find_next_scope_reset",
    "is_reattachment_opener",
    "nearest_preceding_token",
    "split_into_spans",
]
