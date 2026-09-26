"""Running a tailor over `tailoring_v1` and scoring the documents it produces.

The measurement `docs/resume-tailoring.md` said did not exist: *how often does a
real model produce a tailored resume this application will hand over, and is it
any good when it does*. Until it has been run, `TAILORING_AI_ENABLED` stays off.

Deliberately not a test — the same reasoning `evaluation/runner.py` gives for
itself. A model is sampled, so this reports a distribution for a person to read,
and a threshold that goes red on a nondeterministic generator would be a flaky
build rather than a signal. What IS a test is the deterministic path
(`tests/test_tailoring_evaluation.py`), which asserts the per-case expectations
below case by case because that generator is reproducible.

It runs the same sequence the pipeline runs — `reconciliation.settle`, which is
validate, repair, validate again — rather than a reimplementation of it, because
a measurement of a slightly different sequence is a measurement of nothing.

Five things are counted per document, and they are not equally weighted:

  delivered     did it pass grounding and render. The headline number.
  safety        forbidden strings, invented numbers, altered contact details.
                COUNTED, not rated: one is a failure whatever the averages say.
  selection     the per-case expectations from cases.json — which skills led,
                which projects were carried, which bullets answered the posting.
                What tailoring is FOR, and the thing better wording cannot buy.
  repairs       how much reconciliation had to remove to get there. A safety net
                that is doing all the work is a prompt that is wrong.
  shape         both jobs, in order, nothing printed twice.

Run it:

    python -m evaluation.tailoring_runner                      # the baseline
    python -m evaluation.tailoring_runner --tailor ai --samples 5

The second makes real API calls and costs real money — four postings times the
sample count, one call each. It needs TAILORING_AI_PROVIDER configured and a key;
without one the provider raises `ai_not_configured` and every case reports it.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document

from app.models.tailoring import GENERATOR_AI
from app.services.tailoring import reconciliation
from app.services.tailoring.generation import (
    LEAD_SKILL_GROUP,
    TailoringInputs,
    requirement_labels,
)
from app.services.tailoring.rendering import render
from evaluation.datasets.tailoring_v1.inputs import cases, confirmed_version, structured_job

DATASETS = Path(__file__).parent / "datasets"
DEFAULT_VERSION = "tailoring_v1"
CRITERIA = "ai_release_criteria.json"

#: The same token shape `test_tailoring_evaluation.py` uses for the number check.
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9+.%#/_-]*")


def load_criteria(version: str = DEFAULT_VERSION) -> dict[str, Any]:
    return json.loads((DATASETS / version / CRITERIA).read_text(encoding="utf-8"))


# --- reading a document ------------------------------------------------------------


def all_text(document) -> str:
    """Every word the document puts in front of a reader."""
    parts: list[str] = []
    if document.contact is not None:
        parts += [
            value
            for value in (
                document.contact.full_name,
                document.contact.email,
                document.contact.phone,
                document.contact.location,
            )
            if value
        ]
        parts += [link.label or "" for link in document.contact.links]
    if document.headline is not None:
        parts.append(document.headline.text)
    parts += [line.text for line in document.summary]
    for group in document.skills:
        parts.append(group.category)
        parts += group.skills
    for entry in document.experience:
        parts += [entry.job_title, entry.company, entry.location or ""]
        parts += [bullet.text for bullet in entry.bullets]
    for project in document.projects:
        parts += [project.name, project.role or ""]
        parts += project.technologies
        parts += [bullet.text for bullet in project.bullets]
    for entry in document.education:
        parts += [entry.institution, entry.degree or "", entry.field_of_study or ""]
    for entry in document.certifications:
        parts += [entry.name, entry.issuing_organization or ""]
    parts += list(document.targeted_requirements)
    return " ".join(parts)


def lines(document) -> list[str]:
    out = [line.text for line in document.summary]
    for entry in document.experience:
        out += [bullet.text for bullet in entry.bullets]
    for project in document.projects:
        out += [bullet.text for bullet in project.bullets]
    return out


def lead_group(document):
    for group in document.skills:
        if group.category == LEAD_SKILL_GROUP:
            return group
    return None


def profile_vocabulary(version) -> set[str]:
    """Every token the profile contains, for the whole-document number check."""
    known: set[str] = set()
    sources = [version.summary or ""]
    sources += [row.description or "" for row in version.experiences]
    sources += [row.job_title for row in version.experiences]
    sources += [row.description or "" for row in version.projects]
    sources += [row.name for row in version.certifications]
    sources += [str(row.start_date) for row in version.experiences]
    for text in sources:
        known |= set(_TOKEN.findall(text.casefold()))
    return known


# --- scoring one document ----------------------------------------------------------


@dataclass
class Checks:
    """Named booleans. Kept as a list so a failure says which one."""

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def record(self, name: str, ok: bool) -> None:
        (self.passed if ok else self.failed).append(name)

    @property
    def rate(self) -> float | None:
        total = len(self.passed) + len(self.failed)
        return len(self.passed) / total if total else None


@dataclass
class SampleScore:
    case_id: str
    delivered: bool
    errors_before: int
    errors_after: int
    repairs: int
    rendered: bool
    violations: list[str]
    selection: Checks
    shape: Checks
    error: str | None = None


def score_sample(
    case: dict[str, Any],
    invariants: dict[str, Any],
    settlement: reconciliation.Settlement,
    vocabulary: set[str],
) -> SampleScore:
    document = settlement.document
    expect = case["expect"]
    delivered = settlement.report.passed

    rendered = False
    docx_text = ""
    if delivered:
        try:
            docx_text = "\n".join(
                paragraph.text
                for paragraph in Document(io.BytesIO(render(document))).paragraphs
            )
            rendered = True
        except Exception as exc:  # a renderer that cannot lay it out is a failure
            docx_text = ""
            rendered = False
            print(f"  ! {case['id']}: render failed: {type(exc).__name__}", file=sys.stderr)

    text = all_text(document)
    violations: list[str] = []

    # --- must never happen ---------------------------------------------------
    #
    # Checked on the DELIVERED document only. A document that was withheld was
    # never put in front of anyone, and counting its sins against the model
    # would be counting the same failure twice.
    if delivered:
        for phrase in expect.get("never_appears", []):
            if phrase.casefold() in text.casefold() or phrase.casefold() in docx_text.casefold():
                violations.append(f"forbidden:{phrase}")

        for line in lines(document):
            for token in _TOKEN.findall(line.casefold()):
                if any(character.isdigit() for character in token) and token not in vocabulary:
                    violations.append(f"number:{token}")

        contact = document.contact
        expected_contact = invariants["contact"]
        if contact is None:
            violations.append("contact:missing")
        else:
            for name in ("full_name", "email", "phone", "location"):
                if getattr(contact, name) != expected_contact[name]:
                    violations.append(f"contact:{name}")

    # --- shape ---------------------------------------------------------------
    shape = Checks()
    if delivered:
        companies = [entry.company for entry in document.experience]
        shape.record("career_whole_and_in_order", companies == invariants["experience_order"])
        printed = [" ".join(line.split()).casefold() for line in lines(document)]
        shape.record("no_duplicate_lines", len(printed) == len(set(printed)))
        shape.record("every_job_has_a_bullet", all(entry.bullets for entry in document.experience))

    # --- selection -----------------------------------------------------------
    selection = Checks()
    if delivered:
        group = lead_group(document)
        led = list(group.skills) if group else []
        for name in expect.get("lead_skills_include", []):
            selection.record(f"leads:{name}", name in led)
        for name in expect.get("lead_skills_exclude", []):
            selection.record(f"not-leads:{name}", name not in led)

        project_names = [project.name for project in document.projects]
        for name in expect.get("projects_include", []):
            selection.record(f"project:{name}", name in project_names)
        for name in expect.get("projects_exclude", []):
            selection.record(f"no-project:{name}", name not in project_names)
        if "projects_first" in expect:
            selection.record(
                "project-first", bool(project_names) and project_names[0] == expect["projects_first"]
            )

        for company, needle in expect.get("leading_bullet", {}).items():
            if company == "why":
                continue
            entry = next((e for e in document.experience if e.company == company), None)
            selection.record(
                f"leads-with:{company}",
                bool(entry and entry.bullets and needle.casefold() in entry.bullets[0].text.casefold()),
            )

        for company, needle in expect.get("bullet_selected", {}).items():
            if company == "why":
                continue
            entry = next((e for e in document.experience if e.company == company), None)
            selection.record(
                f"keeps:{company}:{needle}",
                bool(entry and any(needle.casefold() in b.text.casefold() for b in entry.bullets)),
            )

        certifications = {entry.name for entry in document.certifications}
        for name in expect.get("certifications_include", []):
            selection.record(f"certification:{name}", name in certifications)

        for metric in expect.get("metrics_preserved", []):
            selection.record(f"metric:{metric}", metric in text)

        claimed = list(document.targeted_requirements)
        for label in expect.get("targeted_include", []):
            selection.record(f"targets:{label}", label in claimed)
        for label in expect.get("targeted_exclude", []):
            selection.record(f"not-targets:{label}", label not in claimed)

    return SampleScore(
        case_id=case["id"],
        delivered=delivered,
        errors_before=settlement.errors_before,
        errors_after=len(settlement.report.errors),
        repairs=len(settlement.repairs),
        rendered=rendered,
        violations=violations,
        selection=selection,
        shape=shape,
    )


# --- running -----------------------------------------------------------------------


def build_tailor(name: str):
    from app.services.tailoring.generation import DeterministicTailor, ProviderTailor

    if name == "deterministic":
        return DeterministicTailor()
    if name == "ai":
        return ProviderTailor()
    raise SystemExit(f"unknown tailor {name!r}; use 'deterministic' or 'ai'")


def run(
    *,
    tailor=None,
    samples: int = 1,
    version: str = DEFAULT_VERSION,
    capture: Path | None = None,
) -> list[SampleScore]:
    """Generate, settle and score every case `samples` times.

    `capture`, when given, appends one JSON line per sample: the raw document the
    generator returned (or its failure code). It is written BEFORE settlement and
    changes nothing about scoring; it exists so `evaluation.tailoring_repairs` can
    replay grounding and reconciliation offline and say what each repair was for.
    """
    dataset = cases()
    invariants = dataset["invariants"]
    tailor = tailor or build_tailor("deterministic")

    profile = confirmed_version()
    vocabulary = profile_vocabulary(profile)

    scores: list[SampleScore] = []
    for sample in range(samples):
        for case in dataset["cases"]:
            job, requirements = structured_job(case["job"])
            labels = frozenset(requirement_labels(requirements))
            try:
                generated = tailor.generate(
                    TailoringInputs(version=profile, job=job, requirements=requirements)
                )
            except Exception as exc:
                _capture(capture, case_id=case["id"], sample=sample,
                         error=getattr(exc, "code", None) or type(exc).__name__)
                # A generator that fails is a document not delivered, and the
                # code is the interesting part — `ai_not_configured` and
                # `ai_rate_limited` are very different news.
                scores.append(
                    SampleScore(
                        case_id=case["id"],
                        delivered=False,
                        errors_before=0,
                        errors_after=0,
                        repairs=0,
                        rendered=False,
                        violations=[],
                        selection=Checks(),
                        shape=Checks(),
                        error=getattr(exc, "code", None) or type(exc).__name__,
                    )
                )
                continue

            _capture(capture, case_id=case["id"], sample=sample, generated=generated)
            settlement = reconciliation.settle(
                generated.document,
                profile,
                job_requirement_labels=labels,
                repair_allowed=generated.generator == GENERATOR_AI,
            )
            scores.append(score_sample(case, invariants, settlement, vocabulary))
    return scores


def _capture(path: Path | None, *, case_id: str, sample: int, generated=None, error=None) -> None:
    """One JSON line per sample, for offline replay. Never the prompt or a key."""
    if path is None:
        return
    from evaluation.tailoring_repairs import fingerprints

    record: dict[str, Any] = {"case_id": case_id, "sample": sample, "fingerprints": fingerprints()}
    if error is not None:
        record["error"] = error
    else:
        record.update(
            generator=generated.generator,
            model=generated.model,
            prompt_version=generated.prompt_version,
            document=generated.document.model_dump(mode="json"),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --- reporting ---------------------------------------------------------------------


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.2f}"


def report(scores: list[SampleScore]) -> str:
    by_case: dict[str, list[SampleScore]] = {}
    for score in scores:
        by_case.setdefault(score.case_id, []).append(score)

    out = [
        f"{'case':<18} {'n':>3} {'deliv':>6} {'errs':>5} {'rep':>4} {'select':>7} {'shape':>6} {'bad':>4}",
        "-" * 62,
    ]
    for case_id in sorted(by_case):
        samples = by_case[case_id]
        delivered = [s for s in samples if s.delivered]
        out.append(
            f"{case_id:<18} {len(samples):>3} "
            f"{len(delivered) / len(samples):>6.2f} "
            f"{_mean([float(s.errors_before) for s in samples]):>5} "
            f"{_mean([float(s.repairs) for s in delivered]) or 0:>4.1f} "
            f"{_fmt(_mean([r for s in delivered if (r := s.selection.rate) is not None])):>7} "
            f"{_fmt(_mean([r for s in delivered if (r := s.shape.rate) is not None])):>6} "
            f"{sum(len(s.violations) for s in samples):>4}"
        )

    errors = [s for s in scores if s.error]
    if errors:
        out.append("")
        counted: dict[str, int] = {}
        for score in errors:
            counted[score.error] = counted.get(score.error, 0) + 1
        for code, count in sorted(counted.items()):
            out.append(f"  generator failed {count}x: {code}")

    failures: dict[str, int] = {}
    for score in scores:
        for name in score.selection.failed + score.shape.failed:
            failures[name] = failures.get(name, 0) + 1
    if failures:
        out.append("")
        out.append("  expectations missed:")
        for name, count in sorted(failures.items(), key=lambda item: -item[1]):
            out.append(f"    {count:>3}x  {name}")

    violations: dict[str, int] = {}
    for score in scores:
        for name in score.violations:
            violations[name] = violations.get(name, 0) + 1
    if violations:
        out.append("")
        out.append("  MUST NEVER HAPPEN:")
        for name, count in sorted(violations.items(), key=lambda item: -item[1]):
            out.append(f"    {count:>3}x  {name}")

    return "\n".join(out)


def gate(scores: list[SampleScore], criteria: dict[str, Any]) -> tuple[bool, str]:
    """The frozen release criteria, applied to what was just measured.

    Reports a verdict rather than exiting non-zero on a bad one: this is a
    measurement, and a person decides what to do about it. The only thing a
    non-zero exit would add is the temptation to move a number until it passes.
    """
    rates = criteria["rates"]
    never = criteria["must_never_happen"]
    delivered = [s for s in scores if s.delivered]

    lines_out: list[str] = []
    ok = True

    def check(name: str, value: float | None, minimum: float) -> None:
        nonlocal ok
        if value is None:
            lines_out.append(f"  ?  {name}: nothing measured")
            return
        good = value >= minimum
        ok = ok and good
        lines_out.append(f"  {'OK' if good else 'NO':<2} {name}: {value:.3f} (needs >= {minimum})")

    def count(name: str, value: int, maximum: int) -> None:
        nonlocal ok
        good = value <= maximum
        ok = ok and good
        lines_out.append(f"  {'OK' if good else 'NO':<2} {name}: {value} (needs <= {maximum})")

    forbidden = sum(1 for s in scores for v in s.violations if v.startswith("forbidden:"))
    numbers = sum(1 for s in scores for v in s.violations if v.startswith("number:"))
    contact = sum(1 for s in scores for v in s.violations if v.startswith("contact:"))
    count("forbidden strings", forbidden, never["forbidden_strings"])
    count("fabricated numbers", numbers, never["fabricated_numbers"])
    count("wrong contact details", contact, never["wrong_contact_details"])

    check(
        "delivered",
        len(delivered) / len(scores) if scores else None,
        rates["delivered"]["minimum"],
    )
    check(
        "career whole and in order",
        _mean(
            [
                1.0 if "career_whole_and_in_order" in s.shape.passed else 0.0
                for s in delivered
            ]
        ),
        rates["career_whole_and_in_order"]["minimum"],
    )
    check(
        "no duplicate lines",
        _mean([1.0 if "no_duplicate_lines" in s.shape.passed else 0.0 for s in delivered]),
        rates["no_duplicate_lines"]["minimum"],
    )
    check(
        "selection expectations held",
        _mean([r for s in delivered if (r := s.selection.rate) is not None]),
        rates["selection_expectations_held"]["minimum"],
    )

    budget = criteria["budgets"]["repairs_per_delivered_document"]["maximum"]
    mean_repairs = _mean([float(s.repairs) for s in delivered])
    if mean_repairs is None:
        lines_out.append("  ?  repairs per delivered document: nothing measured")
    else:
        good = mean_repairs <= budget
        ok = ok and good
        lines_out.append(
            f"  {'OK' if good else 'NO':<2} repairs per delivered document: "
            f"{mean_repairs:.2f} (needs <= {budget})"
        )

    verdict = "MEETS the release criteria" if ok else "DOES NOT meet the release criteria"
    return ok, "\n".join([f"release criteria ({criteria['criteria_version']}): {verdict}"] + lines_out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tailor", default="deterministic", choices=["deterministic", "ai"])
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="how many times to run every case. A model is sampled; one pass is an anecdote.",
    )
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--capture",
        type=Path,
        default=None,
        help=(
            "write each sample's raw document to this JSONL, for evaluation.tailoring_repairs. "
            "Must be a new file unless --overwrite-capture is given"
        ),
    )
    parser.add_argument(
        "--overwrite-capture",
        action="store_true",
        help="replace an existing --capture file instead of refusing to run",
    )
    args = parser.parse_args(argv)
    if args.overwrite_capture and args.capture is None:
        parser.error("--overwrite-capture requires --capture")
    # Records are appended one sample at a time, so reusing a path would mix two
    # runs in one file. Refused before the tailor is built, so a wrong path costs
    # no provider call.
    if args.capture is not None and args.capture.exists() and not args.overwrite_capture:
        raise SystemExit(
            f"capture file {args.capture} already exists; choose a new path, "
            "or pass --overwrite-capture to replace it"
        )

    dataset = cases()
    calls = len(dataset["cases"]) * args.samples
    if args.tailor == "ai":
        print(f"# {calls} provider calls, one per case per sample. This costs money.\n")

    tailor = build_tailor(args.tailor)
    if args.overwrite_capture:
        # Removed only once the tailor exists, so a configuration error does not
        # cost the old capture; this run's records then start a fresh file.
        args.capture.unlink(missing_ok=True)

    scores = run(
        tailor=tailor,
        samples=args.samples,
        version=args.version,
        capture=args.capture,
    )
    print(report(scores))
    print()
    print(gate(scores, load_criteria(args.version))[1])
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
