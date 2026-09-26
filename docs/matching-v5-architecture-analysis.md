# Matching V5 architecture analysis

Analysis only. Nothing in `app/services/matching/` changed to produce this
document: `NON_DOING_MARKERS`, `is_non_doing()`, `demonstrates()` and
`ALGORITHM_VERSION` (`matching_v4`) are exactly as they were. No LLM,
embedding or similarity model is proposed anywhere below — every mechanism
discussed is a regex, a small closed vocabulary, or a bookkeeping change to a
dataclass. Every case ID below is drawn from `non_doing_v1`
(`services/api/evaluation/datasets/non_doing_v1/cases.json`, 137 cases) and
every claim about "current behaviour" was checked by running the existing,
unmodified code — see the command at the end of each subsection.

The brief: analyze three dimensions independently, and for each problem found,
give the exact current behavior, the case IDs that demonstrate it, the first
point where semantics become incorrect, and whether the defect is in the
**representation** (the data shape carrying evidence through the pipeline) or
the **vocabulary** (a curated word/phrase list whose membership is wrong for
some sentence). End with the smallest deterministic shape that could hold the
observed cases — not built, not scheduled, a design artifact for whoever
scopes Matching V5.

---

## A. Clause/span granularity

### A.0 Current behavior, exactly

`vocabulary.sentences()` splits an `Experience.description` on
`_SENTENCE = re.compile(r"[.;!?\n]+")` (`vocabulary.py:221`). Each resulting
piece — plus the job title as its own zero-th piece — is a **clause** in this
codebase's terminology. `profile_index.ProfileIndex._index_employment()`
(`profile_index.py:159`) then, per clause:

```python
shows = demonstrates(experience.description, clause)          # profile_index.py:175
...
for token in phrase_variants(clause):                          # profile_index.py:185
    for bucket in (self._bucket(token), self._action_bucket(token)):
        target = bucket.employment if shows else bucket.mentions
        ...
```

`demonstrates(text, sentence)` (`evidence.py:266`) ignores its first
parameter entirely and returns `not is_non_doing(sentence)`. So exactly one
boolean, `shows`, is computed **per clause**, and `phrase_variants(clause)`
— which returns every 1/2/3-gram technology token the whole clause contains —
is then walked in a loop that assigns that *same* boolean to *every* token
found. This loop is the entire mechanism. There is no per-token branch inside
it, and there is no second look at which word in the clause a marker
grammatically modifies.

Reproduce: `python -m evaluation.nondoing.runner --failures`, section
"TECHNOLOGY-ATTRIBUTION FAILURES".

### A.1 Problem: one boolean per clause, N technologies per clause

**Exact current behavior.** When a clause names two technologies and a
`NON_DOING_MARKERS` word anywhere in the clause fires, both technologies are
filed identically — the one the marker actually describes and the one it
does not.

**Case IDs.** `pl-001` through `pl-006` — the dedicated
`technology_attribution` category's 6 confirmed failures out of 8 members
(the other 2, `pl-007` and `pl-034`, are controls — see A.3). Representative:
`pl-001`'s description, *"Deprecated the legacy PHP monolith while building
its replacement in Go, which now serves all production traffic."* —
`deprecated` fires once for the whole clause; `php` and `go` both land in
`mentions`. Expected: `go` → `employment_described`
(`attributed_employment=true`); actual: `go` → `mentioned`
(`attributed_employment=false`). All 6 cases propagate to a flipped final
verdict (`matched` expected → `uncertain` actual) — see §C.2 for the full
downstream trace. (`pl-032`, discussed in §C.1, shares this broadcast
mechanism but is a different, single-technology shape and is tagged
`vocabulary` rather than `attribution` — it is not counted here.)

**First point of incorrect semantics.** `profile_index.py:185`, the
`for token in phrase_variants(clause)` loop — specifically, the moment a
single `shows` value computed at `profile_index.py:175` is broadcast to a
`set` of tokens rather than to the one token (or none) it was actually about.
Nothing before this point is wrong: `is_non_doing("Deprecated the legacy PHP
monolith while building its replacement in Go, which now serves all
production traffic.")` returning `True` is a *defensible* reading of the
clause as a whole (it does contain a caveat). The defect is not in the
boolean; it is in reusing one clause-wide boolean as if it applied uniformly
to every noun phrase inside the clause.

**Representation defect, not vocabulary.** No amount of editing
`NON_DOING_MARKERS` changes this — `deprecated` is a correct member of that
list, and firing on this sentence is correct. The clause, as a unit, is too
coarse to hold two independent verdicts. This is purely a *shape* problem.

### A.2 Problem: attribution is clause-scoped, not sentence- or proximity-scoped

**Exact current behavior.** `pl-034`'s description — *"Abandoned a plan to
rewrite the notifications service in Elixir; the existing Rails service the
team has run for five years continued handling all notifications."* — is
structurally the same two-technologies-one-idea shape as `pl-001`–`pl-006`.
It was authored predicting the same defect (`defect_class: attribution`) and
instead passed, because the sentence happens to contain a semicolon.
`vocabulary.sentences()` splits it into two clauses before either technology
is looked up, so `elixir` and `rails` are never in the same `phrase_variants()`
call in the first place.

**Case IDs.** `pl-034` (unpredicted control; see `BASELINE_REPORT.md`, "the
one resilient attribution case") against `pl-001`–`pl-006` (failing).

**First point of incorrect semantics.** N/A for `pl-034` — nothing is wrong.
The finding is diagnostic, not a defect: it proves the failure boundary in
§A.1 is the *clause* as currently delimited (`.;!?\n`), not sentence
adjacency and not word-distance. A period, semicolon or line break between
two technologies is sufficient protection today; a comma or a subordinating
conjunction between them is not.

### A.3 What would and would not fix this with more (but still only) punctuation/conjunction splitting

This section exists because "split more finely" is the obvious first idea,
and the benchmark can say precisely how far it goes.

**Fully fixed by splitting each clause further on strong, unambiguous
scope-reset points — a comma that begins a new independent clause, or a
small closed set of contrastive/subordinating connectors (`but`, `while`,
`though`, `although`, `before`, `after`, `instead`, `in favor of`, `and
then`, `and ultimately`) — each new span getting its own `is_non_doing()`
call:**

`sd-029, sd-030, sd-069, sd-070, sd-088, sd-089, sd-090, sd-091, sd-092,
sd-093, pl-001, pl-003, pl-004, pl-006, pl-028, pl-032`. In every one of
these, the technology whose evidence should survive sits entirely on one side
of a connector or comma, and the marker sits entirely on the other. `pl-028`
additionally needs the *same* multi-entry resilience the codebase already
gives multi-clause and multi-role evidence (§A.5) — a span-level `mentions`
entry and a span-level `employment` entry for the same token, same role, are
not a conflict, because `TokenEvidence.tier` already checks `employment`
before `mentions`.

**Not fixed by any punctuation/conjunction splitting, because the technology
sits *inside* the marker-bearing span as an object, and its rescuing
information is a trailing relative clause that refers back to it
specifically:** `pl-005` (*"Prototyped a GraphQL layer on top of the existing
**gRPC** services, **which** have handled every internal call in production
since 2017."*), `pl-009` (*"Retired the team's homegrown job scheduler in
favor of **Airflow**, **which** has orchestrated every nightly pipeline..."*),
`pl-011` (*"Planning and delivering the migration of order processing to
**Kafka**, **which** now streams every order event..."*). Splitting on the
comma before "which" puts the technology on the *wrong* side (still inside
the caveat span), because the relative pronoun's antecedent is a noun phrase
that already occurred, not the clause that follows it.

**Not fixed by any span splitting at all, and with no trailing relative
clause available to rescue it either, because the marker and the technology
sit in one unbroken run with no delimiter anywhere and the marker's true
grammatical target is a *different, unrelated* noun phrase:** `sd-031`
(*"proposed"* describes "features"; "Elasticsearch-backed" is a hyphenated
compound modifier three words later in the same unbroken clause, with no
comma, conjunction, or trailing "which/that" clause anywhere in the
sentence). Recognizing this requires knowing *which noun a word modifies* —
a step no amount of splitting the string on punctuation or conjunctions can
produce, because there is no punctuation or conjunction present to split on,
and no trailing modifier for §A.4's reattachment rule to catch either.

`pl-019` (*"familiar"* describes "the onboarding docs", not "the Kubernetes
platform team", again with no delimiter between the two) looks identical to
`sd-031` on the surface, but is *not* in this tier: its sentence also ends in
a trailing relative clause — *"...which has run production workloads for
five years"* — that names no caveat and refers back to Kubernetes
specifically. §A.4's reattachment rule catches that clause independent of
whatever the main span decided, and the codebase's own existing
employment-before-mentions precedence resolves the rest. See §A.4 for why
this is a real fix for `pl-019` and not a fix for `sd-031`, which has no
trailing clause of any kind to reattach.

Three tiers, in short: **(1)** clean two-clause shapes — fixed by finer
splitting; **(2)** technology-as-object-plus-trailing-relative-clause shapes,
*including* same-clause-modifies-the-wrong-noun shapes that happen to also
end in a rescuing trailing clause (`pl-019`) — fixed by §A.4's targeted
mechanism, not by splitting harder; **(3)** zero-delimiter, no-trailing-clause
same-clause modification of an unrelated noun (`sd-031` alone) — not fixable
by any span-boundary mechanism, deterministic or not, without identifying
which word modifies which (see §A.6 for why that is out of scope here).

### A.4 The mechanism that closes tier (2) using only what tier (1) already needs

A span that opens with a relative pronoun (`which`, `that`, `who`) or a bare
present participle immediately after a comma (no subject of its own — "...,
now streaming...", "...,  which now serves...") is not a new independent
scope; it is a **modifier of the nearest preceding technology token**, not of
the whole enclosing span. Reattach its own `is_non_doing()` verdict to that
one token instead of broadcasting the *enclosing* span's verdict to it.

Checked against the benchmark, this closes `pl-005`, `pl-009`, `pl-011`
outright (the relative clause names no caveat, so the reattached token gets a
clean, span-scoped affirmative entry), and — because the existing
"employment beats mentions" precedence already exists — it *also* closes
`pl-019` without needing to solve "which noun does 'familiar' modify" at all:
Kubernetes gets a `mentions` entry from the (still-broadcast, still
imperfect) main span *and* a clean `employment` entry from the reattached
relative clause, and the tier property already prefers the latter. The
existing resilience mechanism becomes the safety net that makes the new
mechanism sufficient for a case it was not specifically designed for.

**This is not free of new risk**, and honesty about the residual matters more
here than optimism: "employment beats mentions regardless of order" is sound
in every case this benchmark contains, but order-blindness has an
identifiable counter-shape that does not currently exist in `non_doing_v1` —
an affirmative-sounding main clause followed by a trailing relative clause
that is *itself* the caveat (*"Uses Kubernetes for everything, which was
piloted last quarter and never fully adopted."*). Under §A.4 as stated, that
would resolve to `employment_described`, which is backwards. This is flagged
here as a named, scoped gap for a `non_doing_v2` case, not solved — solving
it changes "nearest-token reattachment" into "most-recent-statement-wins",
which is a different and larger design decision than this analysis is asked
to make.

### A.5 The unit this argues for

Not a fourth alternative invented for its own sake — the two mechanisms
above, taken together, are already precisely what the 23 clause-scoped
failures (§A.1's 6 + §A.3's remaining ~17, counted exactly in §C.1) need and
no more:

1. **Scope-reset connector list** (new, small, closed, reviewable exactly
   like `NON_DOING_MARKERS` is) splits each existing clause into spans.
   `is_non_doing()` runs per span, unchanged.
2. **Nearest-token reattachment** for a narrow, syntactically recognizable
   shape (comma + relative pronoun, or comma + subjectless participle) —
   its own `is_non_doing()` verdict attaches to the nearest preceding
   technology token rather than to the enclosing span's whole token set.

Explicitly rejected, and why:

- **Fixed-width token window** (e.g. ±5 words around a technology token)
  trades the current arbitrary boundary (a whole clause) for a different
  arbitrary boundary (N tokens) with no linguistic motivation and no way to
  decide `N` from anything but curve-fitting to this dataset. It would cut
  `pl-005`'s relative clause in half depending on how the intervening phrase
  is worded, non-deterministically with respect to sentence length.
- **Plain, unconditional comma-splitting** (every comma is a new span, no
  exceptions) is *not* provably safe: it is exactly what tier (1) needs, but
  it is also what would break a case this benchmark does not happen to
  contain — *"Redis, evaluated for six months, was never adopted."* Splitting
  blindly on both commas isolates "Redis" (no verb, no marker) into its own
  span, "evaluated for six months" into a span with no technology token left
  to attach to, and files Redis as affirmative. This is why §A.5's connector
  list is *conditioned* on the following span having its own finite
  subject+verb, not "every comma."
- **Coordinating "and"/"or"** must **not** be a scope-reset connector, and
  this is worth stating explicitly because it is the connector most tempting
  to add by analogy with "but"/"while": *"Redis and Memcached were both
  evaluated and rejected"* splits, under naive "and"-splitting, into "Redis"
  (isolated, no marker, wrongly affirmative), "Memcached were both
  evaluated", "rejected" — turning one correct clause into one broken one and
  two orphaned fragments. `while`/`but`/`though` are contrastive (they
  introduce a *different* claim); `and`/`or` are conjunctive (they *extend*
  one claim across a list) and must keep the whole list in one scope.
- **A dependency parse** would close tier (3) (`sd-031`) as well as tiers
  (1)-(2), but is a categorically larger and less predictable component than
  a closed connector list plus a five-line reattachment rule, for one
  residual case out of 137 that this analysis is explicitly not asked to
  eliminate at any cost. Out of scope by the "smallest" framing of the
  brief, independent of the "no LLM/embedding" constraint (a rule-based
  dependency parser is not an LLM, and is rejected here on cost/benefit
  grounds, not on the ML restriction).

### A.6 Title fragments are not a span-granularity problem

`pl-023`, `pl-026`, and the sentence-level `job_title` category (`sd-078`
through `sd-087`) are not solved by A.1–A.5 and should not be forced to be.
A job title (*"Kubernetes Migration - Planning & Delivery Lead"*) has no
finite verb, no subject, and often no punctuation to split on at all — it is
a different **kind** of text, not a badly-punctuated sentence. `pl-023` fails
today because the title clause is the *only* place `kubernetes` is named, the
title clause also carries `planning`, and `profile_index._index_employment`
treats the title exactly like any other clause with no field-aware rule.
`pl-024`, `pl-025`, `pl-038` pass today as controls for the reasons already
established (no technology co-located in the title clause; or the technology
also independently named in a clean description clause rescues it). This
belongs in §C and in the V5 recommendation as a fourth, independent
component — not as a fifth span-granularity tier.

---

## B. Explicit negation

### B.0 Exhaustive extraction

Every case in `non_doing_v1` containing `never`, `not`, `no`, `none`,
`nothing`, `zero`, `unable` or `without` (29 of 137; script and full list
below) splits cleanly into two groups on one question: **does the same
clause also contain an existing `NON_DOING_MARKERS` word?**

```python
# run against evaluation/datasets/non_doing_v1/cases.json
import json, re
neg = re.compile(r"\b(never|not|no|none|nothing|zero|unable|without)\b", re.I)
# 29 hits total; 18 co-occur with an existing marker in the same clause
# (redundant today), 11 do not (see B.1).
```

**Group 1 — redundant today (18 cases):** `sd-002, sd-006, sd-008, sd-009,
sd-010, sd-012, sd-028, sd-043, sd-044, sd-048, sd-097, pl-004, pl-010,
pl-014, pl-018, pl-020, pl-030, pl-036`. Each of these also contains a word
already in `NON_DOING_MARKERS` in the same clause (`evaluating`, `piloted`,
`explored`, `familiar`, `keenly` [does not fire — see the `non_doing_v1`
README on word-boundary controls], `abandoned`, etc.), so `is_non_doing()`
already returns the correct answer for reasons unrelated to the negation
word. The negation word is currently inert at the classification stage. Three
of these (`pl-014`, `pl-030`, `pl-036`) turn out to carry *separate*,
non-inert information — see B.3.

**Group 2 — negation is the only signal (11 cases):** `sd-056` through
`sd-065` (the ten `explicit_non_use` cases) plus the second clause of
`pl-029`. None of these contain any `NON_DOING_MARKERS` word. This is the
group where "separate negation from `NON_DOING_MARKERS`" matters, because it
is currently not represented *anywhere*, under any name.

### B.1 Problem: plain-English denial is invisible to `is_non_doing()`

**Exact current behavior.** `is_non_doing("Never used Kubernetes outside of a
single tutorial.")` → `False`. `_SINGLE_MARKERS` (`vocabulary.py:226`) is a
flat set of single words drawn entirely from verbs of contemplation
(`evaluated`, `considered`...), lifecycle/abandonment verbs
(`cancelled`, `retired`...), planning words (`planned`, `intend`...), and
learning-adjacent words (`learning`, `curious`...). It contains **zero**
negation particles — no `not`, `never`, `no`, `none`, `zero`, `nothing`,
`unable`. This is a clean structural fact, checkable by inspection of the
literal set, not a probabilistic observation.

**Case IDs.** `sd-056` through `sd-065` (10 cases, all predicted
`defect_class: vocabulary`, all confirmed).

**First point of incorrect semantics.** `vocabulary.is_non_doing()` itself —
the word-matching step, not anything downstream. There is nothing to trace
further; the function is asked "does this clause deny use" and its complete
answer space is "does this clause contain one of 43 specific words," which
these ten sentences never do.

**Vocabulary defect, and specifically *not* fixable by adding words to
`NON_DOING_MARKERS`.** This is the one place in this whole analysis where
"just add the missing words" is the wrong instinct even though the defect
*looks* like a vocabulary gap. `not`, `never` and `no` are negator particles
that can attach to *any* predicate, including affirmative ones —
*"This is not a hobby project, it has been in production for six years"*
contains `not` and is unambiguously affirmative. Adding bare negators to a
flat single-word marker set the way `retired` or `curious` were added would
not close the gap safely; it would open a large, unbounded new false-positive
surface, because a flat word list has no way to say "`not` counts only when
it negates a use-predicate." What the ten cases actually share is a
**negator immediately scoping a small, closed set of use-predicates**: `never
used`, `not worked (with)`, `no hands-on ... experience`, `does not have ...
experience with`, `never wrote`, `nothing ... done with`, `no experience
with`, `zero ... hours with`. This is a *bigram/trigram pattern* shape
(negator + predicate class, within a short window), structurally different
from `NON_DOING_MARKERS`'s flat single-word/fixed-phrase shape — which is
exactly why the brief's "separate negation from existing `NON_DOING_MARKERS`"
instruction is the technically correct move, not merely a scoping
preference.

### B.2 Problem: an isolated negation clause manufactures a false MATCHED

**Exact current behavior.** `pl-029`'s description — *"Intend to introduce
Rust for performance-critical services once the team completes onboarding;
no Rust code has shipped yet."* — is split by the semicolon into two clauses.
Clause 1 correctly fires on `intend` and files `rust` as a `mentions` entry.
Clause 2, *"no Rust code has shipped yet,"* contains no marker word, so
`is_non_doing` → `False` → `demonstrates` → `True` → `rust` is filed as an
**`employment`** entry for the *same role*. `TokenEvidence.tier` checks
`employment` first, so the role resolves to `employment_described`, and
`judge_experience` against a "2+ years of Rust" requirement returns
**`matched`** — from a sentence whose entire content is a denial that any
Rust code exists.

**Case ID.** `pl-029` — the one case in the whole 137 that was authored as a
control (`defect_class: none`) and disagreed anyway (`BASELINE_REPORT.md`,
"The one surprise").

**First point of incorrect semantics.** The same line as §A.1,
`profile_index.py:185` — but note this is a *different failure mode* through
the *same line*, not a duplicate of §A.1: in §A.1 two technologies share a
scope and a real marker's effect leaks between them; here there is no marker
at all, and an absence of caveat is being treated as presence of proof. The
loop does not distinguish "no reason to doubt this" from "this looks like a
positive statement," and the current vocabulary supplies no way for a clause
to affirmatively assert `False` for "was this done" without an existing
marker firing `True` first.

**Necessary condition, stated precisely (checked against all 11 Group-2
cases):** the false-`MATCHED` cascade requires all three of: (1) no other
clean corroborating evidence for that technology exists elsewhere in the
profile; (2) the negation clause is punctuation-separated from any clause
that *does* contain a real marker (so the marker's protection does not
already cover it); (3) the technology token is named inside the negation
clause's own text. `pl-029` satisfies all three. The ten `sd-05x` cases are
sentence-level only and were never embedded in a profile, so they demonstrate
the vocabulary gap (B.1) without individually demonstrating the cascade —
`pl-029` is the only case that shows the cascade end-to-end, but the
mechanism generalizes to any of the ten by construction.

**What must be preserved to prevent it — representation, not just
vocabulary.** A closed, curated `EXPLICIT_NEGATION_PATTERNS` table (negator +
use-predicate, bounded window — mirroring `NON_DOING_MARKERS`'s curation
discipline but living in its own name and its own file, not merged into it)
closes B.1 and, combined with the existing clause mechanism, closes B.2's
specific cascade. But it must be evaluated as **its own boolean**, unioned
with (not substituted for) the marker check — because the two questions are
different ("does this describe contemplation/lifecycle/learning" vs. "does
this explicitly deny the predicate happened") and conflating them into one
list is what made the naive fix in B.1 unsafe in the first place.

### B.3 Problem: even where negation is redundant for classification, it carries a magnitude the tier model has no field for

**Exact current behavior.** `pl-014` (*"Some exposure to Databricks from a
two-day training, **no production work with it**."*), `pl-030` (*"...three
weeks; the rollout **was not continued**."*), `pl-036` (*"...after two weeks
when the query patterns **did not fit**..."*) all correctly land on tier
`mentioned` — the negation phrase is not needed for that, an existing marker
(`exposure`, `piloted`, `abandoned`) already got it right. The gap is one
stage later: a human reading *"no production work with it"* or *"three
weeks... not continued"* reads an explicit, small-or-zero duration and
concludes the requirement is **`missing`** (settled in the negative), not
merely **`uncertain`** (unknown). `rules._judge_technology_duration`
(`rules.py:881`) can only read a number from two places — a dated
`employment` span (absent here, by construction: the clause is a mention),
or `Skill.years_of_experience` (never set for a mention-only technology) —
so it falls through to its final branch and reports `uncertain` regardless
of how explicit the prose is.

**Case IDs.** `pl-014`, `pl-030`, `pl-036` — exactly the `evidence_model`
bucket in `BASELINE_REPORT.md`'s defect classification, 3 of 3.

**First point of incorrect semantics.** Not `is_non_doing`, not the clause
split, not the tier — all three are already correct for these cases. The
gap is in `TokenEvidence` itself (`evidence.py`, the `declarations` /
`employment` / `mentions` dataclass): it has no field capable of holding "an
explicit, small, prose-stated duration attached to a mention," so
`_judge_technology_duration` has nothing to read even in principle. This is
the one problem in this entire analysis that is a **pure representation
defect with no vocabulary path fixing it at all** — no word added to any
list changes what `TokenEvidence` can carry.

---

## C. Technology attribution

### C.0 The full trace, function by function

```
Experience.description
  → vocabulary.sentences()                    clause split, vocabulary.py:221
  → vocabulary.is_non_doing(clause)            ONE boolean per clause, vocabulary.py:315
  → evidence.demonstrates(_, clause)           thin wrapper, ignores its first arg, evidence.py:266
  → ProfileIndex._index_employment()           broadcasts the boolean to every token
        for token in phrase_variants(clause):    phrase_variants(clause), profile_index.py:185
            bucket(token).employment.append(evidence)   if shows
            bucket(token).mentions.append(evidence)     if not shows
  → TokenEvidence.tier                          declarations/employment/mentions → 4-way tier, evidence.py:218
  → ProfileIndex.employment_mentioning(token)   reads ONLY .employment, profile_index.py:262
  → rules._judge_technology_duration()          merge_years() over dated employment spans only, rules.py:881
  → rules.judge_experience() / _judge_token()   tier + duration + standard → matched/missing/uncertain
```

Separately, and **not** where attribution breaks:
`rules.resolve_technology_subject()` (`rules.py:714`) decides which token a
*requirement's own wording* is about (e.g. "3+ years of professional Go
experience" → `go`). This was checked against all 38 pipeline cases in
`non_doing_v1`, including every attribution-failure case above — **zero
mismatches**. This half of "attribution" (matching_v2's own fix, per
`docs/matching.md`) is empirically sound today; nothing below implicates it.

### C.1 Where evidence for one technology can leak to another — the exact point, quantified

There is exactly **one** point in the trace above where leakage is possible:
`profile_index.py:185`, the `for token in phrase_variants(clause)` loop.
Every stage after it (`TokenEvidence.tier`, `employment_mentioning`,
`_judge_technology_duration`, `judge_experience`) is a faithful, leak-free
function of whatever that loop already decided — they do not introduce new
attribution errors, they *launder* the one that already happened into
increasingly consequential forms (a wrong tier becomes a missing duration
becomes a flipped verdict). This was checked directly: of the 6 pipeline
cases where the loop leaks evidence between two named technologies
(`pl-001, pl-002, pl-003, pl-004, pl-005, pl-006`), **100%** also
show a flipped final verdict (`matched` expected → `uncertain` actual). There
is no case in `non_doing_v1` where leakage happens at the loop but is
absorbed before reaching the verdict, **except** when the profile happens to
contain independent, cleanly-attributed evidence for the same technology
elsewhere (`pl-007`, `pl-018`, `pl-031`, `pl-034`, `pl-038` — the "rescue"
controls). The system's only defense against this leakage today is
happenstance redundancy, not anything designed to prevent the leak.

Two distinguishable sub-shapes of leakage exist at that one line, both worth
separating because a fix for one does not imply a fix for the other, plus a
third, adjacent shape that runs through the identical line but is rooted in
a word's meaning rather than in the span it was evaluated over:

- **Technology-to-technology leakage** — the marker's true subject and the
  affected token are *both* recognized technologies. `pl-001, pl-002, pl-003,
  pl-004, pl-005, pl-006` (6 pipeline cases, `defect_class: attribution`,
  all 6 confirmed).
- **Non-technology-noun-to-technology leakage** — the marker's true
  grammatical subject is *not* a technology at all (a job scheduler, a
  backlog of tickets, a set of vendor proposals, a job title's own project
  name), and a technology merely co-occurs in the same clause. `pl-009,
  pl-011, pl-019, pl-023, pl-028` (5 pipeline cases) and, at the sentence
  level with no pipeline stage to trace, `sd-029, sd-030, sd-031, sd-069,
  sd-070, sd-082, sd-088, sd-089, sd-090, sd-091, sd-092, sd-093` (12 cases).
  All 17 are `defect_class: clause_context`, all 17 confirmed. Combined with
  the 6 above: **23 confirmed cases** share this one root mechanism —
  exactly `attribution` (6 confirmed) + `clause_context` (17 confirmed) from
  the baseline report's defect classification. (`pl-023` is the title-fragment
  shape of this same mechanism — see §A.6 — counted here because it is
  mechanically identical at this line, even though its fix belongs with
  component 4, not components 1-2, in the architecture below.)
- **Self-referential directional ambiguity** — a single technology, one
  clause, no second noun phrase involved at all: `pl-032`'s second role,
  *"Migrated the checkout pipeline to Kafka, replacing the polling-based
  batch jobs, now streaming every order in real time."* `replacing` fires on
  the clause and demotes `kafka` — the technology *performing* the
  replacement, not the one being replaced away. Tagged `vocabulary`, not
  `attribution` or `clause_context`, when this dataset was authored, because
  the word `replacing` itself does not encode which side of a replacement its
  own clause's technology is on — no span boundary changes that fact in
  general. It happens, in this specific sentence, that a comma already
  separates `kafka` (in "Migrated the checkout pipeline to Kafka") from
  `replacing` (in "replacing the polling-based batch jobs, now streaming..."),
  so the §A.3/§A.5 span mechanism would incidentally fix *this exact
  phrasing* too. It would not fix an equivalent sentence with no comma
  between the technology and `replacing` ("...migrated the checkout pipeline
  to Kafka replacing the old batch jobs...") — there, only recognizing that
  `replacing`'s own clause names the *new* side, not the *old* one, would
  help, which is a vocabulary/sense question, not a span question. `pl-032`
  is excluded from the 23 above for this reason: it is real, and it runs
  through the same line, but attributing it to "representation" would credit
  the fix for the wrong reason.

### C.2 The downstream trace for one representative case, in full

`pl-001` — *"Deprecated the legacy PHP monolith while building its
replacement in Go, which now serves all production traffic."* — requirement
*"3+ years of professional Go experience"*, role dated 2019-01-01 to present,
`as_of` 2026-01-01 (a genuine 7.0 years):

| stage | expected (authored independently) | actual (measured) | first divergence? |
|---|---|---|---|
| 1-2. non-doing / attribution | `go` → `attributed_employment=true` | `go` → `attributed_employment=false` | **yes — here** |
| 3. evidence tier | `go` → `employment_described` | `go` → `mentioned` | inherited from stage 1-2 |
| 4. experience duration | `7.0` years, measurable | `uncertain/none` (`employment_mentioning("go")` returns nothing to sum) | inherited |
| 5. requirement verdict | `matched` | `uncertain` | inherited |

Nothing invents a new error after stage 1-2; every later column is the
mechanical, correct-given-its-input consequence of the one wrong entry at the
top. This is the general pattern for all 6 technology-to-technology cases,
and for the 3 non-technology-noun cases that are also duration requirements
(`pl-009, pl-011, pl-023` — the identical four-row shape). The other 2
non-technology-noun cases (`pl-019, pl-028`) are plain skill requirements
with no duration to measure, so they stop one row earlier, at the
tier → verdict step, for the same underlying reason.

### C.3 Representation vs. vocabulary, for the whole of C

Every one of the 23 leakage cases in §C.1 is a representation defect: the
*word* that fires in each of `pl-001` through `pl-028` (`deprecated`,
`investigated`, `explored`, `piloted`, `prototyped`, `migrating away`,
`retired`, `planning`, `familiar`, `studying`) is a legitimate,
correctly-firing member of `NON_DOING_MARKERS` for *some* sentence — the
defect is never "this word should not be a marker." It is always "this
clause's one boolean was applied to a token it does not describe." No change
to any vocabulary list closes any of these 23 cases; only a finer-grained
span (§A) or a per-technology reattachment rule (§A.4) does. This is the
cleanest representation/vocabulary split in the whole analysis: these 23
defects are 100% representation, 0% vocabulary, by construction of what
"leakage" (as opposed to `pl-032`'s directional-ambiguity, deliberately
excluded — see §C.1) means.

---

## The smallest deterministic architecture that represents the observed cases

Four components. None implemented. Each is independently addable — a team
could ship one without the others and each would close a disjoint slice of
`non_doing_v1`'s confirmed failures.

```
1. SCOPE_RESET_CONNECTORS                  (new, closed vocabulary, own file)
     a small, curated list — "but", "while", "though", "although", "before",
     "after", "instead", "in favor of", "and then", "and ultimately" — used
     to split each existing clause into SPANS wherever the following text
     has its own subject and finite verb. Explicitly excludes "and"/"or".
     Curated with the same discipline as NON_DOING_MARKERS; not a rewrite of
     it, a sibling to it. Closes the 16 tier-1 cases listed in §A.3 — which
     includes pl-032 (§C.1's directional-ambiguity case) incidentally rather
     than by design, because this specific sentence's own comma happens to
     separate its technology from the marker; a differently-punctuated
     equivalent would still need a vocabulary fix, not this one.

2. NEAREST-TOKEN REATTACHMENT              (new, ~5-line rule, no new vocab)
     a span opening with a relative pronoun (which/that/who) or a bare
     participle after a comma attaches its own is_non_doing() verdict to the
     nearest preceding technology token, not to its enclosing span's whole
     token set. Depends on (1) only for span boundaries; does not require
     solving general "which word modifies which." Closes pl-005, pl-009,
     pl-011, and — via the pipeline's EXISTING employment-before-mentions
     precedence, unmodified — pl-019.

3. EXPLICIT_NEGATION_PATTERNS              (new, closed vocabulary, own file)
     a small table of (negator, use-predicate) bigram/trigram patterns —
     "never used", "not worked with", "no hands-on ... experience", "zero
     ... hours with", "nothing done with" — evaluated as its OWN boolean,
     unioned with (not merged into) the existing marker check at the span
     level. Structurally a pattern table, not a flat word list, because a
     bare negator is unsafe as a flat-list member (§B.1). Closes sd-056
     through sd-065 and the false-MATCHED cascade shape demonstrated by
     pl-029.

4. TITLE-AWARE INDEXING                    (new, small rule, field-scoped)
     Experience.job_title is already a structurally distinct field from
     Experience.description; give it its own default-polarity rule instead
     of running it through the same clause path as prose. A marker word
     alone in a title does not demote a technology also named in that title
     unless (3) also fires, or the title matches a small curated
     personal/employment-status prefix pattern (^(Former|Retired|Ex-)\b).
     Closes pl-023, pl-026, and the sentence-level job_title category.
```

Explicitly **not** part of this shape, and why each is excluded:

- **A `TokenEvidence.prose_duration` field** (or equivalent) to carry an
  explicit small/zero duration extracted from a mention's own text — this
  would close `pl-014, pl-030, pl-036` (§B.3), but it is a genuinely
  *separate* representation change (in `evidence.py`'s dataclass shape, not
  in span/clause mechanics or vocabulary at all) with its own design
  questions (how is "three weeks" parsed into a number without a duration
  grammar? does it need one at all, or just a MISSING-eligible flag rather
  than a magnitude?). Named here as a fifth, independent candidate for scope,
  not folded into the four above because it touches a different file for a
  different reason.
- **Fixing `sd-031`'s zero-delimiter, wrong-target-noun, no-rescue shape** —
  one case, needs genuine dependency information, rejected in §A.6 on
  cost/benefit grounds specific to this dataset's size, not attempted here.
  (`pl-019` looks the same but is not in this residual — see §A.3/§A.4.)
- **Any change to `NON_DOING_MARKERS`, `MARKER_BEARING_NAMES`,
  `is_non_doing()`, `demonstrates()`, or `ALGORITHM_VERSION`** — the vocabulary
  findings in §A/B/C (proper-name collisions, `familiar`/`curious` plus a
  duration-intensifier, `deprecated`/`retired`/`decommissioned` applied to
  something plainly self-operated for years) are real and are curation work,
  but they are explicitly out of scope for *this* document, which was asked
  for an architecture analysis, not a vocabulary diff.

---

## Recommendation for Matching V5

Scope Matching V5 as components **1-4** above, in that order, each gated on
`non_doing_v1` (extended, if needed, with the residual/counter-shape cases
named in §A.4 and §B.3) rather than on the current 137-case baseline alone.
Order matters: (1) is the foundation the other three read; (2) and (4) are
independent of each other and of (3); (3) is independent of all three and
could ship alone. None require a model, an embedding, or a change to the
existing curated-vocabulary posture the codebase has held since `matching_v2`
— every component is a closed list, a small dataclass addition, or a rule a
person can read and apply by hand, which is the property this codebase's own
`docs/matching.md` says a deterministic baseline exists to preserve.

What V5 should **not** attempt in the same milestone: the `TokenEvidence`
duration-magnitude field (§B.3) and the zero-delimiter noun-attachment gap
(§A.6) are real, are smaller in count (3 and 1 cases respectively) than
components 1-4's combined ~23+11, and each is a genuinely separate design
question — bundling them in risks the same thing `matching_v1 → v2`'s own
history warns against (see `docs/matching.md`): shipping a plausible-looking
bundle without a specific, falsifiable case driving each piece of it.

This is a recommendation for what to scope, not an implementation, a
schedule, or a version bump. `ALGORITHM_VERSION` stays `matching_v4` until
someone builds this and measures it against `non_doing_v1` the way
`matching_v1 → v2` was measured against `matching_real_v1`.
