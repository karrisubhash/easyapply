"""Loading the non_doing_v1 dataset. Knows nothing about any matcher.

Mirrors `evaluation/benchmark/groundtruth.py`: a thin, literal translation from
JSON to the dataclasses in `schema.py`, with `schema.validate` run before
anything downstream can see the result.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from evaluation.nondoing.schema import (
    Dataset,
    DurationExpectation,
    PipelineCase,
    PipelineExperience,
    PipelineExpected,
    PipelineRequirement,
    PipelineSkill,
    ReviewMetadata,
    SentenceCase,
    TokenExpectation,
    validate,
)

DATASETS = Path(__file__).resolve().parent.parent / "datasets"
#: non_doing_v1 was retired for the matching_v5 release (see
#: evaluation/datasets/non_doing_v2/cases.json's review.methodology and
#: docs/matching-v5-results.md) and stays on disk, frozen, as the historical
#: baseline matching_v4 was measured against
#: (evaluation/datasets/non_doing_v1/BASELINE_REPORT.md). non_doing_v2 is
#: what matching_v5 and every later algorithm version are measured against.
DEFAULT_VERSION = "non_doing_v2"


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _sentence_case(payload: dict) -> SentenceCase:
    return SentenceCase(
        id=payload["id"],
        category=payload["category"],
        markers=tuple(payload["markers"]),
        construction=payload["construction"],
        text=payload["text"],
        expected_non_doing=bool(payload["expected_non_doing"]),
        rationale=payload["rationale"],
        pair_id=payload.get("pair_id"),
        defect_class=payload.get("defect_class", "none"),
    )


def _pipeline_case(payload: dict) -> PipelineCase:
    requirement = payload["requirement"]
    expected = payload["expected"]
    duration = expected["duration"]
    return PipelineCase(
        id=payload["id"],
        category=payload["category"],
        markers=tuple(payload["markers"]),
        note=payload["note"],
        skills=tuple(
            PipelineSkill(name=item["name"], years=item.get("years"))
            for item in payload.get("skills", [])
        ),
        experiences=tuple(
            PipelineExperience(
                job_title=item["job_title"],
                start=_date(item["start"]),
                end=_date(item["end"]) if item.get("end") else None,
                description=item["description"],
            )
            for item in payload["experiences"]
        ),
        requirement=PipelineRequirement(
            kind=requirement["kind"],
            name=requirement["name"],
            minimum_years=requirement.get("minimum_years"),
            quotes=tuple(requirement.get("quotes", ())),
        ),
        as_of=_date(payload["as_of"]),
        expected=PipelineExpected(
            tokens=tuple(
                TokenExpectation(
                    token=item["token"],
                    attributed_employment=bool(item["attributed_employment"]),
                    tier=item["tier"],
                )
                for item in expected["tokens"]
            ),
            resolved_subject=expected.get("resolved_subject"),
            duration=DurationExpectation(
                measurable=bool(duration["measurable"]), years=duration.get("years")
            ),
            verdict=expected["verdict"],
        ),
        pair_id=payload.get("pair_id"),
        defect_class=payload.get("defect_class", "none"),
    )


def load(version: str = DEFAULT_VERSION) -> Dataset:
    directory = DATASETS / version
    payload = json.loads((directory / "cases.json").read_text(encoding="utf-8"))

    review = ReviewMetadata(
        created=payload["review"]["created"],
        reviewer=payload["review"]["reviewer"],
        methodology=payload["review"]["methodology"],
        independence_statement=payload["review"]["independence_statement"],
    )

    dataset = Dataset(
        version=payload["version"],
        review=review,
        sentence_cases=tuple(_sentence_case(item) for item in payload["sentence_cases"]),
        pipeline_cases=tuple(_pipeline_case(item) for item in payload["pipeline_cases"]),
    )
    validate(dataset)
    return dataset


__all__ = ["DATASETS", "DEFAULT_VERSION", "load"]
