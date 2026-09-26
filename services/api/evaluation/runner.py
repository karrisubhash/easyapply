"""Running a structurer over the evaluation dataset and scoring what it read.

Deliberately not a test. A test asserts a threshold and goes red; this reports a
number and lets a person decide whether the number moved the right way. Turning
it into a pass/fail gate before anyone knows what a good score looks like would
just encode today's accident as tomorrow's requirement.

Three measures per section, and the third is the one that matters most:

  recall       of what the resume states, how much was found
  precision    of what was found, how much the resume actually states
  fabrication  values reported that the resume does not contain

Fabrication is counted separately rather than folded into precision because the
two failures are not equally bad. A missed job leaves a gap the user can see and
fill. An invented grade leaves them with a fact they have no reason to doubt,
and the whole provenance design exists because of that asymmetry.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.resume.extraction import ExtractedDocument, ExtractedPage

DATASETS = Path(__file__).parent / "datasets"
DEFAULT_VERSION = "v1"

#: Which fields are compared per section. Only fields a resume asserts; adding
#: one the document does not state would mark a correct reading as wrong.
COMPARED_FIELDS = {
    "experiences": ("company", "job_title", "start_date", "end_date"),
    "education": ("institution", "degree", "grade"),
    "skills": ("name",),
    "projects": ("name",),
    "certifications": ("name",),
}


def load_cases(version: str = DEFAULT_VERSION) -> dict[str, Any]:
    path = DATASETS / version / "cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def as_document(case: dict[str, Any]) -> ExtractedDocument:
    """A case's pages, in the shape extraction would have produced."""
    return ExtractedDocument(
        pages=[
            ExtractedPage(page_number=index, text=text, method="text")
            for index, text in enumerate(case["pages"], start=1)
        ]
    )


# --- comparison --------------------------------------------------------------


def _normalise(value: Any) -> Any:
    """Compare on meaning, not on typography.

    Case and surrounding whitespace are not claims about a resume, so they are
    not held against a reading. A date keeps its precision, because that IS a
    claim: 'May 2024' read to the day is wrong even when the day happens to be
    the first.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, dict):
        return (value.get("value"), value.get("precision"))
    return value


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return _normalise(item.get(name))
    value = getattr(item, name, None)
    if value is not None and hasattr(value, "value"):  # CandidateDate
        return (
            value.value.isoformat() if value.value else None,
            value.precision,
        )
    return _normalise(value)


def _key(item: Any, fields: tuple[str, ...]) -> tuple:
    return tuple(_field(item, name) for name in fields)


@dataclass
class SectionScore:
    section: str
    expected: int = 0
    produced: int = 0
    matched: int = 0
    fabricated: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0

    @property
    def precision(self) -> float:
        return self.matched / self.produced if self.produced else 1.0


@dataclass
class CaseScore:
    case_id: str
    shape: str
    sections: dict[str, SectionScore]
    error: str | None = None

    @property
    def recall(self) -> float:
        scores = [s.recall for s in self.sections.values()]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def precision(self) -> float:
        scores = [s.precision for s in self.sections.values()]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def fabrications(self) -> int:
        return sum(len(s.fabricated) for s in self.sections.values())


def score_case(case: dict[str, Any], candidate) -> CaseScore:
    """Compare one reading against what the resume actually says."""
    sections: dict[str, SectionScore] = {}

    for section, fields in COMPARED_FIELDS.items():
        expected_items = case["expected"].get(section)
        if expected_items is None:
            # The case makes no claim about this section, so neither do we.
            continue

        produced_items = getattr(candidate, section, []) or []

        expected_keys = [_key(item, fields) for item in expected_items]
        produced_keys = [_key(item, fields) for item in produced_items]

        score = SectionScore(
            section=section,
            expected=len(expected_keys),
            produced=len(produced_keys),
        )

        remaining = list(expected_keys)
        for produced in produced_keys:
            if produced in remaining:
                remaining.remove(produced)
                score.matched += 1
            else:
                # Not in the expectation at all. Reported as a fabrication with
                # the identifying field, never the whole item — a scoring report
                # is not a place to accumulate resume content.
                score.fabricated.append(str(produced[0]))

        sections[section] = score

    return CaseScore(case_id=case["id"], shape=case["shape"], sections=sections)


# --- running -----------------------------------------------------------------


def build_structurer(name: str):
    from app.services.resume.structuring import (
        DeterministicResumeStructurer,
        ProviderResumeStructurer,
    )

    if name == "deterministic":
        return DeterministicResumeStructurer()
    if name == "ai":
        return ProviderResumeStructurer()
    raise SystemExit(f"unknown structurer {name!r}; use 'deterministic' or 'ai'")


def run(
    *,
    structurer=None,
    version: str = DEFAULT_VERSION,
    include_ocr: bool = True,
) -> list[CaseScore]:
    dataset = load_cases(version)
    structurer = structurer or build_structurer("deterministic")

    scores: list[CaseScore] = []
    for case in dataset["cases"]:
        if case.get("requires_ocr") and not include_ocr:
            continue
        try:
            reading = structurer.structure(as_document(case))
        except Exception as exc:  # a structurer that fails is a score of zero
            scores.append(
                CaseScore(
                    case_id=case["id"],
                    shape=case["shape"],
                    sections={},
                    error=type(exc).__name__,
                )
            )
            continue
        scores.append(score_case(case, reading.candidate))
    return scores


def report(scores: list[CaseScore]) -> str:
    lines = [
        f"{'case':<32} {'recall':>8} {'precision':>10} {'fabricated':>11}",
        "-" * 64,
    ]
    for score in scores:
        if score.error:
            lines.append(f"{score.case_id:<32} {'ERROR':>8} {score.error:>22}")
            continue
        lines.append(
            f"{score.case_id:<32} {score.recall:>8.2f} {score.precision:>10.2f} "
            f"{score.fabrications:>11}"
        )

    scored = [s for s in scores if not s.error]
    if scored:
        lines.append("-" * 64)
        lines.append(
            f"{'mean':<32} "
            f"{sum(s.recall for s in scored) / len(scored):>8.2f} "
            f"{sum(s.precision for s in scored) / len(scored):>10.2f} "
            f"{sum(s.fabrications for s in scored):>11}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structurer", default="deterministic", choices=["deterministic", "ai"])
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--skip-ocr",
        action="store_true",
        help="leave out cases that stand in for scanned documents",
    )
    args = parser.parse_args(argv)

    scores = run(
        structurer=build_structurer(args.structurer),
        version=args.version,
        include_ocr=not args.skip_ocr,
    )
    print(report(scores))
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
