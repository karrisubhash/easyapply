"""One real AI call on one frozen `tailoring_v1` case — the gate before an evaluation.

`tailoring_runner --tailor ai` spends one call per case per sample and reports a
generator failure as nothing more than a code. Before spending that, this proves
the whole path works once, end to end, with the configured provider:

    request -> provider -> structured response (schema-validated)
            -> grounding -> reconciliation -> DOCX (if grounded)

Exactly ONE provider request: retries are forced to zero for this process only,
so a transient failure is reported rather than silently paid for twice. It runs
the same `reconciliation.settle` and `score_sample` the runner uses.

Run it (from services/api, in the project venv):

    .venv/Scripts/python.exe -m evaluation.tailoring_smoke
    .venv/Scripts/python.exe -m evaluation.tailoring_smoke --case backend_senior

Configuration comes from the environment / .env exactly as for the runner:
TAILORING_AI_PROVIDER, TAILORING_AI_MODEL and that provider's key. It does not
touch TAILORING_AI_ENABLED. It prints counts, codes and the location of the
DOCX — never the key, and never the document's text.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from app.core.config import settings
from app.models.tailoring import GENERATOR_AI
from app.services.tailoring import reconciliation
from app.services.tailoring.generation import ProviderTailor, TailoringInputs, requirement_labels
from app.services.tailoring.providers import get_provider
from app.services.tailoring.rendering import render
from evaluation.datasets.tailoring_v1.inputs import cases, confirmed_version, structured_job
from evaluation.tailoring_runner import profile_vocabulary, score_sample

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "var" / "tailoring-smoke"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default=None, help="case id; defaults to the first case")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    dataset = cases()
    by_id = {case["id"]: case for case in dataset["cases"]}
    case = by_id[args.case] if args.case else dataset["cases"][0]

    # One request, no retries — for this process only.
    settings.TAILORING_AI_MAX_RETRIES = 0
    provider = get_provider()
    print(f"interpreter:   {sys.executable}")
    print(f"provider:      {provider.name}  model: {provider.model}")
    print(f"ai enabled:    {settings.TAILORING_AI_ENABLED} (untouched)")
    print(f"case:          {case['id']}")

    profile = confirmed_version()
    job, requirements = structured_job(case["job"])
    labels = frozenset(requirement_labels(requirements))

    # 1. request -> provider -> structured response
    try:
        generated = ProviderTailor(provider=provider).generate(
            TailoringInputs(version=profile, job=job, requirements=requirements)
        )
    except Exception as exc:
        print(f"GENERATION FAILED: {getattr(exc, 'code', None) or type(exc).__name__}")
        traceback.print_exc()
        return 1

    document = generated.document
    meta = generated.metadata
    print("\n[1] structured response: OK (TailoredResumeDocument, schema-validated)")
    print(f"    generator={generated.generator} model={generated.model} prompt={generated.prompt_version}")
    print(
        "    "
        + " ".join(
            f"{key}={meta[key]}"
            for key in ("model", "upstream_provider", "stop_reason", "attempts",
                        "input_tokens", "output_tokens", "reasoning_tokens", "cost", "latency_ms")
            if key in meta
        )
    )
    print(
        f"    experience={len(document.experience)} projects={len(document.projects)} "
        f"education={len(document.education)} summary_lines={len(document.summary)} "
        f"targeted_requirements={len(document.targeted_requirements)}"
    )

    # 2 + 3. grounding -> reconciliation (the pipeline's own sequence)
    settlement = reconciliation.settle(
        document,
        profile,
        job_requirement_labels=labels,
        repair_allowed=generated.generator == GENERATOR_AI,
    )
    print("\n[2] grounding (first pass)")
    print(f"    errors={settlement.errors_before}")
    print("\n[3] reconciliation")
    print(f"    repairs applied={len(settlement.repairs)}  errors after={len(settlement.report.errors)}")
    print(f"    grounding passed={settlement.report.passed}")
    for finding in settlement.report.errors:
        # Our own reason and location — not the model's claim text.
        print(f"    - {finding.section} {finding.field_path or ''}: {finding.reason}")

    # 4. DOCX, only for a grounded document
    print("\n[4] DOCX")
    if settlement.report.passed:
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"{case['id']}.docx"
        data = render(settlement.document)
        path.write_bytes(data)
        print(f"    written: {path} ({len(data)} bytes)")
    else:
        print("    withheld: document did not pass grounding")

    score = score_sample(case, dataset["invariants"], settlement, profile_vocabulary(profile))
    print("\n[score] (the runner's own scoring, one sample)")
    print(f"    delivered={score.delivered} rendered={score.rendered} violations={score.violations}")
    print(f"    selection rate={score.selection.rate} missed={score.selection.failed}")
    print(f"    shape rate={score.shape.rate} missed={score.shape.failed}")

    return 0 if score.delivered and score.rendered else 2


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
