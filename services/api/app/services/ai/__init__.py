"""Vendor-facing AI infrastructure shared across domains.

Nothing in this package knows what a resume or a job posting is. It knows how
to ask a model for a Pydantic-shaped answer and how to translate that vendor's
failures into a vocabulary the rest of the application can reason about. Each
domain (`app/services/resume`, `app/services/job`) owns its own prompt, its own
schema, and its own settings namespace, and binds them to this package through
a thin adapter — see `app/services/resume/providers/anthropic_provider.py` and
`app/services/job/providers/anthropic_provider.py` for the pattern.
"""
