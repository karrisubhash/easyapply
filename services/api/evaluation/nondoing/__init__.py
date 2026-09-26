"""The non_doing_v1 benchmark: an independent check on the non-doing marker
machinery in `app.services.matching.vocabulary` and the tiering, attribution
and duration rules that read its verdict.

See `evaluation/README.md` for how this fits alongside the resume, job and
real-data matching evaluations, and `evaluation/datasets/non_doing_v1/README.md`
for how the dataset was authored.

`schema.py`, `groundtruth.py` and `metrics.py` must never import
`app.services.matching` — see `tests/test_nondoing_benchmark_independence.py`.
`adapter.py` is the single seam.
"""
