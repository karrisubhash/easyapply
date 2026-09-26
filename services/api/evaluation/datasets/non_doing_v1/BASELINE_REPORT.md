# non_doing_v1 baseline report

Run against `matching_v4` (the algorithm version at the time of this run), 137
cases (99 sentence-level, 38 pipeline-level), authored independently of the
implementation. See `README.md` for methodology and the independence
statement, `cases.json`'s `review` block, and
`tests/test_nondoing_benchmark_independence.py` for the proof that this
dataset cannot have been derived from the code it measures.

No production code was modified to produce this report. It is a baseline
measurement, not a set of instructions — see "What this report does not do"
at the end.

## Headline

|  | agreement |
|---|---|
| sentence-level (`is_non_doing`) | 54/99 = 0.545 |
| pipeline-level (all 5 stages) | 18/38 = 0.474 |
| all cases | 72/137 = 0.526 |

Raw agreement is reported last on purpose everywhere else in this codebase's
evaluation suite, and the same reasoning applies here: it is not the number to
read first. What it hides is *which* 46% is wrong and whether that is a
handful of narrow gaps or a structural problem. It is a structural problem in
two specific, well-bounded ways — see "Defect classification" below, where
`clause_context` and `vocabulary` alone account for 54 of the 65 predicted
(and confirmed) defects.

## Safety: sentence-level `is_non_doing` errors

| | count |
|---|---|
| false positives (a clause that demonstrates the work is called non-doing; evidence is silently discarded) | 34 |
| false negatives (a genuine disclaimer is read as a demonstration) | 11 |

**False positives are the majority error and the costlier direction.** A
false positive here does not fail loudly — it quietly downgrades a
candidate's strongest evidence (an `employment_described` or
`declared_corroborated` role) to `mentioned`, which is exactly the failure
mode `evidence.py`'s own docstring says the tiering exists to avoid. Every one
of the 34 traces to one of two root causes, both below.

## Per-category accuracy

```
completed_work_with_marker        0.000   (0/6)
explicit_non_use                  0.000   (0/10)
proper_name_collision             0.000   (0/7)
technology_attribution            0.250   (2/8)
duration_consequence              0.333   (2/6)
noun_collision                    0.368   (7/19)
evidence_tier_consequence         0.500   (7/14)
job_title                         0.500   (5/10)
research_verb                     0.600   (3/5)
composite                         0.667   (2/3)
job_title_path                    0.667   (4/6)
lifecycle_verb                    0.667   (4/6)
genuine_non_doing                 0.947   (18/19)
away_construction                 1.000   (8/8)
away_construction_pipeline        1.000   (1/1)
learning_phrase                   1.000   (9/9)
```

Read this table alongside the defect classification below, not instead of
it: the three categories at 0.000 (`completed_work_with_marker`,
`explicit_non_use`, `proper_name_collision`) score zero **by design** — every
case in them was authored as a predicted defect, and every prediction was
confirmed. A 0.000 here is not a surprise; it is 100% prediction accuracy on
a deliberately adversarial slice. `genuine_non_doing`, `away_construction`
and `learning_phrase` sit near 1.000 because those categories are mostly
positive constructions the marker list was built for, and it handles them
correctly.

## Per-marker accuracy

```
deprecated       0.000 (0/2)      exposure      0.167 (1/6)     retired    0.167 (1/6)
intend           0.200 (1/5)      curious       0.333 (2/6)     investigated 0.333 (1/3)
learn            0.333 (1/3)      planning      0.375 (3/8)     interested 0.400 (2/5)
studying         0.400 (2/5)      familiar      0.429 (3/7)     abandoned  0.500 (2/4)
considered       0.500 (2/4)      familiarity   0.500 (1/2)     keen       0.500 (2/4)
piloted          0.500 (2/4)      sunset        0.500 (1/2)     upcoming   0.500 (1/2)
learning         0.600 (6/10)     proposed      0.600 (3/5)     shelved    0.600 (3/5)
considering      0.667 (2/3)      decommissioned 0.667 (2/3)    evaluated  0.667 (2/3)
explored         0.667 (4/6)      migrating away 0.667 (2/3)    prototyped 0.667 (2/3)
rejected         0.750 (3/4)      replacing     0.750 (3/4)
canceled/cancelled/evaluation/intends/investigating/moved away/moving away/
planned/researched/retiring/trialed/trialled                    1.000
```

The words that score worst (`deprecated`, `exposure`, `retired`, `intend`,
`curious`) are exactly the words this benchmark specifically targeted for
vocabulary-sense collisions and proper-name collisions (§ below) — low
per-marker accuracy here is the predicted outcome, not new information. The
words at 1.000 are markers whose word-sense essentially never collides with
anything else in ordinary usage (`cancelled`, `researched`, `retiring`) —
exactly the profile you would want a curated, small marker list to have.

## Tier changes (stage 3: evidence tier)

17 of 38 pipeline cases end at the wrong tier:

```
pl-001  go          employment_described -> mentioned
pl-002  postgresql  employment_described -> mentioned
pl-003  react       employment_described -> mentioned
pl-004  angular     employment_described -> mentioned
pl-005  grpc        employment_described -> mentioned
pl-006  aws         employment_described -> mentioned
pl-009  airflow     employment_described -> mentioned
pl-011  kafka       employment_described -> mentioned
pl-012  snowflake   employment_described -> mentioned
pl-017  terraform   declared_corroborated -> declared_only
pl-019  kubernetes  employment_described -> mentioned
pl-023  kubernetes  employment_described -> mentioned
pl-026  java        employment_described -> mentioned
pl-028  graphql     employment_described -> mentioned
pl-029  rust        mentioned -> employment_described   [the one surprise; see below]
pl-032  kafka       employment_described -> mentioned
pl-035  tableau     declared_corroborated -> declared_only
```

16 of these 17 discard real evidence — a visible cost (the verdict typically
falls to UNCERTAIN rather than staying MATCHED, so a reviewer sees a gap to
check) rather than the invisible one. Exactly one, `pl-029`, moves the other
way and manufactures a tier that was not earned — the direction `evidence.py`'s
own module docstring identifies as the worst thing this system can produce,
because nothing invites a reviewer to double-check a confident MATCHED. It
was not predicted; see "The one surprise" below.

## Duration changes (stage 4: experience duration)

11 cases where the computed duration disagrees with what a human can read
out of the same profile:

```
pl-001  expected 7y     -> uncertain/none      pl-002  expected 8y   -> uncertain/none
pl-005  expected 9y     -> uncertain/none      pl-006  expected 5y   -> uncertain/none
pl-009  expected 6.6y   -> uncertain/none      pl-011  expected 4.6y -> uncertain/none
pl-012  expected 3y     -> uncertain/none      pl-023  expected 6y   -> uncertain/none
pl-026  expected 15y    -> uncertain/none      pl-032  expected 7y   -> uncertain/none
pl-029  expected uncertain/none -> 4y   [the surprise, wrong direction]
```

Every one of these ten "real years disappear" cases is a direct, mechanical
consequence of the stage-3 tier changes above: `rules.employment_mentioning()`
only returns experience rows filed under a token's `employment` bucket, so
the moment stage 1-2 files a role's only relevant clause under `mentions`
instead, `_judge_technology_duration` has literally nothing dated to sum,
independent of how many real years the role covers. This is the clearest
demonstration in the dataset that stage 1 and stage 5 are tightly coupled:
fixing stage 1's clause-scoping would very likely fix all ten of these without
touching `rules.py` or `evidence.py` at all.

## Minimal-pair failures

9 of 10 authored pairs failed — in every failing pair, the "positive"
(genuinely non-doing) half was read correctly and the "negative" (marker word
present, but resolved into affirmative action in the same clause) half was
not:

```
mp-eval-01   sd-001 OK / sd-069 FAIL   (evaluating ... adopted ... production)
mp-eval-02   sd-003 OK / sd-070 FAIL   (considering ... shipped ... production)
mp-eval-04   sd-004 OK / sd-067 FAIL   (investigated: eval-sense vs incident-sense)
mp-eval-05   sd-005 OK / sd-068 FAIL   (exploring: adoption-sense vs leisure-sense)
mp-lifecycle-01  sd-019 OK / sd-020 FAIL  (decommissioning planned vs. already done)
mp-planning-01   sd-023 OK / sd-024 FAIL  (planning underway vs. "sprint planning")
mp-keen-01       sd-042 OK / sd-049 FAIL  (keen to learn vs. "keen eye" idiom)
mp-exposure-01   sd-043 OK / sd-050 FAIL  (workshop exposure vs. security "exposure")
mp-familiar-01   sd-044 OK / sd-051 FAIL  (docs-only familiarity vs. "familiar patterns")
```

The one pair that fully passed, `mp-eval-03` (researched vs. plain "research"
the noun), is not in this list — it is the pair that shows the word-boundary
matching itself is sound; the failures above are about *sense*, not spelling.

## Technology-attribution failures (two technologies, one clause)

The dedicated `technology_attribution` category holds 8 cases: 6 fail
(`pl-001`-`pl-006`), and 2 pass as controls — `pl-007` (two clauses, properly
punctuated, by design) and `pl-034` (also two technologies and one shared
idea, but a semicolon happens to split it into two clauses; see below). The
list the runner prints under "technology-attribution failures" adds a 7th
case, `pl-032`, tagged `composite` rather than `technology_attribution`
because it involves two separate roles rather than one clause, but it fails
for the identical reason. The failure is identical every time: when two
technology names share one undivided clause and a marker word anywhere in
that clause fires, **both** technologies are demoted, including the one the
marker does not grammatically apply to.

```
pl-001  "Deprecated the legacy PHP monolith while building its replacement in
         Go, which now serves all production traffic."
         -> go: employment_described -> mentioned (should be untouched)
pl-002  MongoDB investigated / PostgreSQL kept        -> postgresql demoted
pl-003  Vue explored / React standardized on          -> react demoted
pl-004  Svelte piloted / Angular maintained 6 years    -> angular demoted
pl-005  GraphQL prototyped / gRPC in production since 2017 -> grpc demoted
pl-006  Heroku migrated away from / AWS hosting 5 years -> aws demoted
pl-032  Kafka "considered+shelved" role, then a SEPARATE role that adopted
         Kafka and describes it as "replacing" the old batch jobs
         -> kafka demoted even in the role where it is the technology being
            adopted, not the one being replaced
```

`pl-032` is the most informative case in the whole set: it shows the
attribution defect is not really about "two technologies in one sentence" —
it is about **clause boundaries**, full stop. The 7th failing case's two
technologies are in *different roles entirely*, and the misattribution still
happens, because `replacing` fires on the clause describing the new,
adopted technology (Kafka) just as readily as it would on the clause
describing something being phased out. `pl-034` (Elixir/Rails, "Abandoned a
plan to rewrite ... in Elixir; the existing Rails service ...") is the
control that proves the boundary: it is structurally the same
two-technologies-one-idea shape as the failures above, but because the
sentence happens to contain a semicolon, `vocabulary.sentences()` splits it
into two clauses and Rails is correctly, cleanly attributed. **The defect is
clause-scoped, not sentence-scoped or proximity-scoped** — a comma or a
subordinate clause inside one un-split sentence is enough to leak the marker
across two unrelated technologies; a semicolon, period or line break is
enough to stop it.

## Title-path failures

2 of 6 `job_title_path` cases failed, and 4 passed as controls confirming the
mechanism works correctly in the common case:

- **Failed**, `pl-023`: a technology named *only* in the job title (never
  repeated in the description) is demoted by a marker word sharing the title
  clause, with no other clause anywhere in the role to rescue it.
- **Failed**, `pl-026`: same shape, with `retired` (personal-status sense)
  instead of `planning`.
- **Passed as controls**: `pl-024` (a marker in the title with no technology
  named in that same clause is harmless — the description's own clean clause
  is unaffected); `pl-025` and `pl-038` (a technology named in the title is
  correctly protected by `MARKER_BEARING_NAMES`, or correctly rescued by a
  clean description clause, exactly as an ordinary role's prose would be);
  `pl-027` (a clean, marker-free baseline).

The title clause is not a special, more-trusted source and is not a
weaker one either — it goes through exactly the same clause-level
`is_non_doing` check as any other sentence in `profile_index._index_employment`,
with the same "rescued by a clean clause elsewhere" resilience and the same
"nothing else to rescue it" fragility. The defect only shows up when the
title is the *only* place the technology is named at all, which is a real
and fairly common resume/profile shape (a role titled after the project it
delivered, described only in generic terms).

## `*-away` construction failures

**None.** All 8 sentence-level and both pipeline-level `*-away` cases
(`migrating away`, `moved away`, `moving away`, `replacing`) resolved
correctly, including the four negative controls checking that "living away
from the office," "based away from headquarters" and "worked away
diligently" do *not* false-fire on the multi-word substring match, and
including the minimal pair distinguishing in-progress `replacing` from
completed, untagged `replaced`. This is the one required-coverage area with
zero findings, and it says something specific: the multi-word marker
matching in `vocabulary.py` (whitespace-normalized literal substring match)
is sound. Where `replacing` failed, it failed for the same clause-context
reason every other marker did (`pl-032`), not for a matching-mechanics reason.

## Defect classification

Predicted *before* the run (see each case's `defect_class` and rationale in
`cases.json`), then checked against what actually happened:

```
                    confirmed   resilient (predicted a defect,     surprises
                                 got correct behaviour instead)
attribution              6              1                              -
clause_context          17              0                              -
evidence_model           3              0                              -
tokenizer                 1              0                              -
vocabulary               37              0                              -
none (controls)           -              -                              1
```

65 cases were tagged with a specific predicted defect; 64 of those
predictions were confirmed and exactly 1 (`pl-034`, discussed above) turned
out more resilient than expected. Of the 72 cases tagged as controls
(`defect_class: none`), 71 behaved as predicted and 1 was a genuine surprise.
**Prediction accuracy — whether `defect_class` correctly anticipated
agreement or disagreement, which is a stricter and more informative number
than raw agreement — is 135/137 (98.5%).** This dataset was not calibrated by
peeking at the implementation; the fact that its predictions were confirmed
at this rate after the fact is evidence the underlying mechanism is well
understood, not evidence of circularity (see the independence tests for the
structural proof that circularity is impossible here regardless).

The five kinds, and what each one actually is in this codebase:

- **tokenizer** (1 case, `sd-099`): word-splitting/hyphenation mechanics
  misread a boundary. The one genuine finding here is new: a marker word
  broken mid-word by a Unicode em/en dash (as a PDF line-wrap can produce)
  folds to a literal hyphen and is then treated as an unrecognized hyphenated
  compound, splitting into pieces that don't match the marker. This is a
  narrower case of the *already-fixed* scikit-learn class of bug — the fix in
  `_SEPARATOR_FOLD` handles a dash landing *between* two whole words
  (`scikit`-`learn`) and a soft hyphen landing *inside* one word
  (`eval`-`uated`, removed and rejoined); it does not handle a Pd dash
  (em/en/figure dash etc.) landing inside one word, because those fold to a
  visible hyphen rather than being removed.
- **vocabulary** (37 cases, the largest bucket): the word is found correctly,
  but its membership in `NON_DOING_MARKERS` (or absence from
  `MARKER_BEARING_NAMES`) is wrong for the sentence at hand. This splits
  further into: (a) genuine proper-name collisions the curated list has never
  seen (`Planning Center`, `Keen`, `Intend Health Strategies`, `Curious.com`,
  `Sunset Learning`, `Exposure`, `Curious Learning` — 7 for 7, the exact
  shape of bug `MARKER_BEARING_NAMES` already exists to fix for `scikit
  learn`/`machine learning`, just not extended to these); (b) ordinary-word
  senses that don't relate to technology adoption at all (`keen eye`,
  `exposure` as security risk, `familiar` as "well-known," `interested
  stakeholders`, `retired` as personal/military status); (c) markers whose
  own family is arguably too broad for a sub-sense (`deprecated` and
  `decommissioned` applied to a system the author plainly operated for years
  before retiring it — deprecating something is not evidence it was never
  used, it is close to the opposite); and (d) the mirror problem, **coverage
  gaps**: 10 `explicit_non_use` cases (plus `sd-099`'s neighbor `pl-029`,
  see below) show plain-English non-use statements ("never used," "has not
  worked with," "zero production hours") that use none of the 43 listed
  words at all, and are read as demonstrating the work.
- **clause_context** (17 cases, second largest): the word's sense is
  completely correct, but `is_non_doing` grades a whole clause at once and a
  single unsplit sentence can contain both a caveat and its own resolution
  ("after evaluating X, adopted X, in production for 2 years"), or a caveat
  aimed at one noun phrase while a technology sits in an unrelated part of
  the same sentence ("kept the onboarding docs familiar... joining the
  Kubernetes team, which has run production for five years"). This is the
  single largest identifiable root cause by case count.
- **attribution** (7 cases): the specific, high-value instance of
  clause_context where the sentence names two *different* technologies and
  the marker relevant to one silently reaches the other. Kept as its own
  bucket because it is the shape the task called out explicitly, and because
  its fix (if any) is more likely to be about which token a clause's verdict
  is attached to than about the clause split itself.
- **evidence_model** (3 cases): `is_non_doing` and the clause split are both
  *already correct* (tier lands on `mentioned` exactly as authored), and the
  disagreement is entirely at stage 5. A mention that explicitly states a
  short, clearly-insufficient duration in its own prose ("three weeks," "two
  weeks," "no production work with it") is read by a person as settling a
  duration requirement in the negative (MISSING), but `TokenEvidence` has no
  slot for a duration extracted from unstructured mention text — only
  `employment` spans (dated) and `declarations` (a `Skill.years_of_experience`
  field) carry a number, so the tier-based duration rule can only say
  UNCERTAIN. This is a genuine model-shape gap, not a wrong regex anywhere.

### The one surprise

`pl-029` was authored as a control (`defect_class: none`): "Intend to
introduce Rust for performance-critical services once the team completes
onboarding; **no Rust code has shipped yet**." The semicolon splits this into
two clauses. The first correctly fires on `intend` and files Rust as a
mention. The second, "no Rust code has shipped yet," contains **no marker
word at all** — and reads, correctly per `is_non_doing`'s own rules, as an
affirmative clause, filing Rust as `employment` in the very same role. The
`employment` bucket wins the tier check, so this profile ends up
`employment_described`/`matched` against a 2-year Rust requirement, from a
sentence whose entire content is an explicit denial that any Rust code
exists. This was not predicted; it was found by the benchmark. It is the
sharpest illustration in the whole dataset of the coverage-gap half of the
vocabulary bucket: a plain-English non-use statement doesn't just fail to
weaken a claim (the `explicit_non_use` cases above already show that) — when
it lands in its own clause next to a real technology mention, it can
actively manufacture a false `MATCHED`, which is precisely the class of
error `evidence.py`'s own module docstring identifies as the worst thing
this system can produce.

## Independence and reproducibility

- `tests/test_nondoing_benchmark_independence.py`: 14 tests, all passing —
  structural (schema/groundtruth/metrics cannot import
  `app.services.matching`), textual (no dataset case mentions engine
  internals outside the review block), and behavioural (adding a word to the
  live marker set changes the measured score while the dataset file's SHA-256
  and every loaded expected value are byte-identical before and after).
- `tests/test_nondoing_benchmark.py`: 30 tests, all passing — dataset shape,
  coverage of every one of the 43 markers and all required categories,
  rejection of malformed cases, metrics arithmetic, and report rendering.
- The full existing suite (`pytest`, entire `tests/` directory) passes
  unchanged: 100% of prior tests still pass, confirming nothing here touched
  production code.

## What this report does not do

Per the task, this is a baseline measurement only. It does not propose a
change to `NON_DOING_MARKERS`, `is_non_doing()`, `demonstrates()`, or the
matching algorithm version, and none of those were modified to produce it.
The findings above are organized so that a future fix can be scoped
precisely — clause-splitting (17+7 cases), vocabulary curation (37 cases,
further split into proper-name additions, sense-collision removals, and
coverage-gap additions), and the evidence-model duration gap (3 cases) are
three separable pieces of work with three different blast radii — but
deciding which to do, in what order, is a separate decision this report
leaves open.
