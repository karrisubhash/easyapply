"""The one place the non_doing_v1 benchmark touches the matching code.

Every other module under `evaluation/nondoing/` describes the non-doing
vocabulary and its consequences as a problem, never as this implementation.
This file is the seam: it builds real `ProfileIndex` objects from a
`PipelineCase` and calls the real `is_non_doing`, `resolve_technology_subject`,
`merge_years` and `judge_*` functions to produce the actual values the report
compares against the dataset's authored expectations.

Imports are deferred into functions, exactly as
`evaluation/benchmark/adapter.py` defers them, so that importing the
`evaluation.nondoing` package — which `groundtruth.py` and `metrics.py` do —
never pulls the matching engine in. `tests/test_nondoing_benchmark_independence.py`
checks this is still true.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from evaluation.nondoing.metrics import ActualToken, PipelineObservation, SentenceObservation
from evaluation.nondoing.schema import PipelineCase, SentenceCase


def evaluate_sentence(case: SentenceCase) -> SentenceObservation:
    from app.services.matching.vocabulary import is_non_doing

    return SentenceObservation(case=case, actual_non_doing=is_non_doing(case.text))


@dataclass(frozen=True)
class _Built:
    profile: object  # ProfileIndex, typed loosely to keep this signature import-free
    version: object


def _build_profile(case: PipelineCase):
    from app.models import Experience, ProfileVersion, Skill
    from app.services.matching.profile_index import ProfileIndex

    skills = [
        Skill(
            id=index + 1,
            name=item.name,
            years_of_experience=Decimal(str(item.years)) if item.years is not None else None,
        )
        for index, item in enumerate(case.skills)
    ]
    experiences = [
        Experience(
            id=index + 1,
            company=f"Company {index + 1}",
            job_title=item.job_title,
            start_date=item.start,
            end_date=item.end,
            description=item.description,
        )
        for index, item in enumerate(case.experiences)
    ]
    version = ProfileVersion(id=1, profile_id=1, version_number=1, status="confirmed", created_by_type="user")
    version.skills = skills
    version.experiences = experiences
    version.education = []
    version.certifications = []
    version.projects = []
    return ProfileIndex(version)


def _resolve_tokens(case: PipelineCase, profile) -> dict[str, ActualToken]:
    from app.services.matching.normalization import canonical

    tokens: dict[str, ActualToken] = {}
    for expectation in case.expected.tokens:
        token = canonical(expectation.token) or expectation.token
        evidence = profile.evidence_for(token)
        tokens[expectation.token] = ActualToken(
            attributed_employment=bool(evidence.employment),
            tier=evidence.tier or "none",
        )
    return tokens


def _resolve_subject(case: PipelineCase, profile) -> str | None:
    from app.services.matching.rules import resolve_technology_subject

    if case.requirement.kind != "duration":
        return None
    return resolve_technology_subject(case.requirement.name, profile)


def _measure_duration(case: PipelineCase, profile, subject: str | None) -> tuple[float | None, bool]:
    """Independently re-derive the years the duration rules would compute for
    `subject`, from the same public building blocks `rules._judge_technology_duration`
    uses, so the report can show stage 4 (duration) even on cases whose final
    verdict (stage 5) took a different path (e.g. an UNCERTAIN from a mention
    with no dated employment) — see `rules.py`'s four-path docstring.
    """
    from app.services.matching.rules import merge_years

    if subject is None:
        return None, True

    employment = profile.employment_mentioning(subject)
    if not employment:
        return None, True

    spans = []
    for item in employment:
        if item.start_date is None:
            continue
        end = item.end_date or case.as_of
        if end < item.start_date:
            continue
        spans.append((item.start_date, end))
    if not spans:
        return None, True

    # merge_years wants objects with .start/.end; build the same tiny shape
    # `rules._Span` provides rather than importing the private dataclass.
    class _Span:
        __slots__ = ("start", "end")

        def __init__(self, start, end):
            self.start = start
            self.end = end

    return merge_years([_Span(start, end) for start, end in spans]), False


def _judge(case: PipelineCase, profile) -> str:
    from app.services.matching.rules import judge_experience, judge_token_requirement
    from app.models.match import CATEGORY_SKILL

    if case.requirement.kind == "duration":
        judgment = judge_experience(
            description=case.requirement.name,
            minimum_years=case.requirement.minimum_years,
            index=0,
            is_required=True,
            profile=profile,
            as_of=case.as_of,
            quotes=case.requirement.quotes,
        )
    else:
        judgment = judge_token_requirement(
            name=case.requirement.name,
            category=CATEGORY_SKILL,
            index=0,
            is_required=True,
            profile=profile,
            quotes=case.requirement.quotes,
        )
    return judgment.status


def evaluate_pipeline(case: PipelineCase) -> PipelineObservation:
    profile = _build_profile(case)
    tokens = _resolve_tokens(case, profile)
    subject = _resolve_subject(case, profile)
    years, uncertain = _measure_duration(case, profile, subject)
    verdict = _judge(case, profile)

    return PipelineObservation(
        case=case,
        actual_tokens=tokens,
        actual_resolved_subject=subject,
        actual_duration_years=years,
        actual_duration_uncertain=uncertain,
        actual_verdict=verdict,
    )


def algorithm_version() -> str:
    from app.services.matching.engine import ALGORITHM_VERSION

    return ALGORITHM_VERSION


__all__ = ["algorithm_version", "evaluate_pipeline", "evaluate_sentence"]
