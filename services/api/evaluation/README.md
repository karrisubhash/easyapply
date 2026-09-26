# Evaluation

Four datasets — resume structuring, job structuring, matching regression and
the real-data matching benchmark — each answering the same question before
anyone starts tuning anything: "did that change make it better or worse?" They
exist first on purpose: changing a rule or a prompt without a measurement is
how a change that helps a few cases and breaks a few others gets shipped
feeling like progress.

No runner here is a test. A test asserts a threshold and goes red; these report
a number and let a person decide whether it moved the right way. Turning one
into a pass/fail gate before anyone knows what a good score looks like would
just encode today's accident as tomorrow's requirement.

## Two kinds of measurement, and why both stay

The matching domain has two datasets and they answer different questions.
Deleting either would lose something the other cannot supply.

    datasets/matching_v2/        RULE-LEVEL REGRESSION
                                 Cases written FROM the matching rules. Asks:
                                 does the engine still do what it is
                                 specified to do? A perfect score means "no
                                 regression", never "good matching" — the cases
                                 and the code were derived from the same
                                 specification, so agreement is close to
                                 tautological. Cheap, exhaustive, and the thing
                                 that catches an accidental rule change.

    datasets/matching_real_v1/   INDEPENDENT BENCHMARK
                                 Human verdicts about realistic profiles and
                                 postings, authored without reference to any
                                 algorithm. Asks: does the matcher agree with a
                                 person? A poor score here is information, not
                                 a bug — and a perfect score would be evidence
                                 that the ground truth had been contaminated.

The second is run by `benchmark/`, whose independence from the matching code is
structural and is asserted in `tests/test_benchmark_independence.py`.

## A fifth dataset: one mechanism, not the whole matcher

    datasets/non_doing_v2/       NON_DOING_MARKERS BENCHMARK (active)
                                 Independent expectations about whether a
                                 clause says a technology was NOT used, and
                                 about the tier, attribution and duration
                                 consequences that follow from it. Narrower
                                 than matching_real_v1 on purpose: it isolates
                                 one mechanism (vocabulary.is_non_doing and
                                 what evidence.py/profile_index.py/rules.py do
                                 with its answer) and separates the five
                                 pipeline stages a real match passes through,
                                 so a disagreement can be pinned to the stage
                                 that produced it rather than only to a final
                                 verdict. What `evaluation.nondoing.runner`
                                 measures by default, and what matching_v5 and
                                 every later algorithm version are compared
                                 against.

    datasets/non_doing_v1/       NON_DOING_MARKERS BENCHMARK (retired)
                                 The dataset matching_v4 was measured against
                                 before the matching_v5 release. Frozen, on
                                 disk, exactly as it was —
                                 `datasets/non_doing_v1/BASELINE_REPORT.md` is
                                 that measurement and is not re-run. Superseded
                                 by v2, not deleted, for the same reason
                                 `datasets/matching_v1/` stays next to
                                 `datasets/matching_v2/`: a historical result
                                 has to keep pointing at the dataset it was
                                 actually measured against.

Run by `nondoing/`, independence asserted in
`tests/test_nondoing_benchmark_independence.py`. See
`datasets/non_doing_v2/README.md` and its `cases.json` `review.methodology`
for what changed between v1 and v2, and
`docs/matching-v5-results.md`/`docs/matching-v5-release.md` for the
matching_v5 measurement itself.

    python -m evaluation.nondoing.runner
    python -m evaluation.nondoing.runner --failures
    python -m evaluation.nondoing.runner --json
    python -m evaluation.nondoing.runner --dataset non_doing_v1   # the retired baseline

## The declared-only datasets: one mechanism, one of them with no answers yet

    datasets/declared_only_v1/   INDEPENDENT BENCHMARK (scored)
                                 94 judgments over 6 profiles and 4 postings,
                                 concentrated on one question matching_real_v1
                                 can only gesture at: what a claim is worth when
                                 the profile asserts it and nothing else in the
                                 profile touches it. Same shape, same runner and
                                 same rules as matching_real_v1, and NOT
                                 comparable to it in aggregate — declared-only
                                 rows are far denser here by construction, so
                                 its raw agreement is a different measurement
                                 and must never be quoted beside
                                 matching_real_v1's as though it were the same
                                 one. Findings: docs/matching-v6-declared-only-analysis.md.

    datasets/declared_only_v2/   FIXTURES ONLY — NO EXPECTATIONS
                                 Profiles, postings and an authored pairings.json
                                 saying which requirements are to be judged. It
                                 has NO ground_truth.json, deliberately: it
                                 covers the evidence shapes §G of the V6 analysis
                                 says are still missing, and its expectations are
                                 for a human reviewer to write. It therefore does
                                 not appear in `groundtruth.available()` and the
                                 benchmark runner cannot score it — validate it
                                 with the test suite instead (see below). A model
                                 may not author expectations for it.

The two feed one blind second-rating package,
`datasets/declared_only_v1/review_r2/`, which draws its rows from both and is
the only place the two are used together. Its README explains the blinding, the
strata and the pre-registered sufficiency criteria.

    python -m evaluation.benchmark.runner --dataset declared_only_v1
    python -m evaluation.benchmark.runner --dataset declared_only_v1 --failures
    python evaluation/datasets/declared_only_v1/review_r2/build_packet.py   # regenerate the packet
    python evaluation/datasets/declared_only_v1/review_r2/analyze.py        # after a human fills answers.json
    pytest tests/test_declared_only_benchmarks.py                           # both datasets, as CI runs them

## Resume structuring evaluation

### What is here

    datasets/v1/cases.json   the dataset, versioned by directory
    runner.py                runs a structurer over it and scores the result

The dataset is deliberately small. Its job is coverage of *shapes* — the ways a
resume can be awkward — not volume. A hundred conventional one-column resumes
would score well and tell us nothing.

### Running it

    python -m evaluation.runner                  # deterministic structurer
    python -m evaluation.runner --structurer ai  # the configured provider

The AI run needs RESUME_AI_ENABLED and a key, and it costs money. The
deterministic run is free and is what CI uses.

### Scoring

Per case, per section, against the fields a resume actually asserts:

  * recall    — of what the case says is there, how much was found
  * precision — of what was found, how much is really there
  * fabrication — values reported that the expectation does not contain, counted
    separately because a confident invention is worse than a miss. A miss leaves
    a user with a gap they can see; a fabrication leaves them with a fact they
    have no reason to doubt.

Dates carry their precision into the comparison, so "May 2024" read as
2024-05-01/day is wrong even though the date matches — inventing a day is
exactly what the precision column exists to prevent.

### Adding a case

Add an entry to `cases.json` with a `shape` nobody has covered yet, the text the
extractor would produce, and only the fields the resume genuinely states. Leave
out anything the resume does not say: a field in `expected` that the document
does not assert makes the model look wrong for being right.

Bump the directory to `v2` when an existing case's expectation changes, so a
score is always comparable to the one before it.

## Job structuring evaluation

### What is here

    datasets/jobs_v1/cases.json   the dataset, versioned by directory
    job_runner.py                 runs a structurer over it and scores the result

Fifteen postings, each covering a shape the domain spec calls out explicitly:
ordinary roles across a few disciplines, required-vs-preferred splits, explicit
and absent years, multiple experience requirements on one posting,
certifications, education, salary and location prose that must NOT leak into a
requirement category, ambiguous wording, a prompt-injection attempt, HTML
artifacts, and heading phrasing the deterministic structurer's fixed patterns
do not recognize. See `datasets/jobs_v1/cases.json` for the full list and the
`note` on each case explaining what it is checking for.

### Running it

    python -m evaluation.job_runner                  # deterministic structurer
    python -m evaluation.job_runner --structurer ai  # the configured provider

The AI run needs JOB_AI_ENABLED and a key, and it costs money. The
deterministic run is free and is what CI uses — and is also the baseline the
AI run is meant to be compared against, not a placeholder waiting to be
deleted; see `app/services/job/structuring.py`'s module docstring for why both
stay.

### Scoring

`JobCandidate` splits differently from `ResumeCandidate` — skills and
experience are already separate required/preferred lists at the schema level,
technologies/education/certifications/qualifications carry `is_required` per
item, and responsibilities carry no required/preferred axis at all.
`CATEGORY_CONFIG` in `job_runner.py` encodes that shape once. Four measures per
case:

  * recall / precision — the same meaning as the resume runner's, averaged
    across categories a case makes a claim about.
  * fabrication — items reported that the case's `expected` does not contain at
    all, in either bucket.
  * classification_accuracy — of items that WERE found (matched by identity,
    ignoring which bucket they landed in), what fraction landed in the bucket
    the posting's own wording says they should. Measured separately from
    recall/precision on purpose: "found the right technology but called it
    preferred instead of required" is a different failure from "invented a
    technology," and folding them together would hide which one a prompt
    change actually fixed.
  * provenance — of every citation the structurer produced across the whole
    case, what fraction `verify_job_provenance` could actually find in the
    case's own description text. A structurer that only ever quotes text it
    read should sit at or near 1.0.

### Adding a case

Add an entry to `cases.json` with a `shape` nobody has covered yet and only the
categories the posting genuinely makes a claim about — an omitted category is
simply not scored for that case. Use an explicit `{"required": [], "preferred":
[]}` when the point of the case is specifically that a category should stay
empty (a salary figure that must not become a "skill", an injected paragraph
that must not become a requirement) — see `job-with-salary` or
`prompt-injection` for the pattern.

Bump the directory to `jobs_v2` when an existing case's expectation changes.

## Matching evaluation

### What is here

    datasets/matching_v2/cases.json   the dataset, versioned by directory
    datasets/matching_v1/cases.json   the previous rules, kept interpretable
    match_runner.py                   runs the matcher over it and scores it

Different in kind from the two above. A structuring case asks "what did it read
out of this text?"; a matching case states, per requirement, **the verdict a
careful person would reach** from the profile alone — so scoring compares
verdicts rather than extracted text, and the unit of truth is a `matched` /
`missing` / `uncertain` label.

The expectations are written from the domain rules, not read off the engine.
Where the two disagree, the rules are what should change.

### Running it

    python -m evaluation.match_runner
    python -m evaluation.match_runner --json

Free and offline: matching_v1 is deterministic, with no model and no network.

### Scoring

Five measures, and the reason each is separate:

  * classification_accuracy — of the requirements a case pins, how many got the
    expected verdict. The headline, and on its own not enough.
  * false_matches — expected `missing` or `uncertain`, reported `matched`. The
    worst output the system can produce: unlike a miss it is invisible to the
    person reading it, who has no reason to check it. Counted and reported
    first for that reason.
  * false_missing — a real error, since it hides evidence the profile contains,
    but a visible one.
  * uncertainty errors, split into `overcautious` (doubt reported where the
    profile settles the question) and `overconfident` (a settled verdict where
    it does not). Folding them together would hide which way the engine leans.
  * score consistency and reproducibility — that the stored counts agree with
    the verdicts, the coverage agrees with the counts, and a second run of the
    same inputs produces the identical result.

### Adding a case

Add an entry to `cases.json` with a `shape` nobody has covered yet, a `note`
saying what the case is *for*, an `as_of` date (durations are measured against
it, so a case without one is unreproducible), and an `expected` entry per
requirement you want pinned. A requirement the case does not pin is simply not
scored, which is what lets a case be about one thing.

Write the expectation from what the profile actually states, never from what
the current rules happen to produce — a case that agrees with the code by
construction tests nothing.

Bump the directory when an existing case's expectation changes, alongside the
`ALGORITHM_VERSION` bump that made it change. `matching_v1` → `matching_v2`
happened for exactly one expectation: a technology demonstrated in a paid
role's description now matches a requirement that does not demand production
use, where the old rules called it uncertain because the skills list did not
repeat it. That change came from the real-data benchmark, where it was the
single largest class of disagreement with human judgment.

## Real-data matching benchmark

### What is here

    datasets/matching_real_v1/   the dataset and its methodology (see its README)
    benchmark/schema.py          vocabulary and shapes        (no matcher)
    benchmark/groundtruth.py     loading and validation       (no matcher)
    benchmark/metrics.py         scoring expected vs actual   (no matcher)
    benchmark/failures.py        naming what went wrong       (no matcher)
    benchmark/adapter.py         the single seam to a matcher
    benchmark/runner.py          orchestration and reporting

The modules marked "(no matcher)" may not import `app.services.matching`,
transitively or otherwise. That boundary is what makes the expected answers
independent evidence rather than a reflection of the thing being measured, and
it is enforced three ways: by walking the import graph, by scanning the dataset
for engine vocabulary, and — the proof that matters — by changing a matching
rule and asserting that the ground truth does not move while the score does.

### Running it

    python -m evaluation.benchmark.runner
    python -m evaluation.benchmark.runner --failures    # every disagreement in full
    python -m evaluation.benchmark.runner --json

Free and offline. Deterministic: the same dataset and the same algorithm
produce the same numbers, and the runner evaluates every case twice to say so.

### Reading the report

It leads with false matches and ends with raw agreement, deliberately. A single
agreement figure over mixed verdicts is the most misleading number this
benchmark could publish: a matcher answering UNCERTAIN to everything scores
respectably while being useless, and turning five real misses into confident
matches costs the same few points as the reverse — though the first is
dangerous and the second merely unhelpful. Read in this order:

  * **false matches** — said MATCHED where the profile does not satisfy it.
    Invisible to the person reading the result, because nothing invites them to
    check a green row.
  * **false missing** — hid evidence the profile contains. Real, but visible.
  * **per-verdict precision and recall** — which direction the errors run.
    Low UNCERTAIN precision means the matcher hedges on settled questions; low
    UNCERTAIN recall means it answers confidently where it should not.
  * **agreement by aspect** — where it struggles, by what the requirement was
    testing.
  * **evidence correctness** — an explainable matcher that cites the wrong item
    has not succeeded just because the verdict was right.
  * **raw agreement**, last and never alone.

### Adding a case

Add a profile to `profiles.json` or a posting to `jobs.json`, then a pairing to
`ground_truth.json` with one verdict per requirement the posting states — the
loader refuses a dataset that leaves any of them unjudged. Write the verdict
and the rationale from the documents, before running anything. Follow the
reviewer rules in the dataset's own README; if a case makes you want a new
rule, add the rule there first.

Bump the directory to `matching_real_v2` when an existing expectation changes,
so results from before and after are never compared as though they were the
same benchmark.

### Benchmarking a different algorithm

Implement `MatchingAlgorithm` in `benchmark/adapter.py` and register it in
`build()`. Nothing else changes — not the dataset, not the ground truth, not
the metrics, not the failure categories. That is the whole point of the
boundary: "missing alias" means the same thing about a semantic model as about
a lookup table, so the two are comparable.
