"""Which grounding findings a tailoring evaluation's repairs were made for. Offline.

`tailoring_runner` reports repairs as one number per document. That number is
the length of `Settlement.repairs` — one WARNING per action reconciliation took —
and by the time the runner sees it, the first-pass grounding report that caused
those actions has been reduced to a count (`Settlement.errors_before`). This
module puts the detail back, from a captured run, without calling anything:

    captured raw document  ->  grounding.validate (first pass)
                           ->  reconciliation.repair
                           ->  one row per repair, joined to the error(s) behind it

It reuses the pipeline's own functions rather than re-implementing them, and it
checks its replay against `reconciliation.settle` for every document, so a table
that disagrees with what the runner counted says so instead of being printed.

Nothing here imports a provider, builds a tailor, or opens a connection: the
input is a JSONL capture written by `tailoring_runner --capture` (one line per
sample, holding the document the model returned), and every step after that is
deterministic over the frozen `tailoring_v1` profile.

Run it:

    .venv/Scripts/python.exe -m evaluation.tailoring_repairs var/tailoring-eval/capture.jsonl
    .venv/Scripts/python.exe -m evaluation.tailoring_repairs capture.jsonl --no-claims
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.models.tailoring import GENERATOR_AI
from app.schemas.tailored_resume import TailoredResumeDocument
from app.services.tailoring import grounding, reconciliation

#: Grounding's fixed reason strings, keyed by a short rule name for the tables.
#: Built from the module's own constants, so a renamed reason fails loudly here.
RULES: dict[str, str] = {
    value: name.removeprefix("REASON_").lower()
    for name, value in vars(grounding).items()
    if name.startswith("REASON_") and isinstance(value, str)
}

#: Reconciliation's reason strings, reduced to the action each one records.
ACTIONS: dict[str, str] = {
    reconciliation.REASON_REMOVED_ENTRY: "removed",
    reconciliation.REASON_REMOVED_LINE: "removed",
    reconciliation.REASON_REMOVED_SKILL: "removed",
    reconciliation.REASON_REMOVED_TECHNOLOGY: "removed",
    reconciliation.REASON_REMOVED_LINK: "removed",
    reconciliation.REASON_REMOVED_DUPLICATE: "removed",
    reconciliation.REASON_RESTORED_FIELD: "restored",
}

#: The files whose behaviour a replay depends on. Fingerprinted into a capture so
#: a replay against changed grounding or reconciliation code is flagged.
FINGERPRINTED = {
    "grounding": Path(grounding.__file__),
    "reconciliation": Path(reconciliation.__file__),
}

_INDEXED = re.compile(r"^(?P<section>[a-z_]+)\[(?P<index>\d+)\]")
_BULLET = re.compile(r"^(?P<section>[a-z_]+)\[(?P<index>\d+)\]\.bullets\[(?P<bullet>\d+)\]")


def fingerprints() -> dict[str, str]:
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        for name, path in FINGERPRINTED.items()
    }


@dataclass(frozen=True)
class RepairRow:
    case_id: str
    sample: int
    document: str  # "<case>#<sample>"
    total_repairs: int
    rule: str  # grounding rule behind it; "a+b" when several errors hit one target
    grounding_reason: str
    section: str
    source_field: str | None
    field_path: str | None
    claim: str | None
    action: str  # removed | restored
    repair_reason: str


@dataclass(frozen=True)
class DocumentReplay:
    case_id: str
    sample: int
    errors_before: int
    repairs: int
    delivered: bool
    rows: tuple[RepairRow, ...]
    unmatched_errors: tuple[grounding.Finding, ...]


# --- joining a repair to the errors behind it --------------------------------------


def _target(path: str | None) -> str | None:
    """The thing a finding points at, so a repair and its errors share a key.

    Grounding addresses a bad quote as `...bullets[2].source_quote` and an
    invented number on the same line as `...bullets[2]`; reconciliation removes
    `...bullets[2]` once for both. Stripping `.source_quote` gives them one key.
    """
    if path is None:
        return None
    return path.removesuffix(".source_quote")


def _key(finding: grounding.Finding) -> tuple[str | None, str | None]:
    # Skills and technologies are removed by NAME within one path, so the name is
    # part of the key there; everywhere else the path alone identifies the target.
    path = _target(finding.field_path)
    if path and (path.startswith("skills.") or path.endswith(".technologies")):
        return path, finding.claim
    return path, None


def _under(path: str | None, prefix: str) -> bool:
    if not path or not prefix:
        return False
    return path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")


def _source_field(document: TailoredResumeDocument, path: str | None) -> str | None:
    """Which snapshot column the repaired thing claimed to come from."""
    if not path:
        return None
    if path == "headline":
        return document.headline.source_field if document.headline else None
    match = _BULLET.match(path)
    if match:
        entries = getattr(document, match["section"], [])
        entry = entries[int(match["index"])] if int(match["index"]) < len(entries) else None
        bullets = entry.bullets if entry is not None else []
        position = int(match["bullet"])
        return bullets[position].source_field if position < len(bullets) else None
    match = _INDEXED.match(path)
    if match and match["section"] == "summary":
        position = int(match["index"])
        return document.summary[position].source_field if position < len(document.summary) else None
    # A copied field, a skill, a technology, a link: the field name is the source.
    if path.startswith("skills."):
        return "skills"
    return path.rsplit(".", 1)[-1] if "." in path else path.split("[", 1)[0]


def replay(
    case_id: str,
    sample: int,
    document: TailoredResumeDocument,
    version,
    labels: frozenset[str],
    *,
    include_claims: bool = True,
) -> DocumentReplay:
    first = grounding.validate(document, version, job_requirement_labels=labels)
    outcome = reconciliation.repair(document, version, first)

    # Cross-check against the pipeline's own sequence: the rows must describe
    # exactly the repairs the runner counted, or they describe nothing.
    settled = reconciliation.settle(
        document, version, job_requirement_labels=labels, repair_allowed=True
    )
    applied = outcome.repairs if settled.repairs else ()
    if len(applied) != len(settled.repairs) or len(first.errors) != settled.errors_before:
        raise RuntimeError(f"{case_id}#{sample}: replay disagrees with reconciliation.settle")

    errors_by_key: dict[tuple, list[grounding.Finding]] = defaultdict(list)
    for error in first.errors:
        errors_by_key[_key(error)].append(error)

    rows: list[RepairRow] = []
    matched: set[int] = set()
    for repair in applied:
        causes = errors_by_key.get(_key(repair), [])
        if not causes and _key(repair)[1] is None:
            # An entry removed for an unknown source id is noted at the entry's
            # path; restores of a whole entry's field share it. Fall back to any
            # error whose target lies under the repaired path.
            prefix = _target(repair.field_path) or ""
            causes = [e for e in first.errors if _under(_target(e.field_path), prefix)]
        matched.update(id(cause) for cause in causes)
        reasons = sorted({cause.reason for cause in causes})
        rows.append(
            RepairRow(
                case_id=case_id,
                sample=sample,
                document=f"{case_id}#{sample}",
                total_repairs=len(applied),
                rule="+".join(RULES.get(r, "unknown") for r in reasons) or "unmatched",
                grounding_reason=" | ".join(reasons) or "(no grounding error found for this repair)",
                section=repair.section,
                source_field=_source_field(document, repair.field_path),
                field_path=repair.field_path,
                claim=(repair.claim if include_claims else None),
                action=ACTIONS.get(repair.reason, "unknown"),
                repair_reason=repair.reason,
            )
        )

    return DocumentReplay(
        case_id=case_id,
        sample=sample,
        errors_before=len(first.errors),
        repairs=len(applied),
        delivered=settled.report.passed,
        rows=tuple(rows),
        unmatched_errors=tuple(e for e in first.errors if id(e) not in matched) if applied else (),
    )


# --- reading a capture -------------------------------------------------------------


def read_capture(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    current = fingerprints()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        recorded = record.get("fingerprints")
        if recorded and recorded != current:
            warnings.append(
                f"line {number}: grounding/reconciliation changed since capture "
                f"({recorded} -> {current}); replay reflects today's code"
            )
        records.append(record)
    return records, warnings


def replay_capture(records: Iterable[dict[str, Any]], *, include_claims: bool = True):
    # Imported here so that merely importing this module does not build the
    # dataset (which runs the resume pipeline over the golden document).
    from evaluation.datasets.tailoring_v1.inputs import cases, confirmed_version, structured_job
    from app.services.tailoring.generation import requirement_labels

    by_id = {case["id"]: case for case in cases()["cases"]}
    version = confirmed_version()
    replays: list[DocumentReplay] = []
    failures: Counter[str] = Counter()
    for record in records:
        if record.get("error"):
            failures[record["error"]] += 1
            continue
        if record.get("generator", GENERATOR_AI) != GENERATOR_AI:
            continue
        case = by_id[record["case_id"]]
        _, requirements = structured_job(case["job"])
        labels = frozenset(requirement_labels(requirements))
        document = TailoredResumeDocument.model_validate(record["document"])
        replays.append(
            replay(
                record["case_id"], int(record.get("sample", 0)), document, version, labels,
                include_claims=include_claims,
            )
        )
    return replays, failures


# --- reporting ---------------------------------------------------------------------


def _clip(text: str | None, width: int) -> str:
    if text is None:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def report(replays: list[DocumentReplay], failures: Counter[str] | None = None) -> str:
    rows = [row for r in replays for row in r.rows]
    out: list[str] = []

    out.append("repairs / delivered document, by case")
    out.append(f"  {'case':<18} {'docs':>4} {'deliv':>5} {'errs':>5} {'repairs':>7} {'per doc':>7}")
    by_case: dict[str, list[DocumentReplay]] = defaultdict(list)
    for r in replays:
        by_case[r.case_id].append(r)
    for case_id in sorted(by_case):
        docs = by_case[case_id]
        delivered = [d for d in docs if d.delivered]
        total = sum(d.repairs for d in delivered)
        out.append(
            f"  {case_id:<18} {len(docs):>4} {len(delivered):>5} "
            f"{sum(d.errors_before for d in docs):>5} {total:>7} "
            f"{(total / len(delivered)) if delivered else 0:>7.2f}"
        )
    if failures:
        for code, count in sorted(failures.items()):
            out.append(f"  (generator failed {count}x: {code} — no document, nothing to replay)")

    for title, key in (
        ("repairs by grounding rule", lambda row: row.rule),
        ("repairs by source field", lambda row: row.source_field or "-"),
        ("removals vs restorations", lambda row: row.action),
        ("repairs by section", lambda row: row.section),
    ):
        out.append("")
        out.append(title)
        for value, count in Counter(key(row) for row in rows).most_common():
            out.append(f"  {count:>4}  {value}")

    out.append("")
    out.append("by case and grounding rule")
    grouped: dict[tuple[str, str], list[RepairRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.case_id, row.rule)].append(row)
    for (case_id, rule), members in sorted(grouped.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        out.append(f"  {case_id} / {rule} / {len(members)}")
        for row in members:
            out.append(
                f"      {row.document:<18} {row.action:<8} {row.field_path or '-':<34} "
                f"src={row.source_field or '-':<12} {_clip(row.claim, 70)}"
            )

    unmatched = [(r.document, e) for r in replays for e in r.unmatched_errors]
    if unmatched:
        out.append("")
        out.append("grounding errors with no repair of their own (absorbed by another repair)")
        for document, error in unmatched:
            out.append(f"  {document:<18} {RULES.get(error.reason, '?'):<20} {error.field_path}")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="JSONL written by tailoring_runner --capture")
    parser.add_argument("--no-claims", action="store_true", help="omit the claim text column")
    args = parser.parse_args(argv)

    records, warnings = read_capture(args.capture)
    for warning in warnings:
        print(f"! {warning}", file=sys.stderr)
    replays, failures = replay_capture(records, include_claims=not args.no_claims)
    print(report(replays, failures))
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
