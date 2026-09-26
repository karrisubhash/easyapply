# declared_only_v1 — the declared-only evidence benchmark

A second real-data-shaped benchmark, built for one question that
`matching_real_v1` can only gesture at: **when should a claim the profile makes
and nothing else in the profile supports be allowed to settle a requirement?**

```
profiles.json       6 synthesised candidates, junior through senior
jobs.json           4 synthesised postings, 26 requirements between them
ground_truth.json   14 pairings, 94 verdicts with rationales
```

> Nothing in this benchmark estimates anyone's chance of being interviewed,
> hired, shortlisted or called back. It measures one thing: whether a matcher's
> verdict about a stated requirement agrees with a reviewer's, given the same
> two documents.

`matching_real_v1` contains **7** judgments whose operative evidence tier is
`declared_only`, and 6 of the 7 come from a single profile. Every conclusion
about declared-only handling drawn from it is therefore a conclusion about
`p_junior_stuffed`. This dataset exists to break that confound.

## What it varies, and why

The two things `matching_real_v1` cannot separate are **how much professional
experience the profile shows** and **whether anything at all sits next to the
claim**. In that dataset they move together: the only profile with bare
declarations is also the only profile with no career. Here they are crossed.

| profile | employment | the declared-only claims in it |
|---|---|---|
| `d_grad_bare` | 0.5y | five claims, nothing anywhere near any of them |
| `d_grad_project` | 1.0y | two claims corroborated by a three-year project that describes the work; one bare |
| `d_changer` | 8.0y total, 1.7y in software | one bare; one asserted twice (skill row and project technology) and described nowhere |
| `d_mid` | 6.5y | two claims that the surrounding work makes entirely plausible and never mentions |
| `d_senior_bare` | 12.0y | three claims in a profile whose three role descriptions are detailed and name none of them |
| `d_senior_years` | 10.2y | three claims, each carrying its own stated number of years |

`d_senior_bare` is the case the whole dataset is built around: on the specific
claim, it says exactly what a graduate's stuffed list says — the word, once, in
a field whose purpose is to hold it — while being a profile no reviewer would
call thin. Any policy that reads seniority as a proxy for credibility has to
answer for it.

The four postings vary the other axis: the bar the requirement sets. The same
Kubernetes claim is put against a bare required line, a bare preferred line, an
explicit production line, an "any exposure" line, and a three-year duration
line — so the posting's wording and the profile's evidence move independently.

Requirements are authored, not structured, for the same reason as in
`matching_real_v1`: a structuring mistake would otherwise arrive here looking
like a matching failure.

## How ground truth was established

Every verdict was written by reading the profile fixture and the posting as two
documents and deciding what a careful hiring reviewer would conclude, using the
verdict definitions and the thirteen reviewer rules fixed in
`../matching_real_v1/README.md`. The rationale was written at the same time as
the verdict, and the whole file was frozen before any matcher was executed
against this dataset:

```
sha256(ground_truth.json) = 0f96c6d6374ca781ff4372f67329ec9c83af4b3af4175edfa23abcb2786b1f04
frozen at                  2026-09-20T17:50:43Z, before the first run
```

The only property of the engine consulted while writing the fixtures was the
curated technology vocabulary, so that a case would measure declared-only policy
rather than vocabulary coverage. No verdict was read off, derived from, or
adjusted to any matcher output, and none has been edited since the freeze.

**Where the reviewer rules disagree with each other, the disagreement is
recorded rather than resolved.** Rule 1 counts stated years and project
technologies as corroboration; rule 6 says duration counts paid employment.
`c12/rq_da_scala5` is exactly the collision — six stated years of Scala, no paid
role that used it, against a five-year bar — and it is marked `contested` with
both readings written out.

### Contested judgments

**19 of 94** are flagged `contested`, against 9 of 143 in `matching_real_v1`.
The higher rate is a property of the question, not sloppiness: this dataset
deliberately concentrates on the cells where a claim's believability is the
whole judgment, and those are the cells where two readers most easily part
company. The runner reports agreement over the uncontested subset separately,
and that subset is the one to read first.

## Known limitations

- **The rater is not a human.** `matching_real_v1` was rated by an engineer on
  this system; this dataset was rated by the AI assistant that also performed
  the accompanying analysis. That is a weaker form of independence than a second
  human, and it is the dataset's single largest limitation. The verdicts here
  are a design input, not a release gate. Re-rating these 94 judgments by a
  human — especially the 19 contested ones — is the highest-value improvement
  available.
- **Six profiles, four postings, 94 judgments, 27 of them declared-only.**
  Enough to refute a policy; nowhere near enough to confirm one.
- **Synthetic, and written to be hard.** The declared-only density here is far
  above what real profiles show, by construction. Aggregate agreement on this
  dataset is not comparable to aggregate agreement on `matching_real_v1` and
  must never be quoted as though it were.
- **No second discipline.** All four postings are software engineering roles in
  the UK, inheriting `matching_real_v1`'s domain skew.
- **The engine is a co-author of the fixtures, weakly.** Technology names were
  chosen from the curated vocabulary. Nothing else about the engine was
  consulted, but that much was.

## Running it

```
python -m evaluation.benchmark.runner --dataset declared_only_v1
python -m evaluation.benchmark.runner --dataset declared_only_v1 --failures
```

Findings, the full trace from declaration to score, and the policy comparison
this dataset was built to support are in
`docs/matching-v6-declared-only-analysis.md`.

**Changing this file.** Same rule as `matching_real_v1`: a verdict may be
corrected only on the grounds that the reviewer misread the profile or the
posting, and the rationale must change with it. A verdict must never be edited
because an algorithm disagrees with it. Any change to an expectation bumps the
directory to `declared_only_v2`.
