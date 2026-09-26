"""Running a matcher over the real-data benchmark and reporting what it did.

    dataset (human ground truth)
        → algorithm adapter
        → observation per requirement
        → metrics, grouped failures, per-case detail

Not a test, for the reason every runner in this directory gives: it reports
numbers for a person to read. `tests/test_benchmark.py` asserts the machinery —
that the dataset loads, that ground truth is independent, that the arithmetic
is right — and never a score. A threshold checked in before anyone knows what a
good score looks like freezes today's accident into tomorrow's requirement.

The report leads with false matches and ends with raw agreement, deliberately
and in that order. See `metrics.py` on why a single headline number would be
the most misleading thing this benchmark could publish.

    python -m evaluation.benchmark.runner
    python -m evaluation.benchmark.runner --failures
    python -m evaluation.benchmark.runner --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

from evaluation.benchmark import adapter as adapters
from evaluation.benchmark import failures as failure_analysis
from evaluation.benchmark import groundtruth
from evaluation.benchmark.failures import Failure
from evaluation.benchmark.metrics import Observation, Report
from evaluation.benchmark.schema import Dataset, VERDICTS


@dataclass
class BenchmarkRun:
    report: Report
    failures: list[Failure] = field(default_factory=list)
    dataset: Dataset | None = None

    @property
    def mismatches(self) -> list[Failure]:
        """Everything the report would show as a disagreement, most severe
        first. `EVIDENCE_MISMATCH` is included: the verdict was right and the
        justification was not, which is still a defect in an explainable
        matcher."""
        return sorted(self.failures, key=lambda f: (f.severity, f.case_id, f.requirement_id))


def run(
    *,
    dataset_version: str = groundtruth.DEFAULT_VERSION,
    algorithm: str = "engine",
) -> BenchmarkRun:
    data = groundtruth.load(dataset_version)
    matcher = adapters.build(algorithm)

    report = Report(algorithm_version=matcher.version, dataset_version=data.version)
    collected: list[Failure] = []

    for case in data.cases:
        profile = data.profiles[case.profile_id]
        job = data.jobs[case.job_id]

        result = matcher.run(profile=profile, job=job, as_of=case.as_of)
        # Reproducibility is measured, not assumed: a second run of identical
        # inputs must produce identical verdicts and identical citations.
        again = matcher.run(profile=profile, job=job, as_of=case.as_of)

        if result.verdicts == again.verdicts and result.summary == again.summary:
            report.reproducible.append(case.id)
        else:
            report.irreproducible.append(case.id)

        (report.score_consistent if result.self_consistent else report.score_inconsistent).append(
            case.id
        )

        actual = result.by_requirement()
        for truth in case.ground_truth:
            requirement = job.requirement(truth.requirement_id)
            verdict = actual.get(truth.requirement_id)
            if verdict is None:
                report.unjudged.append(f"{case.id}/{truth.requirement_id}")

            # The reviewer's per-pairing aspect wins over the job's, because
            # what a requirement tests depends on the profile it meets.
            aspect = truth.aspect or requirement.aspect
            observation = Observation(
                case_id=case.id,
                requirement_id=truth.requirement_id,
                category=requirement.category,
                aspect=aspect,
                is_required=requirement.is_required,
                expected=truth.verdict,
                actual=verdict.status if verdict else None,
                actual_is_required=verdict.is_required if verdict else None,
                actual_refs=verdict.refs if verdict else (),
                expected_refs=truth.evidence.refs,
                contested=truth.contested,
            )
            report.observations.append(observation)

            category = failure_analysis.categorise(observation)
            if category == failure_analysis.OTHER and observation.agrees:
                continue  # agreement with correct evidence is not a failure
            collected.append(
                Failure(
                    category=category,
                    case_id=case.id,
                    profile_id=profile.id,
                    profile_label=profile.label,
                    job_id=job.id,
                    job_label=job.label,
                    requirement_id=truth.requirement_id,
                    requirement_label=requirement.label,
                    requirement_category=requirement.category,
                    aspect=aspect,
                    is_required=requirement.is_required,
                    expected=truth.verdict,
                    actual=verdict.status if verdict else None,
                    expected_rationale=truth.rationale,
                    expected_refs=truth.evidence.refs,
                    actual_explanation=verdict.explanation if verdict else None,
                    actual_refs=verdict.refs if verdict else (),
                    actual_rule=verdict.rule_label if verdict else None,
                    contested=truth.contested,
                )
            )

    return BenchmarkRun(report=report, failures=collected, dataset=data)


# --- reporting -------------------------------------------------------------------


def _bar(label: str, rate, width: int = 28) -> str:
    value = rate.value
    if value is None:
        return f"  {label:<26} {'n/a':>6}"
    filled = round(value * width)
    return f"  {label:<26} {value:6.3f}  {'█' * filled}{'·' * (width - filled)}"


def report_text(run_result: BenchmarkRun, *, show_failures: bool = False) -> str:
    report = run_result.report
    data = run_result.dataset
    lines: list[str] = []

    lines += [
        "=" * 78,
        f"REAL-DATA MATCHING BENCHMARK — {report.dataset_version}",
        "=" * 78,
        f"algorithm      {report.algorithm_version}",
        f"dataset        {report.dataset_version}"
        + (f"  (reviewed {data.review.created})" if data else ""),
        f"profiles       {len(data.profiles) if data else 0}",
        f"jobs           {len(data.jobs) if data else 0}",
        f"cases          {len(data.cases) if data else 0}",
        f"judgments      {len(report.observations)}",
        "",
        "Coverage of structured job requirements by profile evidence, measured",
        "against independent human judgment. These numbers say nothing about",
        "whether any candidate would be interviewed, hired, or should apply.",
        "",
    ]

    # --- the errors that matter most, first ---------------------------------
    lines += [
        "-" * 78,
        "SAFETY — the errors a reader of the result cannot see",
        "-" * 78,
        f"  false matches              {len(report.false_matches):>4}"
        "   said MATCHED where the profile does not satisfy it",
        f"  false-match rate           {report.false_match_rate}"
        f"   of {report.false_match_rate.denominator} requirements not genuinely met",
        f"  unsupported matches        {len(report.unsupported_matches):>4}"
        "   said MATCHED citing nothing",
        "",
        f"  false missing              {len(report.false_missing):>4}"
        "   said nothing found where the profile satisfies it",
        "",
    ]

    # --- per verdict ---------------------------------------------------------
    lines += ["-" * 78, "PER VERDICT (one-vs-rest)", "-" * 78]
    lines.append(f"  {'verdict':<12} {'n(exp)':>7} {'n(act)':>7} {'precision':>10} {'recall':>8} {'f1':>7}")
    for verdict in VERDICTS:
        metrics = report.for_verdict(verdict)
        f1 = metrics.f1
        lines.append(
            f"  {verdict:<12} {metrics.expected:>7} {metrics.predicted:>7} "
            f"{str(metrics.precision):>10} {str(metrics.recall):>8} "
            f"{(f'{f1:.3f}' if f1 is not None else 'n/a'):>7}"
        )
    lines += [
        "",
        f"  uncertainty overcautious   {len(report.overcautious):>4}"
        "   hedged where the profile settles it",
        f"  uncertainty overconfident  {len(report.overconfident):>4}"
        "   answered where the profile does not",
        "",
    ]

    # --- confusion -----------------------------------------------------------
    table = report.confusion
    lines += ["-" * 78, "CONFUSION  (rows = human, columns = matcher)", "-" * 78]
    lines.append(f"  {'':<12}" + "".join(f"{verdict:>12}" for verdict in VERDICTS))
    for expected in VERDICTS:
        lines.append(
            f"  {expected:<12}" + "".join(f"{table[expected][a]:>12}" for a in VERDICTS)
        )
    lines.append("")

    # --- axes and evidence ---------------------------------------------------
    lines += ["-" * 78, "BY AXIS, EVIDENCE, AND INTEGRITY", "-" * 78]
    lines += [
        _bar("required agreement", report.required_agreement),
        _bar("preferred agreement", report.preferred_agreement),
        _bar("evidence correctness", report.evidence_rate),
        _bar("required/preferred read", report.classification_rate),
        "",
        f"  score self-consistent      {len(report.score_consistent)}/"
        f"{len(report.score_consistent) + len(report.score_inconsistent)} cases",
        f"  reproducible               {len(report.reproducible)}/"
        f"{len(report.reproducible) + len(report.irreproducible)} cases",
        f"  unjudged requirements      {len(report.unjudged)}",
        "",
    ]

    # --- where it struggles ---------------------------------------------------
    lines += ["-" * 78, "AGREEMENT BY ASPECT — what the requirement was testing", "-" * 78]
    for aspect, rate in sorted(
        report.by_aspect().items(), key=lambda pair: (pair[1].value or 0.0, pair[0])
    ):
        lines.append(
            f"  {aspect:<26} {str(rate):>6}   ({rate.numerator}/{rate.denominator})"
        )
    lines.append("")

    lines += ["-" * 78, "AGREEMENT BY CATEGORY", "-" * 78]
    for category, rate in sorted(
        report.by_category().items(), key=lambda pair: (pair[1].value or 0.0, pair[0])
    ):
        lines.append(
            f"  {category:<26} {str(rate):>6}   ({rate.numerator}/{rate.denominator})"
        )
    lines.append("")

    # --- failure categories ---------------------------------------------------
    counts = failure_analysis.summarise(run_result.failures)
    lines += ["-" * 78, "FAILURE CATEGORIES", "-" * 78]
    if not counts:
        lines.append("  none")
    for category, count in counts.items():
        lines.append(
            f"  {category:<26} {count:>4}   {failure_analysis.CATEGORY_NOTES[category]}"
        )
    lines.append("")

    # --- raw agreement, last and labelled ------------------------------------
    lines += [
        "-" * 78,
        "RAW AGREEMENT — reported last, and not a quality summary",
        "-" * 78,
        f"  all judgments              {report.agreement}"
        f"   ({report.agreement.numerator}/{report.agreement.denominator})",
        f"  uncontested only           {report.uncontested_agreement}"
        f"   ({report.uncontested_agreement.numerator}/"
        f"{report.uncontested_agreement.denominator})",
        "",
        "  A single agreement figure hides the asymmetry between the two error",
        "  directions above. Read the false-match count first.",
        "",
    ]

    if show_failures:
        lines += _failure_detail(run_result)

    return "\n".join(lines)


def _failure_detail(run_result: BenchmarkRun) -> list[str]:
    lines = ["=" * 78, "DETAILED DISAGREEMENTS", "=" * 78, ""]
    for category, items in failure_analysis.group(run_result.failures).items():
        lines += [
            "-" * 78,
            f"{category}  ({len(items)})  —  {failure_analysis.CATEGORY_NOTES[category]}",
            "-" * 78,
        ]
        for failure in items:
            axis = "required" if failure.is_required else "preferred"
            lines += [
                f"  case        {failure.case_id}",
                f"  profile     {failure.profile_id} — {failure.profile_label}",
                f"  job         {failure.job_id} — {failure.job_label}",
                f"  requirement {failure.requirement_id} [{failure.requirement_category}/{axis}/{failure.aspect}]",
                f"              {failure.requirement_label!r}",
                f"  expected    {failure.expected}"
                + (f"  via {list(failure.expected_refs)}" if failure.expected_refs else ""),
                f"  actual      {failure.actual}"
                + (f"  via {list(failure.actual_refs)}" if failure.actual_refs else "")
                + (f"   [{failure.actual_rule}]" if failure.actual_rule else ""),
                f"  human said  {failure.expected_rationale}",
                f"  matcher said{' ' + (failure.actual_explanation or '—')}",
            ]
            if failure.contested:
                lines.append("  NOTE        reviewers disagreed on this judgment")
            lines.append("")
    return lines


def report_json(run_result: BenchmarkRun) -> str:
    report = run_result.report
    return json.dumps(
        {
            "dataset_version": report.dataset_version,
            "algorithm_version": report.algorithm_version,
            "judgments": len(report.observations),
            "safety": {
                "false_matches": len(report.false_matches),
                "false_match_rate": report.false_match_rate.value,
                "false_missing": len(report.false_missing),
                "unsupported_matches": len(report.unsupported_matches),
            },
            "per_verdict": {
                verdict: {
                    "expected": report.for_verdict(verdict).expected,
                    "predicted": report.for_verdict(verdict).predicted,
                    "precision": report.for_verdict(verdict).precision.value,
                    "recall": report.for_verdict(verdict).recall.value,
                    "f1": report.for_verdict(verdict).f1,
                }
                for verdict in VERDICTS
            },
            "uncertainty": {
                "overcautious": len(report.overcautious),
                "overconfident": len(report.overconfident),
            },
            "axes": {
                "required_agreement": report.required_agreement.value,
                "preferred_agreement": report.preferred_agreement.value,
                "classification_rate": report.classification_rate.value,
            },
            "evidence": {
                "correctness": report.evidence_rate.value,
                "errors": len(report.evidence_errors),
            },
            "integrity": {
                "score_consistent_cases": len(report.score_consistent),
                "score_inconsistent_cases": len(report.score_inconsistent),
                "reproducible_cases": len(report.reproducible),
                "irreproducible_cases": len(report.irreproducible),
                "unjudged": report.unjudged,
            },
            "agreement_raw": report.agreement.value,
            "agreement_uncontested": report.uncontested_agreement.value,
            "by_aspect": {
                aspect: rate.value for aspect, rate in report.by_aspect().items()
            },
            "by_category": {
                category: rate.value for category, rate in report.by_category().items()
            },
            "confusion": report.confusion,
            "failure_categories": failure_analysis.summarise(run_result.failures),
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real-data matching benchmark")
    parser.add_argument("--dataset", default=groundtruth.DEFAULT_VERSION)
    parser.add_argument("--algorithm", default="engine")
    parser.add_argument(
        "--failures", action="store_true", help="print every disagreement in full"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    result = run(dataset_version=args.dataset, algorithm=args.algorithm)
    print(report_json(result) if args.json else report_text(result, show_failures=args.failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
