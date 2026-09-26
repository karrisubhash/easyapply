"""Reading the dataset off disk. Knows nothing about any matcher.

Three files, kept apart on purpose:

    profiles.json       the candidates
    jobs.json           the postings and the requirements they state
    ground_truth.json   what a human decided about each requirement

The split is structural, not cosmetic. Ground truth lives in its own file, is
authored in its own pass, and is loaded by code that cannot reach a matching
rule — so "the expected answers came from the engine" is not a mistake anyone
can make here by accident. See `schema.py`'s docstring and
`tests/test_benchmark_independence.py`.

Everything is validated on load and a bad dataset raises. There is no lenient
mode: the only thing worse than a benchmark that will not run is one that runs
on a dataset missing the verdicts it claims to measure.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from evaluation.benchmark.schema import (
    Case,
    Dataset,
    DatasetError,
    ExpectedEvidence,
    GroundTruth,
    JobFixture,
    ProfileCertification,
    ProfileEducation,
    ProfileExperience,
    ProfileFixture,
    ProfileProject,
    ProfileSkill,
    Requirement,
    ReviewMetadata,
    validate,
)

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
DEFAULT_VERSION = "matching_real_v1"


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _read(directory: Path, name: str) -> Any:
    path = directory / name
    if not path.exists():
        raise DatasetError(f"{path} is missing; the dataset is incomplete")
    return json.loads(path.read_text(encoding="utf-8"))


def _profile(raw: dict[str, Any]) -> ProfileFixture:
    return ProfileFixture(
        id=raw["id"],
        label=raw["label"],
        summary=raw["summary"],
        skills=tuple(
            ProfileSkill(
                ref=item["ref"],
                name=item["name"],
                years=item.get("years"),
                proficiency=item.get("proficiency"),
            )
            for item in raw.get("skills", [])
        ),
        experiences=tuple(
            ProfileExperience(
                ref=item["ref"],
                company=item["company"],
                job_title=item["job_title"],
                start=_day(item["start"]),
                end=_day(item.get("end")),
                description=item.get("description"),
                employment_type=item.get("employment_type"),
                location=item.get("location"),
            )
            for item in raw.get("experiences", [])
        ),
        education=tuple(
            ProfileEducation(
                ref=item["ref"],
                institution=item["institution"],
                degree=item.get("degree"),
                field_of_study=item.get("field_of_study"),
            )
            for item in raw.get("education", [])
        ),
        certifications=tuple(
            ProfileCertification(
                ref=item["ref"],
                name=item["name"],
                issuer=item.get("issuer"),
                expires=_day(item.get("expires")),
            )
            for item in raw.get("certifications", [])
        ),
        projects=tuple(
            ProfileProject(
                ref=item["ref"],
                name=item["name"],
                technologies=tuple(item.get("technologies", [])),
                description=item.get("description"),
                start=_day(item.get("start")),
                end=_day(item.get("end")),
            )
            for item in raw.get("projects", [])
        ),
    )


def _job(raw: dict[str, Any]) -> JobFixture:
    return JobFixture(
        id=raw["id"],
        label=raw["label"],
        job_title=raw["job_title"],
        company_kind=raw["company_kind"],
        location=raw.get("location"),
        description=raw["description"],
        requirements=tuple(
            Requirement(
                id=item["id"],
                category=item["category"],
                label=item["label"],
                is_required=item["is_required"],
                aspect=item["aspect"],
                minimum_years=item.get("minimum_years"),
                degree_level=item.get("degree_level"),
                field_of_study=item.get("field_of_study"),
                source_quote=item.get("source_quote"),
            )
            for item in raw["requirements"]
        ),
    )


def _case(raw: dict[str, Any]) -> Case:
    return Case(
        id=raw["id"],
        profile_id=raw["profile_id"],
        job_id=raw["job_id"],
        as_of=_day(raw["as_of"]),
        note=raw["note"],
        ground_truth=tuple(
            GroundTruth(
                requirement_id=item["requirement_id"],
                verdict=item["verdict"],
                evidence=ExpectedEvidence(
                    source_type=item["evidence"]["source_type"],
                    refs=tuple(item["evidence"].get("refs", [])),
                ),
                rationale=item["rationale"],
                aspect=item.get("aspect"),
                contested=item.get("contested", False),
                contested_note=item.get("contested_note"),
            )
            for item in raw["ground_truth"]
        ),
    )


def load(version: str = DEFAULT_VERSION) -> Dataset:
    """Load and validate a benchmark dataset by its directory name."""
    directory = DATASETS / version
    if not directory.is_dir():
        raise DatasetError(f"no dataset named {version!r} under {DATASETS}")

    profiles_raw = _read(directory, "profiles.json")
    jobs_raw = _read(directory, "jobs.json")
    truth_raw = _read(directory, "ground_truth.json")

    if truth_raw["dataset_version"] != version:
        # A ground-truth file that names a different dataset has been copied
        # from somewhere, and the verdicts in it were reached about other data.
        raise DatasetError(
            f"ground_truth.json declares {truth_raw['dataset_version']!r} but lives in {version!r}"
        )

    review = truth_raw["review"]
    dataset = Dataset(
        version=version,
        review=ReviewMetadata(
            dataset_version=version,
            created=review["created"],
            reviewers=tuple(review["reviewers"]),
            methodology=review["methodology"],
            independence_statement=review["independence_statement"],
        ),
        profiles={item["id"]: _profile(item) for item in profiles_raw["profiles"]},
        jobs={item["id"]: _job(item) for item in jobs_raw["jobs"]},
        cases=tuple(_case(item) for item in truth_raw["cases"]),
    )

    validate(dataset)
    return dataset


def available() -> list[str]:
    return sorted(
        path.name
        for path in DATASETS.iterdir()
        if path.is_dir() and (path / "ground_truth.json").exists()
    )
