# Matching V5 — implementation results

Implemented in the four independent steps `docs/matching-v5-implementation-design.md`
specified, each built, tested, and benchmarked before the next started. This
document is the record of what actually happened at each gate, and the final
V4↔V5 comparison across the real matching benchmark and `non_doing_v1`.

**Update, released:** the two items this document originally deferred —
bumping `ALGORITHM_VERSION` to `matching_v5` and retiring `non_doing_v1` into
`non_doing_v2` — were completed as a deliberate, separate release step. See
`docs/matching-v5-release.md` for that work and the final measurements
against `non_doing_v2` (139 cases) and `matching_real_v1`. Everything below
this line is the record of the four implementation steps as they were
built and measured, unedited since. Nothing in `NON_DOING_MARKERS`, `is_non_doing()`,
`resolve_technology_subject()`, or `evaluation/datasets/non_doing_v1/cases.json`
was changed.

## What was built

| file | status |
|---|---|
| `app/services/matching/span_split.py` | new — Step 1 (`split_into_spans`) + Step 2 (`nearest_preceding_token`, `is_reattachment_opener`) |
| `app/services/matching/negation.py` | new — Step 3 (`EXPLICIT_NEGATION_PATTERNS`, `negation_fires`) |
| `app/services/matching/evidence.py` | additive — `demonstrates_v5` (Step 3), `demonstrates_title` (Step 4); `demonstrates()` itself unchanged |
| `app/services/matching/profile_index.py` | `_index_employment` restructured into `_index_experience_clause` (title path, unchanged behaviour until Step 4) and `_index_experience_description_clause` (new, span-aware path) |
| `tests/test_matching_span_split.py` | new — 35 tests |
| `tests/test_matching_negation.py` | new — 35 tests |
| `tests/test_matching_demonstrates_title.py` | new — 12 tests |
| `tests/test_matching_evidence_tiers.py` | +1 new regression test; 1 existing assertion updated (see Step 1) |

## Step 1 — scope-reset span splitting

**Targeted tests**: 22 new tests, all passing — one per connector in `SCOPE_RESET_CONNECTORS`,
the master-invariant fallbacks (no marker → no split; marker with nothing after it → no split),
`MARKER_BEARING_NAMES` protection, and the Redis-hypothetical safety case from
the design doc, pinned permanently.

**Full regression**: 1228 → 1251 passing. One existing assertion needed
updating — `test_a_technology_evaluated_and_abandoned_is_a_mention_not_experience`
asserted `"cancelled" in verdict.evidence[0].quote`; the quote is now the
specific span that names the token ("Evaluated React Native for a mobile
app,") rather than the whole clause, which is the intended, documented
improvement (§ "Evidence provenance" in the architecture analysis), not a
regression. Updated to assert `"Evaluated"` instead — the tier and verdict
are unchanged.

**`non_doing_v1` benchmark**: pipeline agreement 0.474 → 0.632 (18 → 24 of
38). Sentence-level agreement did not move (0.545) — every sentence case
calls `is_non_doing()` directly, which this step does not touch; see
"Scope boundary" below.

**Finding**: implementing the same-clause precedence safeguard (a later
span's literal repeat of a token may not promote it past an earlier span's
`mentions` verdict, in the same original clause) blocks `pl-028` from
closing, permanently, as a deliberate cost of preferring UNCERTAIN over
inventing evidence. Confirmed with a dedicated regression test
(`test_a_technology_repeated_across_a_split_stays_a_mention_not_employment`)
built from the exact case the design doc identified: *"Evaluated Kafka,
before abandoning Kafka entirely for cost reasons."*

## Step 2 — position-based reattachment

**Targeted tests**: 13 new tests, all passing.

**A real bug, caught before it shipped**: the first implementation of
`nearest_preceding_token` had no relevance filter, so `canonical()` — which
normalizes any text, not just technologies — handed back `"services"` as the
"nearest" token in *"...on top of the existing gRPC services,"* because it is
the last word before the comma, not because it names anything. Fixed by
requiring a caller-supplied `is_relevant` predicate
(`vocabulary.is_technology`, closed over the profile's declared tokens,
exactly as `rules.resolve_technology_subject` already uses it), pinned by
`test_nearest_preceding_token_ignores_generic_words` and
`test_nearest_preceding_token_picks_the_textually_later_of_two_technologies`
— the latter is the specific unordered-set shape flagged in the design
doc's §2.2, re-run 50 times to prove it is no longer set-order-dependent.

**Full regression**: 1251 → 1264 passing, no new updates needed to existing
tests.

**`non_doing_v1` benchmark**: pipeline agreement 0.632 → 0.737 (24 → 28 of
38): `pl-005`, `pl-011`, `pl-019` closed via reattachment; `pl-032` closed
incidentally (its trailing "now streaming every order in real time" clause
happens to trigger the participle-opener rule) — not a principled fix for
"replacing"'s directional ambiguity, and not claimed as one; a differently
worded equivalent sentence would still fail.

## Step 3 — negation patterns, kept isolated from `NON_DOING_MARKERS`

The three-way distinction the design specified is enforced structurally, not
just documented:

```
marker-based non-doing        vocabulary.NON_DOING_MARKERS + is_non_doing()   UNCHANGED
        ≠
negation                      negation.EXPLICIT_NEGATION_PATTERNS + negation_fires()   NEW, separate file
        ≠
magnitude of experience       not attempted — see "What was not done"
```

`demonstrates_v5` composes the first two as `demonstrates(...) and not
negation_fires(...)` — an AND of two independently-testable functions, never
a merged word list.

**Targeted tests**: 35 new tests — all 10 `explicit_non_use` cases plus
`pl-029`'s negation clause (all fire), all 6 adversarial controls from the
design doc plus 6 additional per-pattern near-misses (none fire, including
*"There is no reason it hasn't shipped already"* and *"Not only did it ship,
it shipped ahead of schedule"* — both constructed specifically to probe the
patterns' boundaries).

**Full regression**: 1264 → 1299 passing.

**`non_doing_v1` benchmark**: pipeline agreement 0.737 → 0.763 (28 → 29 of
38): `pl-029` — the single false-`MATCHED` cascade found during the
architecture analysis, and the most important case in the whole dataset —
closes. `explicit_non_use` (sentence-level, 10 cases) stays at 0 for the same
structural reason as Step 1: those cases call `is_non_doing()` directly.

## Step 4 — title-aware indexing, kept separate from description semantics

`demonstrates_title` is a **third**, distinct function — not a parameter on
`demonstrates`/`demonstrates_v5`, so a title clause and a description clause
can never be passed to the wrong one by accident. It ignores
`NON_DOING_MARKERS` entirely for title text (a title's grammar does not carry
a marker's caveat meaning) and defers only to `negation_fires`, which can
still weaken a title.

**Targeted tests**: 12 new tests — all 10 `non_doing_v1` `job_title` sentence
texts confirmed `True` under the new function (re-verified: every one of the
ten expects `False` for `is_non_doing`, i.e. `True` for "demonstrates"), plus
a constructed negated title and the empty/`None` defaults.

**Full regression**: 1299 → 1311 passing.

**`non_doing_v1` benchmark**: pipeline agreement 0.763 → 0.816 (29 → 31 of
38): `pl-023`, `pl-026` close. `job_title_path` (the pipeline category) goes
to 6/6. The sentence-level `job_title` category (10 cases) stays at 5/10 —
the same scope boundary: those cases test `is_non_doing()` on a bare title
string directly, which no compliant design can move.

## Step 5 — full comparison

### `non_doing_v1`: before every step vs. after all four

| | sentence (99) | pipeline (38) | overall (137) |
|---|---|---|---|
| baseline (before any step) | 0.545 (54) | 0.474 (18) | 0.526 (72) |
| after step 1 (spans) | 0.545 (54) | 0.632 (24) | 0.569 (78) |
| after step 2 (+ reattachment) | 0.545 (54) | 0.737 (28) | 0.599 (82) |
| after step 3 (+ negation) | 0.545 (54) | 0.763 (29) | 0.606 (83) |
| after step 4 (+ titles) | 0.545 (54) | **0.816 (31)** | **0.620 (85)** |

**+13 cases overall, entirely on the pipeline side**, using four small
deterministic components. Sentence-level agreement is unchanged by
construction — see "Scope boundary," below.

**Remaining `non_doing_v1` failures, all previously identified and none new**:

- `pl-012, pl-017, pl-035` — the "familiar/curious + intensifier" vocabulary
  defect (`familiar with X through hands-on work over N years` should
  strengthen, not weaken, a claim). Vocabulary work, out of scope for V5.
- `pl-028` — the accepted same-clause safeguard cost (Step 1).
- `sd-031` — the zero-delimiter, no-rescue residual named in the
  architecture analysis; needs real dependency information.
- `sd-099` — the em/en-dash tokenizer gap; a second, narrower instance of
  the already-fixed scikit-learn class of bug.
- All 10 sentence-level `explicit_non_use` and all 10 `job_title` cases, and
  9 of 10 minimal pairs' "negative" halves — the scope boundary below.
- 3 `evidence_model` cases (`pl-014, pl-030, pl-036`) — component 5,
  explicitly deferred.

**Defect classification, before → after** (predicted-and-confirmed counts):

```
attribution        6 → 0   (all 7 members now resilient)
clause_context    17 → 13  (4 closed: pl-009, pl-011, pl-019, pl-023)
vocabulary        37 → 35  (2 closed incidentally: sd-024, pl-032)
tokenizer          1 → 1   (unchanged — not attempted)
evidence_model     3 → 3   (unchanged — component 5, deferred)
surprises          1 → 0   (pl-029 — the false-MATCHED cascade — resolved)
```

### Scope boundary, stated once and precisely

Every non_doing_v1 **sentence-level** case scores `is_non_doing(case.text)`
directly. Steps 1, 2 and 3 live in `profile_index.py`, one layer above that
function; step 4 lives beside it, never inside it. None of the four steps
change what `is_non_doing()` itself returns for any input, which was an
explicit, repeated constraint across all three prior conversations in this
project. **This means sentence-level agreement (0.545) cannot move under any
design that honors that constraint, full stop** — not a partial result, a
structural one. All of this run's real, measured improvement is in the
pipeline-level number, which is what a real match actually computes from.

### Real matching benchmark (`matching_real_v1`)

Reconstructed the exact pre-matching_v5 `_index_employment` (verbatim, from
this session's own earlier read of the file, not from memory of intent) and
ran `evaluation.benchmark.runner` against both versions.

**Result: byte-for-byte identical report**, both versions:

```
false matches              4    false-match rate  0.044
false missing               3
matched   precision 0.947  recall 0.910  f1 0.928
missing   precision 0.943  recall 0.962  f1 0.952
uncertain precision 0.805  recall 0.846  f1 0.825
required agreement    0.928     preferred agreement   0.864
evidence correctness  0.976     required/preferred read 1.000
```

**Zero change, in every direction.** No false match, false missing,
uncertain classification, evidence citation, or required/preferred reading
moved. This is the strongest evidence available that matching_v5 is purely
additive with respect to the matching_v2 rules the real-data benchmark
exercises: none of its 13 profiles happen to contain the specific
multi-clause, multi-technology, or negation-only shapes matching_v5 targets,
so nothing in it was touched, in either direction.

### Duration effects

`non_doing_v1`'s duration-consequence cases moved from **11 duration
mismatches to 1** across steps 1-4 (§ per-step tables above). The one
remaining, `pl-012`, is the vocabulary-only "familiar + intensifier" defect —
duration mechanics did their job correctly once given the right tier;
nothing about the duration *calculation* itself needed to change anywhere in
this work, confirming the architecture analysis's own finding that the
duration gaps were almost entirely downstream consequences of the tier/
attribution defects, not a separate problem needing its own fix.

## What was not done, on purpose

- **Component 5** (magnitude-bearing negation, `pl-014/030/036`) — not
  attempted, exactly as scoped. `EXPLICIT_NEGATION_PATTERNS` recognizes that
  a mention denies use; it still has no way to say "and the prose states how
  little," which needs a new `Evidence` field and a `rules.py` branch, a
  different and larger change than any of the four steps here.
- **`ALGORITHM_VERSION` was left at `matching_v4` by this document**, per the
  implementation design's own GO conditions: the bump belongs with a
  deliberate ship decision, together with retiring `non_doing_v1` in favor of
  a `v2` that documents the sentence-level cases this work cannot move (per
  the design doc's §6 item 7) and adds the same-clause regression case as a
  permanent benchmark entry rather than only a unit test. Both were completed
  afterward, as their own release step — see `docs/matching-v5-release.md`.
- `sd-031`'s zero-delimiter residual, `pl-032`'s general "replacing"
  ambiguity, and `sd-099`'s dash-splitting gap remain named, not silently
  dropped, exactly as the implementation design scoped them.
