# Matching V5 implementation design

Design only — no production code, migration, `ALGORITHM_VERSION`, or
`non_doing_v1` ground truth was changed to produce this document. It builds
on `docs/matching-v5-architecture-analysis.md` and tightens that document's
four proposed components into exact interfaces, exact call sites, precedence
rules, and a file-by-file plan, using a **read-only** simulation
(`sim2.py`, run against the unmodified `is_non_doing`/`phrase_variants` and
the real `non_doing_v1` dataset) to check every claim below before writing it
down. Two things changed between the architecture analysis and this
document, both because the simulation caught them: `pl-009` turns out to be
closed by component 1, not component 2 (§2.1); and a real, previously
undocumented regression risk in the naive form of component 1 was found and
is fixed by a precedence rule in §3.3, not left as a hand-wave.

## 0. The master invariant

**Every new component only ever adds a signal; none of them override V4's
existing verdict for a token unless a specific, curated, matched trigger
fires.** A clause containing no marker at all is untouched — split into a
single "span" identical to the current whole clause — no matter what
connectors, commas or negation-shaped words it contains. A marker with no
recognized scope-reset point after it is also untouched. This is the concrete
meaning of "prefer UNCERTAIN over inventing evidence" for this design: an
unrecognized construction does not get a new, invented answer — it falls
through to exactly what V4 already does, which is never "invented" because
it is the system's own existing, already-reviewed baseline.

## 1. Where components sit in the pipeline

```
resolve_technology_subject()          UNCHANGED — 0 mismatches across all 38
   |                                   non_doing_v1 pipeline cases (verified
   |                                   by re-running it, again, for this
   |                                   document). No test demonstrates a
   |                                   defect here, so nothing proposes to
   |                                   touch it. See §7.
   v
Experience.description
   |
   +-- job_title (index 0)  ---------> COMPONENT 4 (title-aware indexing)
   |                                   replaces the ordinary marker check for
   |                                   this one clause only.
   |
   +-- vocabulary.sentences()  ------> UNCHANGED. Still produces one clause
   |                                   per `.;!?\n`-delimited piece.
   |
   +-- for each description clause:
         COMPONENT 1 (span split)  --> vocabulary.sentences()'s clause is
         |                             divided further into spans.
         COMPONENT 2 (reattachment) -> a span opening with a relative
         |                             pronoun/bare participle also credits
         |                             the nearest preceding technology token.
         COMPONENT 3 (negation)  ----> unioned into each span's own
         |                             is_non_doing() check, never replacing it.
         v
   TokenEvidence.employment / .mentions   UNCHANGED SHAPE. More entries can
   |                                      land in either list (one per span
   |                                      instead of one per clause); the
   |                                      lists themselves and the dedup rule
   |                                      ("one entry per role per token per
   |                                      bucket") are untouched.
   v
TokenEvidence.tier                    UNCHANGED. Still "employment beats
   |                                  mentions beats declared_only", with
   |                                  one addition: §3.3's precedence rule
   |                                  governs how a single ORIGINAL clause's
   |                                  own internal spans combine before they
   |                                  ever reach this property — the property
   |                                  itself does not change.
   v
employment_mentioning() / merge_years() / judge_experience() / judge_token_requirement()
                                       UNCHANGED. They read `.employment` and
                                       `.mentions` exactly as today; they
                                       have no idea spans exist.
```

Component 5 (magnitude-bearing negation, §2.5) is **not** in this diagram: it
touches `Evidence`/`TokenEvidence`'s shape and `rules._judge_technology_duration`,
a different pipeline stage than the four above, and is explicitly deferred
(§9).

## 2. Component definitions

### 2.1 Component 1 — scope-reset span splitting

**Responsibility.** Given one clause (as `vocabulary.sentences()` already
produces it), divide it into an ordered list of **spans**, each of which
`is_non_doing()` is evaluated on independently, so that a marker's effect
stops at the nearest point that plausibly ends its own claim.

**Interface** (new module, `app/services/matching/span_split.py` — see §8;
nothing here is written to the file yet):

```python
SCOPE_RESET_CONNECTORS: frozenset[str] = frozenset({
    "but", "while", "though", "although", "before", "after",
    "instead", "and then", "and ultimately", "in favor of",
})

def find_marker_span(text: str) -> tuple[int, int] | None:
    """Start/end of the first NON_DOING_MARKERS match in text, reusing
    vocabulary.py's own private helpers (_SEPARATOR_FOLD, _MARKER_BEARING,
    _SINGLE_MARKERS, _MULTIWORD_MARKERS) so this can never disagree with
    is_non_doing() about what counts as a marker. Returns None if none."""

def find_next_scope_reset(text: str, after: int) -> int | None:
    """Earliest index at or after `after` that is either (a) the character
    right after a comma, or (b) the start of a SCOPE_RESET_CONNECTORS match.
    Returns None if neither occurs before the end of `text`."""

def split_into_spans(clause: str) -> list[str]:
    """clause, divided at the first scope-reset point after each marker it
    contains, applied repeatedly to the remainder. A clause with no marker,
    or a marker with no following scope-reset point, returns [clause]
    unchanged -- the master invariant, mechanically."""
```

**Exact algorithm.** `remaining = clause`; loop: find the first marker in
`remaining` via `find_marker_span`; if none, append `remaining` as the final
span and stop; else find `find_next_scope_reset(remaining, after=marker_end)`;
if none, append `remaining` as the final span and stop; else append
`remaining[:split_point]` as a span, set `remaining = remaining[split_point:]`,
and repeat.

**Why marker-relative, not "every comma."** A rule that split at every comma
regardless of a marker's presence is not safe — see §3.1's Redis regression
check. Only scanning **after a marker's own position** guarantees the
algorithm never proposes a split for a clause it has no reason to touch,
which is what makes the master invariant hold for this component.

**Verified against `non_doing_v1`.** Closes, by itself, with no other
component involved: `pl-001, pl-002, pl-003, pl-004, pl-006, pl-009, pl-028`
(7 pipeline cases — note `pl-009` moves here from component 2's list in the
architecture analysis: `in favor of` sits between the marker and the comma,
so the split lands before `Airflow` without needing any reattachment) and
`sd-024, sd-029, sd-030, sd-069, sd-070, sd-088, sd-089, sd-090, sd-091,
sd-092, sd-093` (11 sentence cases — `sd-024` is a bonus: it was authored as
a `vocabulary` defect and is also closed here, since "sprint planning
sessions" and "building the checkout service in Node.js" fall on opposite
sides of `while`). Run: `sim2.py`'s `run_pipeline_cases()` and the standalone
sentence-case block, both included in this design's scratch artifacts.

### 2.2 Component 2 — nearest-technology-token reattachment

**Responsibility.** A span produced by component 1 that opens with a
relative pronoun or a bare present participle has no subject of its own — it
is a comment on a noun phrase from an *earlier* span, not an independent
claim. Attach its `is_non_doing()` verdict to that noun phrase's technology
token as well as to whatever `phrase_variants()` finds literally inside the
span itself (usually nothing, for a relative clause — pronouns are not
technology tokens).

**Interface:**

```python
REL_OPENER = re.compile(r"^\s*,?\s*(which|that|who)\b", re.I)
PARTICIPLE_OPENER = re.compile(r"^\s*,?\s*now\s+\w+ing\b", re.I)

def reattach(spans: list[str], real_technologies: frozenset[str]) -> list[SpanResult]:
    """For each span, in order: locate every real_technologies token
    literally present, by CHARACTER POSITION (not set membership -- see the
    bug this caught in the simulation, below). If the span opens with
    REL_OPENER or PARTICIPLE_OPENER and at least one technology token has
    been seen in an EARLIER span of this same clause, add the token whose
    LAST occurrence has the highest character position (i.e. textually
    nearest, "late closure") to this span's token set."""
```

```python
@dataclass(frozen=True)
class SpanResult:
    text: str
    shows: bool                 # this span's own demonstrates() verdict, §2.3-unioned
    literal_tokens: frozenset[str]
    reattached_tokens: frozenset[str]   # empty unless this span is an opener
```

**The bug this design caught, stated so it does not get reintroduced.** A
first implementation attempt tracked "tokens seen so far" as an unordered
`set` and took an arbitrary element as "nearest." That passed nothing: with
two technologies in the same preceding span (`pl-005`: "GraphQL layer on top
of the existing gRPC services"), an unordered set can hand back either one,
non-deterministically across Python process runs (string-hash salting). The
fix is to track literal technology occurrences as `(token, end_position)`
pairs and take `max(..., key=lambda pair: pair[1])`. **Any implementation of
this component must be position-based; a test asserting this against a
two-technology preceding span is required (§6).**

**Verified against `non_doing_v1`.** Closes `pl-005, pl-011, pl-019` (3
pipeline cases) that component 1 alone does not reach, using the corrected,
position-based reattachment. `pl-019` closes without ever resolving "which
noun does 'familiar' modify" — the main span still (incorrectly, on its own)
demotes Kubernetes, but the reattached relative clause independently supplies
a clean `employment` entry for the same role, and `TokenEvidence.tier`'s
existing employment-beats-mentions precedence (unchanged) resolves it.

**Ambiguity fallback.** If no technology token has been seen in any earlier
span of the clause, reattachment does nothing — no token is guessed. If
several technologies were seen, "nearest" (last by character position) is a
deterministic, if imperfect, choice; it is the standard "late closure"
default for relative-clause attachment and is the only tie-break this design
proposes. It is not claimed to be always correct — see §5.

### 2.3 Component 3 — explicit negation patterns

**Responsibility.** Recognize a plain-English denial of use that names no
word from `NON_DOING_MARKERS` at all, without adding negator particles to
that set (which would be unsafe — see the analysis doc §B.1 and the control
check below).

**Interface** (new module, alongside `span_split.py`):

```python
EXPLICIT_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(never|not)\s+(used|worked(\s+with)?|wrote|touched|tried|shipped|deployed)\b", re.I),
    re.compile(r"\b(no|zero|none)\b[\w\s-]{0,25}?\b(experience|exposure|hours)\b", re.I),
    re.compile(r"\b(does|did)\s+not\s+have\b[\w\s-]{0,25}?\bexperience\b", re.I),
    re.compile(r"\bnothing\b[\w\s-]{0,20}?\bdone\b", re.I),
    re.compile(r"\bno\b[\w\s-]{0,20}?\b(has|have)\b[\w\s-]{0,10}?\b(shipped|started|happened)\b", re.I),
    re.compile(r"\bunable\s+to\b[\w\s-]{0,25}?\b(try|start|use|work)\b", re.I),
)

def negation_fires(span: str) -> bool:
    return any(pattern.search(span) for pattern in EXPLICIT_NEGATION_PATTERNS)
```

**How it composes with `demonstrates()` — a new function, not a modification:**

```python
# app/services/matching/evidence.py -- ADDITION, demonstrates() itself untouched
def demonstrates_v5(text: str | None, sentence: str) -> bool:
    return demonstrates(text, sentence) and not negation_fires(sentence)
```

**Verified against `non_doing_v1`.** Fires on all 10 `explicit_non_use`
sentence cases (`sd-056`-`sd-065`) plus `pl-029`'s second clause ("no Rust
code has shipped yet"). Verified **not** to fire on six adversarial controls
constructed specifically to probe the "bare negator is unsafe" risk named in
the architecture analysis: *"This is not a hobby project, it has been in
production for six years,"* *"Kubernetes in production, not just in a
course,"* *"Kept the service running with no downtime for three years,"*
*"Worked with no direct supervision on the Kafka migration for six years,"*
*"This was not a small project; it shipped to every region,"* *"The team
never stopped shipping new Django features for a decade."* None of the six
patterns match any of these. This is a spot-check, not a proof of
completeness — see §6 for the test suite this needs before shipping.

### 2.4 Component 4 — title-aware indexing

**Responsibility.** `Experience.job_title` is indexed as clause zero today,
through the identical `demonstrates()` path as prose. Every one of the 10
`job_title` benchmark cases expects `False` (not non-doing) — confirmed by
re-querying the dataset for this document; there is no case in `non_doing_v1`
where a bare title should read as non-doing via an ordinary marker. A title
is a noun phrase, not a hedged sentence: "Retired Principal Engineer" does
not have the grammar to mean "this was merely evaluated," the way "Retired
the team's homegrown scheduler" does.

**Interface:**

```python
# app/services/matching/evidence.py -- ADDITION
def demonstrates_title(title: str | None) -> bool:
    """Whether a job title should be read as affirming the technologies it
    names. Ignores NON_DOING_MARKERS entirely -- a title's grammar does not
    carry a marker's caveat meaning -- and defers only to component 3's
    negation patterns, which CAN still fire on a title (a hypothetical
    "Kubernetes Administrator (Never Certified)" should still be weakened)."""
    if not title:
        return True
    return not negation_fires(title)
```

**Design correction from the architecture analysis.** That document proposed
an additional carve-out — a personal-status title prefix pattern
(`^(Former|Retired|Ex-)\b`) that would cause a title to be treated with
*extra* suspicion. Simulating `pl-026` (*"Retired Principal Engineer - 15
Years in Distributed Java Systems"*) against that phrasing showed it produces
the wrong answer: the prefix pattern would match, and the analysis doc's own
wording ("demoted unless... or the title matches...") reads as making that
case *more* likely to be demoted, when the correct answer is the opposite —
this title's own words state 15 years of real work, and "Retired" describes
employment status, not technology history. Dropping the carve-out and simply
ignoring `NON_DOING_MARKERS` for all titles is both simpler and correct
against every case, including `pl-026`. This document supersedes that part
of the architecture analysis.

**Verified against `non_doing_v1` — with a scope caveat.** `pl-023` and
`pl-026` (the two *pipeline* title cases, where a real `Experience.job_title`
is indexed) are closed: `demonstrates_title` returns `True` for both, since
neither title matches any of the six negation patterns. **The 10
sentence-level `job_title` cases (`sd-078`-`sd-087`), and `sd-082`
(categorized `clause_context` rather than `job_title`, but the same
title-shaped string — `"Kubernetes Migration - Planning & Delivery Lead"`),
are not closed by this component, and cannot be by any design that leaves
`is_non_doing()` untouched** — `evaluate_sentence` in the `non_doing_v1`
adapter calls `is_non_doing(case.text)` directly, on the bare title string,
which is exactly the function this design (and the brief) says must not
change. Confirmed by simulation: `split_into_spans` does not touch
`sd-082`'s text at all (no comma, no connector — the hyphen in "Migration -
Planning" is not a comma), so it reads exactly as V4 reads it today. This is
a genuine scope boundary, not an oversight: those 11 cases document what
`is_non_doing()` itself does with title-shaped text, which is correctly
unaffected by a fix that lives one layer up, in how `profile_index.py` calls
it. See §6 for the benchmark change this implies.

### 2.5 (Deferred) Component 5 — magnitude-bearing negation

Named because the brief asks for "magnitude-bearing negation" to be defined
explicitly, and because leaving it undefined would make the design read as
though it were forgotten rather than scoped out on purpose.

**Responsibility, if built.** `pl-014, pl-030, pl-036` show a `mentioned`-tier
clause whose own text states an explicit, small-or-zero duration ("no
production work with it," "three weeks... not continued," "after two
weeks"). A person reads this as `MISSING`; `rules._judge_technology_duration`
can only produce `UNCERTAIN`, because `TokenEvidence` has no field for a
duration read out of prose.

**Interface, if built (not proposed for this milestone — see §9):**

```python
@dataclass(frozen=True)
class Evidence:
    ...
    explicitly_insufficient: bool = False   # new, optional field, default
                                             # preserves every existing call site

INSUFFICIENT_MENTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bno\s+production\s+work\b", re.I),
    re.compile(r"\b(was\s+)?not\s+continued\b", re.I),
    re.compile(r"\bfor\s+(a|one|two|three)\s+(day|days|week|weeks)\b", re.I),
    ...
)
```

`rules._judge_technology_duration` would need one new branch, before its
final `UNCERTAIN` return, checking `found.mentions` for
`explicitly_insufficient` and returning `MISSING` if set. That is a change to
`rules.py`'s actual logic (not just an addition), which is exactly why this
is named as its own component with its own gate rather than folded into
components 1-4: it is smaller (3 cases), touches a different file, and has
its own open design question (a magnitude *value*, or just a boolean
"insufficient" flag? this design does not resolve that — see §9).

## 3. Data structures and precedence rules

### 3.1 `TokenEvidence`'s shape does not change

`declarations` / `employment` / `mentions` remain three lists of `Evidence`;
`tier` remains the same four-way property, in the same order
(`declared_corroborated > employment_described > declared_only > mentioned`).
What changes is only how many `Evidence` items land in `employment` vs.
`mentions` per role — one per **span** now, instead of one per **clause** —
governed by §3.3 below for spans of the same original clause.

### 3.2 Determinism

`split_into_spans` and `reattach` are pure functions of their string input:
no randomness, no dict/set iteration exposed to output ordering (the
position-based fix in §2.2 exists specifically to remove the one place a
`set` had leaked into the design). Evidence lists still append in
clause-processing order, so `evaluation.py`'s existing reproducibility
property (`tests/test_benchmark.py`'s `test_two_separate_runs_produce_identical_observations`-equivalent
for this domain) is unaffected structurally.

### 3.3 New precedence rule: same-clause span conflicts

**The regression this rule exists to prevent, reproduced:**
*"Evaluated Kafka, before abandoning Kafka entirely for cost reasons."*
`evaluated` is a marker; the naive component-1 split (comma, since no
connector precedes it faster) produces `"Evaluated Kafka,"` (marker, `kafka`
present, correctly non-doing) and `" before abandoning Kafka entirely for
cost reasons."` (`abandoning` — the **gerund** — is not in
`NON_DOING_MARKERS`, only `abandoned` is; this span has no marker at all).
Kafka is a *literal, independent* mention in both spans. Naively unioning
with "employment beats mentions" — which is exactly right for evidence from
two different clauses or roles — flips this to `employment`, a false
`MATCHED` that **V4 today does not produce** (the whole, unsplit clause
contains `evaluated`, so V4 reads it correctly, if only because it never
looked closely enough to get it wrong). Confirmed empirically with `sim2.py`
before writing this rule down, not asserted from principle alone.

**The rule.** Track each span's contribution with two kinds of provenance:
*literal* (the token was found by `phrase_variants()` inside that span's own
text) and *reattached* (the token was added by component 2's rule). Within
the spans produced from **one original clause**:

1. A **reattached** token contribution may promote a token from `mentions`
   to `employment`, same as today's cross-clause rule — this is what makes
   `pl-005`, `pl-011`, `pl-019`, and `pl-032` (incidentally — see §4) work.
2. A **literal** token contribution from a *later* span may **not** override
   a `mentions` verdict the *same token* already received from an *earlier*
   span of the *same original clause*. Both entries are still recorded (nothing
   is deleted — a reader can still see both spans quoted), but the combination
   step treats the pair as `mentions` for tier purposes.
3. This rule applies **only within one original clause's own spans.** Across
   different clauses (period/semicolon-separated) or different roles, today's
   existing rule — any employment entry anywhere wins — is unchanged, because
   that is genuine, independently-written corroboration, not an artifact of
   where this design chose to cut a sentence.

This rule does not close any additional benchmark case (none of the 137
exercise the pattern it protects against — it was found by construction, not
by the existing dataset) and does not need to: it exists to make sure
components 1-2 introduce **zero** new false-`MATCHED` cases relative to V4,
which is the one property this design cannot compromise on. §6 requires a
new regression case for exactly this shape before implementation.

## 4. Explicit behavior for every required construct

| construct | rule | closes | notes |
|---|---|---|---|
| `but` | scope-reset connector (component 1) | `pl-002` | no comma required |
| `while` | scope-reset connector (component 1) | `pl-001, pl-004, sd-030, pl-028` | closes with or without a preceding comma |
| `though` / `although` | scope-reset connector (component 1) | none in `non_doing_v1` currently | included for symmetry with `while`; untested by this dataset — flagged in §6 |
| comma-separated independent clauses | component 1, marker-relative only (§2.1) | `pl-003, pl-006, pl-009, sd-024, sd-029, sd-069, sd-070, sd-088, sd-089` | never triggers without a preceding marker — see §3.1's Redis check |
| `and` | **never** a scope-reset point | — | explicitly excluded; see the "Redis and Memcached... evaluated and rejected" case in the architecture analysis §A.5 |
| `or` | **never** a scope-reset point | — | same reasoning as `and` |
| trailing relative clauses (`which`/`that`/`who`) | component 2 reattachment | `pl-005, pl-011, pl-019` | position-based nearest-token rule; see §2.2's known limit in §5 |
| explicit `not` | `EXPLICIT_NEGATION_PATTERNS` P1/P3 (component 3) | `sd-057, sd-059, sd-060` (via "not worked"/"not have"/"never wrote") | never a bare-word addition to `NON_DOING_MARKERS` |
| `never` | P1 | `sd-056, sd-060` | |
| `no` | P2/P4/P5 | `sd-058, sd-061, sd-062, sd-064, pl-029` | context-windowed, not a bare word |
| magnitude-bearing negation | **deferred, component 5** (§2.5) | `pl-014, pl-030, pl-036` — not closed by this milestone | requires an `Evidence` field and a `rules.py` branch, out of scope here |
| `replacing A with B` | **not specifically addressed** | `pl-032` closes *incidentally* via component 2's participle-reattachment (its trailing "now streaming every order in real time" clause), **not** via any directional understanding of "replacing" | confirmed by removing that trailing clause in simulation: without it, the same sentence still fails. Do not report this as "fixed" in a changelog — it is wording-dependent, not principled. |
| multiple technologies, one sentence | components 1+2 together | `pl-001` through `pl-006` except as noted; the general case is §A.1/§C.1 of the architecture analysis | |
| job titles | component 4 | `pl-023, pl-026` (pipeline); **not** `sd-078`-`sd-087` or `sd-082` (sentence-level — see §2.4's scope caveat) | |

## 5. Unsupported and ambiguous constructions

Governed entirely by §0's master invariant, with three named specific
fallbacks:

- **No marker in a clause at all** → component 1 returns `[clause]`
  unchanged. Identical to V4.
- **A marker with no scope-reset point after it anywhere in the remaining
  text** → component 1 returns the remainder as one final span (still
  contains the marker). Identical to V4 for that portion.
- **Component 2 finds an opener (relative pronoun/participle) but no
  technology token has appeared earlier in the clause** → no reattachment
  happens; the span is graded purely on its own literal tokens (usually
  none). Nothing is guessed.
- **Component 2 finds two or more candidate technologies in the preceding
  span** → the textually nearest (highest end position) is chosen — a named,
  deliberate, imperfect default (§2.2), not silently arbitrary. The
  architecture analysis's own counter-example (*"Uses Kubernetes for
  everything, which was piloted last quarter and never fully adopted"* — an
  affirmative-sounding main clause with the trailing relative clause as the
  actual caveat) is **not** solved by this design and is not claimed to be;
  it is named there and here as an accepted residual for a future
  `non_doing_v2` case, not silently mishandled.
- **A clause this design's connector list does not anticipate** (a
  subordinator not in `SCOPE_RESET_CONNECTORS`) → falls through to "no
  scope-reset point found," i.e. V4's behavior. This is why `though`/
  `although` are included even though nothing in the *current* dataset
  requires them: they cost nothing when absent (never trigger without a
  preceding marker) and are cheap insurance against the very next resume
  that writes "Evaluated Foo, though it was never adopted."

## 6. New tests required before implementation

All of these are new; none exist today. Ordered by what they would have
caught, most valuable first.

1. **`non_doing_v2` dataset addition**: the same-clause-repeated-token
   regression from §3.3, verbatim or close to it (*"Evaluated Kafka, before
   abandoning Kafka entirely for cost reasons."* → `kafka` expected
   `mentions`, not `employment`). This is the single most important new test
   this design implies, because it is the one case where the naive
   implementation and the correct one disagree and nothing in the current
   137 cases would catch the naive version shipping.
2. **Unit tests for `find_marker_span`** proving it agrees with
   `is_non_doing()` on every existing `test_matching_vocabulary.py` case
   (separator-fold variants, `MARKER_BEARING_NAMES` neutralization,
   hyphenated-compound handling) — the risk named in §2.1 that a
   re-implemented position-finder could silently diverge from the
   boolean-only function it must never contradict.
3. **Unit tests for `split_into_spans`**, one per row of §4's table that
   involves component 1, plus the Redis-hypothetical safety case
   (*"Redis, evaluated for six months, was never adopted."* → `redis`
   expected `mentions`) as a named, permanent regression test — it is what
   proves the marker-relative design decision (vs. unconditional comma
   splitting) is load-bearing, not cosmetic.
4. **Unit tests for `reattach`** with the position-based nearest-token logic,
   including a two-technology preceding span (`pl-005`'s shape) run multiple
   times or with `PYTHONHASHSEED` varied, to catch a regression to the
   unordered-set version this design's own first draft produced.
5. **Unit tests for `negation_fires`**: all 11 positive texts in §2.3, all 6
   adversarial controls in §2.3, plus new adversarial controls for each
   pattern individually (a P2 near-miss like *"experience with no downtime"*
   reordered, a P5 near-miss like *"no reason it hasn't shipped"*) — the
   patterns in this document were checked against six controls; a real
   implementation needs enough controls to make a reviewer confident the
   windowed regexes do not fire on ordinary affirmative prose at large,
   which six examples cannot fully establish.
6. **Unit tests for `demonstrates_title`**: all 10 `job_title` sentence
   texts, run through the NEW function (not `is_non_doing`), confirming all
   10 return `True`; plus a constructed title containing a negation pattern
   (*"Kubernetes Administrator (Never Certified)"*) confirming it returns
   `False`.
7. **`non_doing_v1` → `non_doing_v2` retagging, not new cases**: `sd-078`
   through `sd-087`, plus `sd-082` (title-shaped text under a different
   category tag), should be given a note (or a linked pipeline case, per
   the pattern `pl-023`/`pl-026` already establish) clarifying they test
   `is_non_doing()`'s behavior on title-shaped text specifically, and are
   expected to remain unchanged by this design — otherwise a future reader
   of the benchmark scorecard will see "0/10 job_title sentence cases fixed"
   next to "component 4: closes job titles" and reasonably conclude the
   component failed, when it did exactly what it was designed to do at the
   layer it was designed to touch.
8. **Full-suite reproducibility test**, the same shape as
   `test_two_separate_runs_produce_identical_observations` in the existing
   matching benchmark: run the whole `non_doing_v1` (`v2`) benchmark twice
   after implementation and assert byte-identical observations — proving
   §3.2's determinism claim in the same way the codebase already proves it
   for the engine as a whole.
9. **A `profile_index.py` integration test** (not a `vocabulary.py`/`evidence.py`
   unit test) building a real `ProfileVersion`/`Experience` and asserting the
   full `_index_employment` → `TokenEvidence.tier` path for at least one case
   per component, so the wiring itself — not just the pure functions — is
   covered.

## 7. Invariants that must remain unchanged

- **`resolve_technology_subject()`**: no test in `non_doing_v1` demonstrates
  a defect (0/38 mismatches, reconfirmed for this document); it is not
  touched, and this design contains no proposal that would touch it.
- **`NON_DOING_MARKERS`, `MARKER_BEARING_NAMES`, `is_non_doing()`,
  `demonstrates()`**: zero diff. Every new behavior is a new function
  (`demonstrates_v5`, `demonstrates_title`, `negation_fires`,
  `split_into_spans`, `reattach`) or a new module. `is_non_doing` and
  `demonstrates` keep their exact current signatures and bodies; existing
  callers (if any exist outside `profile_index.py` — none do today besides
  the one call site this design changes) are unaffected.
- **`ALGORITHM_VERSION`**: unchanged (`matching_v4`) until this is actually
  built and measured, per the architecture analysis's own closing line.
- **Existing `matching_v2`/`matching_real_v1` benchmark expectations**:
  untouched. Nothing here changes subject resolution, tiering rules for
  requirements this design does not touch, certification, education, or
  location logic at all.
- **Reproducibility**: preserved structurally (§3.2); to be re-proven by
  test #8 above, not assumed.
- **Deterministic ordering**: `Evidence` lists still append in the order
  clauses (now spans) are processed; nothing introduces a `set`, a `dict`
  iterated for output, or any hash-seed-dependent behavior into anything
  that reaches a stored result. (The one place a `set` nearly leaked into
  output — component 2's first draft — was caught in simulation and is
  fixed in the interface in §2.2, not left as a warning for the implementer
  to discover.)
- **Evidence provenance**: every `Evidence` object still carries its
  `source_type`, `source_id` and `quote`, unchanged. A span's `quote` is the
  span's own text (a substring of the original clause), which is *more*
  precise than today's whole-clause quote, not less — a reviewer reading a
  V5 result would see "Deprecated the legacy PHP monolith" quoted against
  `php` and "while building its replacement in Go, which now serves all
  production traffic" quoted against `go`, instead of today's single
  quote covering both.

## 8. File-by-file implementation plan

Nothing below is created or edited by this document. This is the plan for
whoever picks up the GO decision in §10.

| file | change |
|---|---|
| `app/services/matching/span_split.py` | **new file.** `SCOPE_RESET_CONNECTORS`, `find_marker_span`, `find_next_scope_reset`, `split_into_spans`, `reattach`, `SpanResult`. Imports `vocabulary._SEPARATOR_FOLD`, `_MARKER_BEARING`, `_SINGLE_MARKERS`, `_MULTIWORD_MARKERS` read-only. |
| `app/services/matching/negation.py` | **new file.** `EXPLICIT_NEGATION_PATTERNS`, `negation_fires`. No dependency on `span_split.py` or `vocabulary.py`. |
| `app/services/matching/vocabulary.py` | **no change.** |
| `app/services/matching/evidence.py` | **addition only.** `demonstrates_v5(text, sentence)` and `demonstrates_title(title)`, both new functions calling `demonstrates` (unchanged) and `negation.negation_fires`. `demonstrates` itself: zero diff. |
| `app/services/matching/profile_index.py` | **the one real integration change.** `_index_employment`'s per-clause loop (currently: one `demonstrates()` call per clause, one `phrase_variants()` call per clause) is replaced for description clauses by: `split_into_spans` → `reattach` → per-`SpanResult`, `demonstrates_v5` unioned in per §2.3 → §3.3's same-clause precedence when combining spans of one original clause before they are appended to `.employment`/`.mentions`. The title clause (index 0) uses `demonstrates_title` instead of `demonstrates`/`demonstrates_v5`. `_index_projects`: **no change** — project evidence is already filed as `mentions` unconditionally, so nothing here is reachable by any of these components. |
| `app/services/matching/rules.py` | **no change** for components 1-4. Component 5 (deferred) would add one branch to `_judge_technology_duration`; not part of this plan. |
| `app/models/match.py`, migrations | **no change.** No new column, no new table — `Evidence`/`TokenEvidence` stay Python dataclasses, not persisted rows, per the existing architecture. |
| `services/api/tests/test_span_split.py` | **new file.** §6 items 2-4. |
| `services/api/tests/test_negation.py` | **new file.** §6 item 5. |
| `services/api/tests/test_demonstrates_title.py` | **new file.** §6 item 6. |
| `services/api/tests/test_profile_index_v5_integration.py` | **new file.** §6 item 9. |
| `evaluation/datasets/non_doing_v2/` | **new dataset directory**, copied from `non_doing_v1` plus: the §3.3 regression case, the §6 item 7 retagging/pipeline-linking of `sd-078`-`sd-087`, and any `though`/`although` case added per §4's row for those connectors. Per the existing benchmark convention (`evaluation/README.md`), a changed expectation always bumps the directory; nothing in `non_doing_v1` is edited in place. |
| `evaluation/nondoing/adapter.py` | **updated to call the new pipeline** once it exists, so `non_doing_v2` measures the real `_index_employment`, not the `sim2.py` prototype. |
| `docs/matching.md` | **updated** once shipped, the same way `matching_v1 → v2` is documented there today, with `ALGORITHM_VERSION` bumped to `matching_v5` only at that point. |

## 9. What this design deliberately does not schedule

- **Component 5** (§2.5): real, 3 cases, smaller than components 1-4
  combined, touches `rules.py`'s actual duration logic (not just addition),
  and has an open design question (boolean flag vs. magnitude value) this
  document does not resolve. Ship components 1-4, measure, then scope this
  separately — matching the architecture analysis's own recommendation.
- **`sd-031`'s zero-delimiter, no-rescue shape** and **`pl-032`'s general
  "replacing" directional ambiguity** (as opposed to the one wording it
  happens to pass on today): both confirmed, by this document's own
  simulation, to require either real dependency information or a dedicated
  vocabulary-sense rule neither proposed nor validated here. Named residuals,
  not silently dropped.
- **`though`/`although`**: included in `SCOPE_RESET_CONNECTORS` for symmetry
  and low cost, but untested by any current case — flagged in §4 and §6,
  not claimed as validated.

## 10. GO / NO-GO

**GO**, scoped to components 1-4 exactly as specified in §2.1-§2.4, gated on
the new tests in §6 (especially items 1-4, which is where the one identified
regression risk and the one identified implementation bug live) — **not**
components 1-5, and **not** without §3.3's precedence rule.

Evidence for GO:

- Every one of the 4 components was validated by running actual code (a
  read-only simulation reusing the unmodified `is_non_doing` and
  `phrase_variants`) against the real `non_doing_v1` cases, not by
  re-reasoning from the architecture analysis's prose. The simulation is
  reproducible: `sim2.py`, `run_pipeline_cases()`.
- Combined, components 1-4 close 21 of the 23 confirmed `attribution`/
  `clause_context` cases — all 6 `attribution` cases
  (`pl-001,002,003,004,005,006`) and 15 of 17 `clause_context` cases
  (`pl-009,011,019,023,028` and `sd-029,030,069,070,088,089,090,091,092,093`;
  `sd-031` and `sd-082` are the two confirmed, named residuals — §2.1/§2.4) —
  plus all 10 `explicit_non_use` cases and `pl-029`'s false-`MATCHED`
  cascade. That is 32 of `non_doing_v1`'s 65 predicted-and-confirmed defects,
  using only regexes, small closed vocabularies, and one dataclass-shaped
  precedence rule. `sd-024` (tagged `vocabulary`) closes too, as a
  documented bonus not counted in the 32 since it was not the case it was
  authored to test. Zero ML, zero embeddings, zero external NLP
  dependencies, zero change to `NON_DOING_MARKERS`, `is_non_doing()`,
  `demonstrates()`, or `resolve_technology_subject()`.
- The design process itself surfaced and fixed two things a less careful
  pass would have shipped wrong: an unordered-set bug in component 2 (§2.2)
  and a same-clause false-`MATCHED` regression in the naive form of
  component 1 (§3.3) — both caught by simulating against real text before
  writing any interface down, and both have a named test in §6 gating
  re-introduction.
- The one thing initially claimed as "fixed" that is not (`pl-032`,
  generally) was caught by the same process and downgraded to "incidentally
  passes on this exact wording" rather than reported as closed — the
  intellectual honesty the brief's "prefer UNCERTAIN over inventing
  evidence" asks for, applied to the design process and not only the runtime
  behavior.

Conditions attached to the GO, all of which gate the *implementation* PR, not
this document:

1. Do not implement component 5 in the same change — ship and measure
   components 1-4 first (§9).
2. Do not report `pl-032` or `sd-078`-`sd-087` as "fixed" in whatever
   communicates this change (a changelog, a PR description, `docs/matching.md`) —
   report exactly what §4 and §6 item 7 say.
3. The §3.3 regression case and the §2.1/§2.2 unit tests (§6 items 1-4) must
   exist and pass before `profile_index.py` is touched, not after — they are
   what makes the difference between this design and the naive version of
   it that this same process disproved.
4. Bump `ALGORITHM_VERSION` to `matching_v5` and `non_doing_v1` to
   `non_doing_v2` together, at ship time, per §8's last two rows — never
   separately, and never before.
