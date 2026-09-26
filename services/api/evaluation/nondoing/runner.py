"""Running the non_doing_v1 benchmark and reporting what it found.

    dataset (independently authored expectations, per pipeline stage)
        -> adapter (the only module that touches the matcher)
        -> Sentence/PipelineObservation
        -> Report

Not a test: `tests/test_nondoing_benchmark.py` asserts the machinery loads and
runs; this prints a number for a person to read, and per the sibling
benchmarks' own README, no threshold here is a pass/fail gate.

    python -m evaluation.nondoing.runner
    python -m evaluation.nondoing.runner --json
    python -m evaluation.nondoing.runner --failures
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

from evaluation.nondoing import adapter
from evaluation.nondoing import groundtruth
from evaluation.nondoing.metrics import PipelineObservation, Report, SentenceObservation
from evaluation.nondoing.schema import Dataset


@dataclass
class BenchmarkRun:
    report: Report
    dataset: Dataset


def run(dataset_version: str = groundtruth.DEFAULT_VERSION) -> BenchmarkRun:
    dataset = groundtruth.load(dataset_version)
    report = Report(
        dataset_version=dataset.version, algorithm_version=adapter.algorithm_version()
    )
    for case in dataset.sentence_cases:
        report.sentence_observations.append(adapter.evaluate_sentence(case))
    for case in dataset.pipeline_cases:
        report.pipeline_observations.append(adapter.evaluate_pipeline(case))
    return BenchmarkRun(report=report, dataset=dataset)


# --- reporting ------------------------------------------------------------------


def _rate_line(label: str, rate, extra: str = "") -> str:
    return f"  {label:<32} {str(rate):>6}   ({rate.numerator}/{rate.denominator}){extra}"


def report_text(run_result: BenchmarkRun, *, show_failures: bool = False) -> str:
    report = run_result.report
    dataset = run_result.dataset
    lines: list[str] = []

    lines += [
        "=" * 78,
        f"NON_DOING_MARKERS BENCHMARK - {report.dataset_version}",
        "=" * 78,
        f"algorithm            {report.algorithm_version}",
        f"dataset              {report.dataset_version}  (reviewed {dataset.review.created})",
        f"sentence cases       {len(dataset.sentence_cases)}",
        f"pipeline cases       {len(dataset.pipeline_cases)}",
        f"total cases          {dataset.case_count}",
        "",
        "Every expected value below was authored from the sentence text alone, "
        "before this runner was ever executed. See the dataset README's "
        "independence statement.",
        "",
    ]

    # --- overall -----------------------------------------------------------
    lines += ["-" * 78, "OVERALL AGREEMENT", "-" * 78]
    lines.append(_rate_line("sentence-level (is_non_doing)", report.sentence_agreement))
    lines.append(_rate_line("pipeline-level (all 5 stages)", report.pipeline_agreement))
    lines.append(_rate_line("all cases", report.overall_agreement))
    lines.append("")

    # --- safety --------------------------------------------------------------
    lines += ["-" * 78, "SAFETY - sentence-level is_non_doing errors", "-" * 78]
    lines.append(
        f"  false positives (evidence wrongly discarded)   {len(report.false_positives):>4}"
    )
    lines.append(
        f"  false negatives (disclaimer wrongly counted)    {len(report.false_negatives):>4}"
    )
    lines.append("")

    # --- per category / marker ------------------------------------------------
    lines += ["-" * 78, "PER-CATEGORY ACCURACY", "-" * 78]
    for category, rate in sorted(
        report.accuracy_by_category().items(), key=lambda pair: (pair[1].value or 0.0, pair[0])
    ):
        lines.append(_rate_line(category, rate))
    lines.append("")

    lines += ["-" * 78, "PER-MARKER ACCURACY", "-" * 78]
    for marker, rate in sorted(
        report.accuracy_by_marker().items(), key=lambda pair: (pair[1].value or 0.0, pair[0])
    ):
        lines.append(_rate_line(marker, rate))
    lines.append("")

    # --- pipeline sections -----------------------------------------------------
    lines += ["-" * 78, "TIER CHANGES  (expected tier != actual tier)", "-" * 78]
    if not report.tier_changes:
        lines.append("  none")
    for observation, token, expected_tier, actual_tier in report.tier_changes:
        lines.append(
            f"  {observation.case.id:<14} {token:<16} expected={expected_tier:<22} "
            f"actual={actual_tier}"
        )
    lines.append("")

    lines += ["-" * 78, "DURATION CHANGES  (expected duration reading != actual)", "-" * 78]
    if not report.duration_changes:
        lines.append("  none")
    for observation in report.duration_changes:
        expected = observation.case.expected.duration
        exp = f"{expected.years:g}y" if expected.measurable else "uncertain/none"
        act = (
            f"{observation.actual_duration_years:g}y"
            if observation.actual_duration_years is not None
            else "uncertain/none"
        )
        lines.append(f"  {observation.case.id:<14} expected={exp:<16} actual={act}")
    lines.append("")

    lines += ["-" * 78, "MINIMAL-PAIR FAILURES", "-" * 78]
    if not report.minimal_pair_failures:
        lines.append("  none")
    for pair_id, members in report.minimal_pair_failures:
        lines.append(f"  {pair_id}")
        for member in members:
            case_id = member.case.id
            agrees = "OK" if member.agrees else "FAIL"
            lines.append(f"      {case_id:<14} {agrees}")
    lines.append("")

    lines += ["-" * 78, "TECHNOLOGY-ATTRIBUTION FAILURES (two technologies, one clause)", "-" * 78]
    if not report.technology_attribution_failures:
        lines.append("  none")
    for observation in report.technology_attribution_failures:
        lines.append(f"  {observation.case.id:<14} {observation.case.note[:90]}")
        for token, field_name, detail in observation.token_mismatches():
            lines.append(f"      {token:<14} {field_name:<12} {detail}")
        if not observation.resolution_agrees:
            lines.append(
                f"      resolved_subject expected={observation.case.expected.resolved_subject} "
                f"actual={observation.actual_resolved_subject}"
            )
    lines.append("")

    lines += ["-" * 78, "TITLE-PATH FAILURES (job_title clause vs description clause)", "-" * 78]
    if not report.title_path_failures:
        lines.append("  none")
    for observation in report.title_path_failures:
        lines.append(f"  {observation.case.id:<14} {observation.case.note[:90]}")
    lines.append("")

    lines += ["-" * 78, "*-AWAY CONSTRUCTION FAILURES", "-" * 78]
    if not report.away_construction_failures:
        lines.append("  none")
    for observation in report.away_construction_failures:
        lines.append(f"  {observation.case.id}")
    lines.append("")

    # --- defect classification ----------------------------------------------------
    lines += ["-" * 78, "DEFECT CLASSIFICATION (predicted before the run, checked against it)", "-" * 78]
    for defect_class, counts in sorted(report.defect_breakdown().items()):
        lines.append(
            f"  {defect_class:<18} confirmed={counts['predicted_and_confirmed']:>3}  "
            f"resilient(no defect after all)={counts['predicted_but_correct']:>3}"
            + (
                f"  surprises={counts['unpredicted_surprise']:>3}"
                if "unpredicted_surprise" in counts
                else ""
            )
        )
    lines.append("")

    lines += ["-" * 78, "SURPRISES - controls (defect_class=none) that disagreed anyway", "-" * 78]
    if not report.surprises:
        lines.append("  none")
    for observation in report.surprises:
        lines.append(f"  {observation.case.id}")
    lines.append("")

    if show_failures:
        lines += _failure_detail(report)

    return "\n".join(lines)


def _failure_detail(report: Report) -> list[str]:
    lines = ["=" * 78, "DETAILED DISAGREEMENTS", "=" * 78, ""]
    for observation in report.sentence_observations:
        if observation.agrees:
            continue
        case = observation.case
        lines += [
            f"  case        {case.id}  [{case.category}/{case.construction}] markers={list(case.markers)}",
            f"  text        {case.text!r}",
            f"  expected    {case.expected_non_doing}",
            f"  actual      {observation.actual_non_doing}",
            f"  rationale   {case.rationale}",
            f"  defect      {case.defect_class}",
            "",
        ]
    for observation in report.pipeline_observations:
        if observation.agrees:
            continue
        case = observation.case
        lines += [
            f"  case        {case.id}  [{case.category}] markers={list(case.markers)}",
            f"  note        {case.note}",
            f"  first divergent stage:  {observation.first_divergent_stage}",
        ]
        for token, field_name, detail in observation.token_mismatches():
            lines.append(f"      token {token}: {field_name} - {detail}")
        if not observation.resolution_agrees:
            lines.append(
                f"      resolved_subject expected={case.expected.resolved_subject} "
                f"actual={observation.actual_resolved_subject}"
            )
        if not observation.duration_agrees:
            lines.append(
                f"      duration expected={case.expected.duration} "
                f"actual_years={observation.actual_duration_years} "
                f"actual_uncertain={observation.actual_duration_uncertain}"
            )
        if not observation.verdict_agrees:
            lines.append(
                f"      verdict expected={case.expected.verdict} actual={observation.actual_verdict}"
            )
        lines.append(f"  defect      {case.defect_class}")
        lines.append("")
    return lines


def report_json(run_result: BenchmarkRun) -> str:
    report = run_result.report
    return json.dumps(
        {
            "dataset_version": report.dataset_version,
            "algorithm_version": report.algorithm_version,
            "cases": {
                "sentence": len(report.sentence_observations),
                "pipeline": len(report.pipeline_observations),
            },
            "agreement": {
                "sentence": report.sentence_agreement.value,
                "pipeline": report.pipeline_agreement.value,
                "overall": report.overall_agreement.value,
            },
            "safety": {
                "false_positives": len(report.false_positives),
                "false_negatives": len(report.false_negatives),
            },
            "by_category": {k: v.value for k, v in report.accuracy_by_category().items()},
            "by_marker": {k: v.value for k, v in report.accuracy_by_marker().items()},
            "tier_changes": len(report.tier_changes),
            "duration_changes": len(report.duration_changes),
            "minimal_pair_failures": len(report.minimal_pair_failures),
            "technology_attribution_failures": len(report.technology_attribution_failures),
            "title_path_failures": len(report.title_path_failures),
            "away_construction_failures": len(report.away_construction_failures),
            "defect_breakdown": report.defect_breakdown(),
            "surprises": [o.case.id for o in report.surprises],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="non_doing_v1 benchmark")
    parser.add_argument("--dataset", default=groundtruth.DEFAULT_VERSION)
    parser.add_argument("--failures", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = run(dataset_version=args.dataset)
    print(report_json(result) if args.json else report_text(result, show_failures=args.failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
