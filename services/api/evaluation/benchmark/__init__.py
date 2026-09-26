"""The real-data matching benchmark.

Measures a matcher against independent human judgment over realistic profiles
and realistic postings — as opposed to `evaluation/match_runner.py`, which
scores the deterministic rules against cases derived from those same rules.
Both exist and neither replaces the other:

    match_runner.py          rule-level regression. Does matching_v1 still do
                             what matching_v1 is specified to do? Cases are
                             written from the rules, so a perfect score means
                             "no regression", not "good matching".

    benchmark/               independent measurement. Do the verdicts agree
                             with what a person reading the same profile and
                             the same posting decided? Ground truth is authored
                             without reference to any algorithm, so a poor
                             score here is information rather than a bug.

    schema.py       vocabulary and shapes for the dataset       (no matcher)
    groundtruth.py  loading and validating the dataset          (no matcher)
    metrics.py      scoring expected against actual             (no matcher)
    failures.py     naming what went wrong                      (no matcher)
    adapter.py      the single seam where a matcher is run
    runner.py       orchestration and reporting

The import boundary is load-bearing and is asserted by
`tests/test_benchmark_independence.py`: everything marked "(no matcher)" must
stay free of `app.services.matching`, so that the expected answers can never be
derived from the thing being measured.
"""
