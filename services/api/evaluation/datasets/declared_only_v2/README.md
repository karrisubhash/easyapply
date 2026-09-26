# declared_only_v2 — fixtures, with no expectations

This dataset is **half a benchmark on purpose.** It holds five candidate
profiles, two postings and an authored list of which requirements are to be
judged. It holds no `ground_truth.json`, no verdict, no rationale and no
expected value of any kind.

```
profiles.json    5 synthesised candidates
jobs.json        2 synthesised postings, 13 requirements between them
pairings.json    7 pairings, 20 requirements selected for review
                 -- and NO ground_truth.json, deliberately
```

Because there is no ground truth, `groundtruth.available()` does not list it and
`python -m evaluation.benchmark.runner --dataset declared_only_v2` cannot score
it. `tests/test_declared_only_benchmarks.py` validates it instead, and one of
those tests exists specifically to fail if a `ground_truth.json` ever appears
here.

## Why it exists

`docs/matching-v6-declared-only-analysis.md` §G lists the evidence still
required before any V6 declared-only policy could be defended. `declared_only_v1`
covers none of items 2-6. Each pairing here exists for one of them:

| §G item | what was missing | the fixture that covers it |
|---|---|---|
| 2 | exposure-worded requirements at volume — the whole exposure option rested on **two** rows | `v2_j_tools`, whose every optional line is exposure-worded, plus two lines that put an exposure word next to a production ask |
| 4 | a project technology the project plainly did not use | `v2_project_contradiction` — Terraform and Redis declared twice each, and the project description says neither was used |
| 5 | a project description naming a technology the structured profile never declares | `v2_project_undeclared` — Postgres and Redis described as in use, declared nowhere |
| 6 | stated durations, including misleading ones | `v2_stated_durations` — twelve stated years of Kubernetes in a profile recording five years of employment altogether |
| — | the profile-side/posting-side exposure collision found in the M1 status report | `v2_prose_exposure` — the profile hedges with the same words a posting uses to lower its bar |
| — | a control for all of the above | `v2_exposure_corroborated` — exposure-worded asks meeting evidence nobody would dispute |

## Why the expectations are absent, and who may write them

`declared_only_v1` was rated by an AI assistant. That is its single largest
limitation, recorded in its own README and in the analysis. Repeating the
mistake here would produce more rows and no more evidence.

So the expectations are not merely unwritten — **a model may not write them.**
The rows go into the blind second-rater packet at
`../declared_only_v1/review_r2/` and are answered by a human reading the
documents. If expectations are ever authored from those answers, the commit that
adds them must record who rated them and when.

One consequence worth stating plainly, because it is easy to assume otherwise:
these 20 rows **cannot** help satisfy the pre-registered criterion C5, which
counts observations *both raters agree on*. They have one rater. They raise the
number of human-rated observations and they inform the policy counterfactual
directly; they do not raise the agreement count. Closing C5 needs a second human
on these rows, or an explicit, recorded decision to re-scope the criterion.

## Independence

The profiles and postings were written by hand, from the shape each was meant to
probe. No matcher was run to produce them, and no matcher output was consulted
while writing them. The one engine property consulted was the curated technology
vocabulary — the same disclosure `declared_only_v1` makes — so that a case would
test evidence shape rather than vocabulary coverage. `nginx` was dropped from an
early draft for exactly this reason: it is not in the vocabulary, so a row about
it would have measured the wrong thing.

Row selection is authored too. `pairings.json` names the requirements to judge,
chosen from what each fixture was built to test. This differs from
`declared_only_v1`, whose review rows are selected by asking the engine which
judgments came out at the `declared_only` tier — a selection criterion that is
sound but unavailable here, since these fixtures exist precisely to cover shapes
the tier model cannot currently express.

## Known limitations

- **Synthetic, and written to be hard.** Five profiles and two postings. The
  density of odd evidence shapes is far above anything real.
- **One discipline, one hiring culture.** UK software engineering, inheriting
  `matching_real_v1`'s skew.
- **Unbalanced by design.** Ten of the twenty rows are exposure-worded, because
  that was the largest gap. The dataset is not a sample of anything.
- **No expectations means no score.** Nothing here can tell you whether the
  matcher is right. It can only tell you what a human says when asked.
