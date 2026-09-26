# Matching V5 — release report

The four matching_v5 components (`docs/matching-v5-implementation-design.md`,
`docs/matching-v5-results.md`) were implemented and independently validated
in a prior step and were **not modified** here. This document covers only
the versioning and benchmark-retirement work: bumping `ALGORITHM_VERSION`,
updating every place that exposes it, verifying idempotency and
reproducibility hold under the new version, retiring `non_doing_v1` into
`non_doing_v2`, and re-running every check that could be affected.

No semantic matching, embeddings, LLM calls, dependency parsers, or
duration-magnitude handling were introduced. `matching_real_v1`'s ground
truth was not touched.

## 1. V4 → V5 behavioral changes

Nothing in this release step changed matching_v5's *behavior* — that was
fixed and tested in the prior implementation step. What changed is which
name that behavior answers to, and since when:

- `ALGORITHM_VERSION` is now `ALGORITHM_V5 = "matching_v5"`
  (`app/models/match.py`), imported and set in
  `app/services/matching/engine.py`, exactly the same mechanism as every
  prior bump (v1→v2→v3→v4).
- `ALGORITHM_V4 = "matching_v4"` is untouched, still defined, still the value
  every pre-existing stored result carries.
- The engine's own version-history docstring (`engine.py`) and
  `docs/matching.md`'s "Algorithm versioning" section both gained a **v4 → v5**
  paragraph, in the same place and the same style as the v1→v2, v2→v3, v3→v4
  paragraphs already there — none of those three were edited.
- The one behavioral consequence of the bump itself (not of matching_v5's
  logic, which is unchanged): any profile+job pair already matched under
  matching_v4 will be **re-evaluated** the next time it is requested, because
  `algorithm_version` is part of the idempotency key (§4). This is the same
  consequence every prior version bump had, verified directly in §4.

## 2. Benchmark results

### `non_doing_v2` (retired from `non_doing_v1`), measured against matching_v5

```
                       sentence (99)   pipeline (40)   overall (139)
non_doing_v2 / v5      0.545 (54)      0.825 (33)      0.626 (87)
```

139 cases: the 137 from `non_doing_v1`, unchanged, plus two new pipeline
cases added as part of retirement (§5 in
`docs/matching-v5-implementation-design.md` recommended exactly this):

- **`pl-039`** — the same-clause repeated-token regression
  ("Evaluated Kafka, before abandoning Kafka entirely for cost reasons.")
  that motivated matching_v5's same-clause precedence rule, previously
  pinned only as a unit test
  (`test_a_technology_repeated_across_a_split_stays_a_mention_not_employment`).
  Passes as a control: `kafka` correctly stays `mentioned`, not promoted to
  `employment_described`.
- **`pl-040`** — a `though`-connector case with no comma anywhere in the
  sentence, so the connector is the sole scope-reset trigger. `though`
  shipped in `SCOPE_RESET_CONNECTORS` with no case in `non_doing_v1`
  exercising it at all; this closes that gap. Passes as a control.

Both are authored, independently-reasoned expectations, not values copied
from a run — consistent with every other case in the dataset (see the
dataset's own `review.independence_statement`).

`non_doing_v1` is frozen exactly as it was and stays on disk as the
historical baseline `matching_v4` was measured against
(`evaluation/datasets/non_doing_v1/BASELINE_REPORT.md`, not re-run or edited).
`evaluation/nondoing/groundtruth.DEFAULT_VERSION` now points at
`non_doing_v2`; `--dataset non_doing_v1` still loads and runs the retired set
for historical reference.

### `matching_real_v1` (ground truth untouched)

Ran with the exact, verbatim pre-matching_v5 `_index_employment`
(reconstructed from this session's own earlier read of the file, not from
memory of intent — see `docs/matching-v5-results.md`'s Step 5) against the
current, post-version-bump code, and diffed the two full reports:

```diff
- algorithm      matching_v4
+ algorithm      matching_v5
```

**That is the entire diff.** Every other line — false matches (4), false
missing (3), per-verdict precision/recall/F1, the full confusion matrix,
required agreement (0.928), preferred agreement (0.864), evidence correctness
(0.976), required/preferred classification (1.000), agreement by aspect,
agreement by category, self-consistency (26/26), reproducibility (26/26) —
is byte-for-byte identical. `matching_real_v1`'s 13 profiles and 12 postings
do not happen to contain the multi-clause, multi-technology, or
negation-only shapes matching_v5 targets, so nothing in this benchmark moved
in either direction, and its ground truth was not opened for editing.

### Matching regression suite (`matching_v2` rule-level dataset)

```
requirements judged        51
classification accuracy    1.000
false matches               0
false missing                0
uncertainty overcautious    0
uncertainty overconfident   0
evidence complete        True
score consistent         True
reproducible             True
```

Unchanged from before the release step; reports `algorithm: matching_v5`.

## 3. Regression results

| suite | result |
|---|---|
| Full backend suite (`pytest`, services/api) | **1312 passed**, 0 failed |
| `tests/test_matching_concurrency.py` (real multi-threaded race, §4) | **3 passed** |
| Frontend (`vitest`, apps/web) | **114 passed**, 1 test file's assertion updated (§6) |
| TypeScript check (`tsc --noEmit`) | clean, 0 errors |
| Frontend production build (`next build`) | succeeded, 9/9 static pages generated |
| Alembic `upgrade head` → `check` → `downgrade -1` → `upgrade head` | clean cycle, `alembic check` reported "No new upgrade operations detected" both before and after — confirming no schema migration was needed for this release, and none was added |
| OpenAPI export + TypeScript generation (`npm run api:types`) | regenerated; `algorithm_version` was already typed as a plain `string` (not an enum/literal) in `app/schemas/match.py`, so the generated contract is structurally unchanged — the run confirms this rather than fixing anything |

Five existing test assertions were updated because they hardcoded the
version string as an expected *value* (not a behavior), the same kind of
one-line change every prior version bump made:

- `tests/test_matching_service.py` — `algorithm_version == "matching_v4"` → `"matching_v5"`
- `tests/test_matching_engine.py` — same, plus `ALGORITHM_V4` → `ALGORITHM_V5` import
- `tests/test_matching_location.py` — same (the file's own docstring, describing when location matching was *introduced*, correctly still says "matching_v4" — that is a historical fact, not a current-version claim, and was left alone)
- `tests/test_matching_api.py` — same, on the live API response
- `apps/web/src/test/match-fixtures.ts` + `apps/web/src/app/jobs/[id]/match/page.test.tsx` — the mock fixture and the one frontend test that asserts the *humanized display text* ("Matching v4" → "Matching v5")

Every other "matching_v4" reference in the codebase (`docs/job-intelligence.md`,
`app/services/matching/geography.py`, `app/services/matching/rules.py`,
`docs/matching.md`'s Location section, and matching_v5's own source
comments contrasting new behavior against old) is a **historical** statement
about when a feature was introduced or what the prior algorithm did, and was
deliberately left unchanged — exactly the same posture the v1/v2/v3 history
already models.

## 4. Idempotency / reproducibility verification

**The schema already anticipated this.** `algorithm_version` has been part of
`uq_match_results_version_requirements_algorithm` (the idempotency key) and
of `service._find_existing`'s lookup query since matching_v2 — no code in
`service.py` or the database schema needed to change for v5 to be treated as
distinct from v4. This release step *verified* that design holds under a
real version bump, rather than building new logic for it:

- **New test**, `test_a_historical_v4_result_is_untouched_and_v5_creates_its_own_row`
  (`tests/test_matching_service.py`): inserts a `MatchResult` row with
  `algorithm_version="matching_v4"` directly, then calls `create_match` for
  the same profile+job under the running `matching_v5` code. Asserts: a
  **new** row is created (`created is True`), the new row's id differs from
  the old one, the new row's `algorithm_version` is `"matching_v5"`, **both**
  rows persist afterward, and the v4 row's own fields
  (`required_missing == 1`) are **byte-identical** to what was inserted —
  proving persisted v4 results are never rewritten. Passing.
- **Existing test**, `test_the_same_request_twice_returns_the_same_row`: two
  sequential calls for the same profile+job under matching_v5 return the
  same row, `created=False` the second time. Passing, unmodified.
- **Existing test suite**, `tests/test_matching_concurrency.py`: six real
  `threading.Thread`s, racing against genuinely separate database
  connections (not the single-connection savepoint the rest of the suite
  runs on — see the file's own docstring for why that distinction matters),
  all requesting the same match. Run explicitly under matching_v5:
  - `test_concurrent_requests_for_the_same_match_produce_exactly_one_row` —
    **passing**: exactly one row, exactly one `created=True`.
  - `test_a_losing_thread_returns_the_same_verdicts_as_the_winner` —
    **passing**: every losing thread reads back a complete, consistent row.
  - `test_a_second_race_after_the_match_exists_creates_nothing` —
    **passing**: once the row exists, every subsequent racing request is a
    read.

Reproducibility (`evaluate()` as a pure function of
`(JobCandidate, ProfileVersion, as_of)`) is unaffected by a version-string
change and continues to be covered by the existing engine test suite
(`tests/test_matching_engine.py`, `tests/test_matching_evaluation.py`), both
passing under matching_v5.

## 5. Persisted-result compatibility

- **No migration was added or needed.** `match_results.algorithm_version`
  is `String(32)` with only a not-blank check constraint
  (`ck_match_results_algorithm_version_not_blank`) — never an enum or a
  `CHECK ... IN (...)` list of known values. `"matching_v5"` (11 characters)
  fits without any DDL change. `alembic check` confirmed zero drift between
  the SQLAlchemy models and the database both before and after this
  release's changes.
- **`ALGORITHM_V1` through `ALGORITHM_V4` are still defined**, unedited, in
  `app/models/match.py`, exactly as the file's own comment requires:
  "Every version ever shipped stays here... deleting v1 when v2 arrived
  would leave every historical row pointing at a version nobody can look
  up." `ALGORITHM_V5` was added beside them, not in their place.
- **No existing `match_results`, `match_requirement_results`, or
  `match_evidence` row was read, written, or migrated by this release.**
  The only database interaction beyond the test suite's own fixtures was the
  Alembic verification cycle in §3, which touched unrelated, already-shipped
  columns (`tier`, `qualifies`, `note`, `quote`, `evidence_standard` — from
  a pre-existing, previously-unapplied migration on the local dev database)
  and left them at their final, upgraded state.
- **Verified directly**, not assumed: §4's new test proves a `matching_v4`
  row is both (a) invisible to a `matching_v5` lookup and (b) unmodified by
  one existing alongside it.

## 6. Exact files changed

Backend:

```
app/models/match.py                          + ALGORITHM_V5, historical comment
app/services/matching/engine.py              ALGORITHM_VERSION -> ALGORITHM_V5, + v4->v5 docstring paragraph
tests/test_matching_service.py               "matching_v4" -> "matching_v5" (1 assertion); + 1 new test
tests/test_matching_engine.py                ALGORITHM_V4 -> ALGORITHM_V5 (import + 1 assertion)
tests/test_matching_location.py              "matching_v4" -> "matching_v5" (1 assertion; the file's historical docstring line was left alone)
tests/test_matching_api.py                   "matching_v4" -> "matching_v5" (1 assertion)
tests/test_nondoing_benchmark_independence.py  + "matching_v5" added to the forbidden-terms list
evaluation/nondoing/groundtruth.py           DEFAULT_VERSION: non_doing_v1 -> non_doing_v2
evaluation/README.md                         non_doing_v1/v2 section rewritten: v2 active, v1 retired
evaluation/datasets/non_doing_v2/cases.json  new (139 cases: 137 from v1 unchanged, + pl-039, + pl-040)
evaluation/datasets/non_doing_v2/README.md   new (copied from v1, review.methodology explains v1->v2)
```

Frontend / shared contracts:

```
apps/web/src/test/match-fixtures.ts                    "matching_v4" -> "matching_v5"
apps/web/src/app/jobs/[id]/match/page.test.tsx          "Matching v4" -> "Matching v5" (rendered-text assertion)
packages/shared/openapi.json                            regenerated (no structural change)
packages/shared/src/api-schema.ts                       regenerated (no structural change)
```

Documentation:

```
docs/matching.md                    title + "currently" line -> matching_v5; + v4->v5 historical paragraph (v1/v2/v3/v4 paragraphs untouched)
docs/matching-v5-results.md         2 stale "still matching_v4" claims corrected, pointing at this document
docs/matching-v5-release.md         new — this document
```

Explicitly **not** changed:

```
evaluation/datasets/non_doing_v1/*           frozen historical baseline
evaluation/datasets/matching_real_v1/*       ground truth, per explicit instruction
evaluation/datasets/matching_v1/, matching_v2/   rule-level regression datasets
app/services/matching/vocabulary.py          NON_DOING_MARKERS, is_non_doing() unchanged
app/services/matching/evidence.py            demonstrates() unchanged (demonstrates_v5/demonstrates_title were additions made in the prior implementation step, not this one)
app/services/matching/span_split.py, negation.py   unchanged (the four V5 components, per this task's own instruction)
Any database migration                       none added; none needed
```

## 7. Remaining known limitations

Unchanged from `docs/matching-v5-results.md`'s own accounting — this release
step did not attempt to close any of them, per its scope:

- **`pl-012`, `pl-017`, `pl-035`** — the "familiar/curious + intensifier"
  vocabulary defect (a marker word followed by a strengthening phrase like
  "through hands-on work over N years" should stop counting as a caveat).
  Vocabulary curation work, not an architecture question; out of scope for
  matching_v5.
- **`pl-028`** — the accepted cost of the same-clause precedence safeguard
  (§3.3 of the implementation design): a later span's literal repeat of a
  token may not promote it past an earlier span's `mentions` verdict in the
  same clause, which is what keeps `pl-039` (§2) safe and is what makes this
  one case unfixable without reopening that regression.
- **`sd-031`** — the zero-delimiter, no-rescue residual: a marker and a
  technology share one unbroken clause with no punctuation or connector
  anywhere, and the marker's true grammatical target is a different,
  unrelated noun phrase. Needs real dependency information; explicitly
  rejected on cost/benefit grounds in the architecture analysis, not
  attempted here or in the implementation.
- **`sd-099`** — a marker word broken mid-word by a Unicode em/en dash (a
  PDF line-wrap artifact) is not recognised; a narrower instance of the
  already-fixed scikit-learn class of bug, for a separator that folds to a
  visible hyphen rather than being removed.
- **`pl-032`'s general "replacing" ambiguity** — this specific sentence
  passes (via reattachment's trailing "now streaming..." clause), but a
  differently-worded equivalent without that trailing clause would not; the
  underlying question — which side of a replacement a clause's own named
  technology is on — is a vocabulary/sense question no span mechanism
  resolves in general.
- **Component 5 (magnitude-bearing negation) is not built.**
  `pl-014, pl-030, pl-036` still read as `UNCERTAIN` where a careful human
  reviewer would read `MISSING` from an explicit, small, prose-stated
  duration ("three weeks," "no production work with it"). This needs a new
  `Evidence` field and a new branch in `rules._judge_technology_duration` — a
  different, larger, and explicitly out-of-scope change, named again here
  per this task's own instruction not to attempt it.
- **All 10 sentence-level `job_title` cases and all 10 `explicit_non_use`
  cases, and 9 of 10 minimal pairs' "negative" halves, are structurally
  unaffected by matching_v5 and always will be under any design that leaves
  `is_non_doing()` unmodified** — they call that function directly, one
  layer below where every matching_v5 component operates. This is the
  single most important scope boundary in the whole release: sentence-level
  `non_doing_v2` agreement (0.545) is not a partial result waiting on more
  work, it is a structural ceiling for any release that keeps
  `NON_DOING_MARKERS`/`is_non_doing()`/`demonstrates()` byte-for-byte
  unchanged, which every instruction across this whole project has required.
