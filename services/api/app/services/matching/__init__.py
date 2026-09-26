"""Deterministic matching: what a confirmed profile version satisfies in a job.

    normalization.py   two spellings of one thing become one token — and
                       nothing else becomes anything else
    profile_index.py   what a profile version claims, sorted into structured
                       evidence and prose mentions
    rules.py           the per-category verdicts, each one a rule a person can
                       apply by hand
    scoring.py         counts and coverage, derived from verdicts that already
                       exist
    engine.py          the traversal: a pure function of (requirements,
                       version, as_of)
    service.py         resolution, authorization, idempotency, persistence

No model, no embeddings, no similarity search. V1 exists to be a baseline whose
mistakes are explainable, so that anything semantic added later has something
honest to be compared against.
"""
