"""Generate the blind second-rater packet. Covers two datasets, differently.

Evaluation tooling. Writes three files:

    packet.md               what the second rater reads. Blind by construction.
    manifest.json           row id -> (case, requirement). ANALYST ONLY.
    answers_template.json   the file the second rater fills in.

Nothing here writes to any dataset file, and the first rater's verdicts are
never read into the packet — `_check_no_leaks` refuses to write a packet that
contains any of them.

**The two halves are selected differently, on purpose.**

`declared_only_v1` (rows R01-R27, pairings 1-10) is selected by the engine: it
takes the judgments whose operative evidence tier is `declared_only`, which is
why this script imports the matcher. That is a selection criterion, not
content — no tier, verdict, confidence or score reaches the packet.

`declared_only_v2` (rows R28 onward) is selected by its own `pairings.json`,
authored by hand from the shape each fixture was written to probe. The matcher
is not consulted for it at all, and it has no `ground_truth.json` to consult
either: that dataset holds fixtures and no expectations, which is the whole
reason it exists.

Row ids, candidate letters and pairing numbers are assigned v1 first, so
adding v2 cannot renumber an existing row. Run:

    python evaluation/datasets/declared_only_v1/review_r2/build_packet.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = HERE.parents[3]
sys.path.insert(0, str(API))

from evaluation.benchmark import groundtruth  # noqa: E402
from evaluation.benchmark.adapter import DeterministicEngineAdapter  # noqa: E402

DATASET = "declared_only_v1"
FIXTURES_ONLY_DATASET = "declared_only_v2"

# --- what may appear in the packet -------------------------------------------
#
# An allowlist, not a denylist: a field is rendered only if it is named here.
# `label`, `summary` and `note` are DELIBERATELY absent from every one of these
# — in this dataset they are authoring metadata that describe what each fixture
# was built to test ("three wholly uncorroborated claims"), which would hand the
# second rater the answer.

PROFILE_FIELDS = {
    "skills": ("name", "years", "proficiency"),
    "experiences": ("job_title", "company", "start", "end", "employment_type", "location", "description"),
    "education": ("institution", "degree", "field_of_study"),
    "projects": ("name", "technologies", "start", "end", "description"),
}
REQUIREMENT_FIELDS = ("category", "label", "is_required", "minimum_years", "degree_level", "field_of_study", "source_quote")
JOB_FIELDS = ("job_title", "company_kind", "location", "description")


def declared_only_rows(dataset):
    """The judgments whose operative tier is declared_only, in dataset order."""
    from app.services.matching.engine import JobPosting, evaluate
    from app.services.matching.profile_index import ProfileIndex
    from app.services.matching.rules import resolve_technology_subject

    selected = []
    for case in dataset.cases:
        profile = dataset.profiles[case.profile_id]
        job = dataset.jobs[case.job_id]
        adapter = DeterministicEngineAdapter()
        version = adapter._profile_version(profile)
        candidate = adapter._candidate(job)
        index = ProfileIndex(version)
        result = evaluate(candidate, version, as_of=case.as_of,
                          posting=JobPosting(location_raw=job.location, workplace_type=None))
        for judgment in result.judgments:
            rid = adapter._positions.get((judgment.category, judgment.requirement_index))
            if rid is None:
                continue
            key = judgment.requirement_key
            evidence = index.evidence_for(key) if key else None
            tier = evidence.tier if evidence else None
            if tier is None and judgment.category == "experience":
                requirement = job.requirement(rid)
                subject = resolve_technology_subject(requirement.label, index) if requirement else None
                if subject:
                    tier = index.evidence_for(subject).tier
            if tier == "declared_only":
                selected.append((case, profile, job, rid))
    return selected


def _fmt_date(value):
    return value.isoformat() if value else None


def _candidate_dossier(profile, as_of):
    """The profile as a document, with no authoring metadata attached."""
    def plural(count: int, singular: str, plural_form: str | None = None) -> str:
        return f"{count} {singular if count == 1 else (plural_form or singular + 's')}"

    lines = []
    counts = ", ".join((
        plural(len(profile.skills), "skill"),
        plural(len(profile.experiences), "role"),
        plural(len(profile.projects), "project"),
        plural(len(profile.education), "education entry", "education entries"),
    ))
    lines.append(f"*The profile holds {counts}. Everything it records is reproduced below.*\n")

    lines.append("**Skills list**\n")
    if profile.skills:
        lines.append("| skill | years stated | proficiency stated |")
        lines.append("|---|---|---|")
        for item in profile.skills:
            years = f"{item.years:g}" if item.years is not None else "—"
            lines.append(f"| {item.name} | {years} | {item.proficiency or '—'} |")
    else:
        lines.append("*(none)*")
    lines.append("")

    lines.append("**Employment**\n")
    if profile.experiences:
        for item in profile.experiences:
            end = _fmt_date(item.end) or "present"
            kind = f", {item.employment_type}" if item.employment_type else ""
            lines.append(f"- **{item.job_title}**, {item.company} — {_fmt_date(item.start)} to {end}"
                         f"{kind}{', ' + item.location if item.location else ''}")
            if item.description:
                lines.append(f"  > {item.description}")
    else:
        lines.append("*(none)*")
    lines.append("")

    lines.append("**Projects**\n")
    if profile.projects:
        for item in profile.projects:
            end = _fmt_date(item.end) or "ongoing"
            lines.append(f"- **{item.name}** — {_fmt_date(item.start) or 'undated'} to {end}")
            if item.technologies:
                lines.append(f"  - technologies listed on the project: {', '.join(item.technologies)}")
            if item.description:
                lines.append(f"  > {item.description}")
    else:
        lines.append("*(none)*")
    lines.append("")

    lines.append("**Education**\n")
    if profile.education:
        for item in profile.education:
            parts = ", ".join(p for p in (item.degree, item.field_of_study) if p)
            lines.append(f"- {parts or 'unspecified'} — {item.institution}")
    else:
        lines.append("*(none)*")
    lines.append("")
    lines.append(f"**Judge this profile as it stood on {as_of.isoformat()}.**")
    return "\n".join(lines)


def fixture_pairings():
    """`declared_only_v2`'s sections, selected by its own authored pairings.json.

    No matcher is consulted here and there is nothing to consult: that dataset
    ships fixtures and no expectations. If a `ground_truth.json` ever appears
    beside them, this refuses rather than quietly treating authored
    expectations as if they were still absent.
    """
    directory = groundtruth.DATASETS / FIXTURES_ONLY_DATASET
    if not directory.is_dir():
        return []
    if (directory / "ground_truth.json").exists():
        raise SystemExit(
            f"{FIXTURES_ONLY_DATASET} now has a ground_truth.json. This builder treats it as a "
            f"fixtures-only dataset; if expectations exist, the packet's blinding has to be "
            f"re-reviewed before it is regenerated."
        )

    def _read(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))

    profiles = {raw["id"]: groundtruth._profile(raw) for raw in _read("profiles.json")["profiles"]}
    jobs = {raw["id"]: groundtruth._job(raw) for raw in _read("jobs.json")["jobs"]}

    sections = []
    for entry in _read("pairings.json")["pairings"]:
        profile, job = profiles[entry["profile_id"]], jobs[entry["job_id"]]
        for rid in entry["requirement_ids"]:
            if job.requirement(rid) is None:
                raise SystemExit(f"{entry['id']}: job {job.id} states no requirement {rid!r}")
        sections.append((entry["id"], date.fromisoformat(entry["as_of"]), profile, job,
                         list(entry["requirement_ids"]), FIXTURES_ONLY_DATASET))
    return sections


def build():
    dataset = groundtruth.load(DATASET)
    selected = declared_only_rows(dataset)

    # Group by pairing, preserving dataset order.
    pairings: list[tuple] = []
    for case, profile, job, rid in selected:
        if not pairings or pairings[-1][0].id != case.id:
            pairings.append((case, profile, job, []))
        pairings[-1][3].append(rid)

    # v1 first, always: row ids, candidate letters and pairing numbers are
    # assigned in this order, so appending v2 can never renumber an existing row.
    sections = [(case.id, case.as_of, profile, job, rids, DATASET)
                for case, profile, job, rids in pairings]
    sections += fixture_pairings()

    candidate_letter: dict[str, str] = {}
    manifest = {
        "datasets": [DATASET, FIXTURES_ONLY_DATASET],
        "generated_from": (
            f"{DATASET}/ground_truth.json (unmodified, for row selection only) and "
            f"{FIXTURES_ONLY_DATASET}/pairings.json (authored; that dataset has no expectations)"
        ),
        "rows": [],
    }
    answers = {
        "_instructions": "Fill in one object per row. Read RUBRIC.md first. Leave nothing blank except `notes`.",
        "reviewer": {"name": "", "role": "", "rated_on": "", "minutes_spent": "",
                     "read_rubric": "", "prior_exposure_to_this_dataset": ""},
        "ratings": [],
    }

    out = []
    out.append("# declared-only evidence — second-rater packet\n")
    out.append("Read `RUBRIC.md` before starting, and record every answer in "
               "`answers.json` (copy `answers_template.json`).\n")
    out.append("Each section below is one **pairing**: one candidate profile and one job posting, "
               "followed by the specific requirements from that posting you are asked to judge. "
               "Everything the profile records is reproduced in full — nothing relevant has been "
               "filtered out, and no item has been marked as the one that matters.\n")
    out.append("Judge each requirement **only** from the two documents in its own section. Do not "
               "open the dataset files, this directory's `manifest.json`, or any analysis document "
               "until you have submitted your answers.\n")
    out.append("---\n")

    row_number = 0
    for index, (case_id, as_of, profile, job, rids, source) in enumerate(sections, start=1):
        letter = candidate_letter.setdefault(profile.id, chr(ord("A") + len(candidate_letter)))
        out.append(f"## Pairing {index}\n")
        out.append(f"### Candidate {letter}\n")
        out.append(_candidate_dossier(profile, as_of))
        out.append("")
        out.append(f"### The posting: {job.job_title}\n")
        out.append(f"*{job.company_kind}* — {job.location or 'location not stated'}\n")
        out.append("```")
        out.append(job.description)
        out.append("```")
        out.append("")
        out.append(f"### Requirements to judge for Pairing {index}\n")
        for rid in rids:
            requirement = job.requirement(rid)
            row_number += 1
            row_id = f"R{row_number:02d}"
            axis = "REQUIRED" if requirement.is_required else "PREFERRED"
            out.append(f"#### {row_id}\n")
            out.append(f"- **The requirement:** {requirement.label}")
            out.append(f"- **Stated in the posting as:** “{requirement.source_quote}”")
            out.append(f"- **Kind:** {requirement.category}")
            out.append(f"- **Status:** {axis}")
            if requirement.minimum_years is not None:
                out.append(f"- **Minimum years the posting asks for:** {requirement.minimum_years:g}")
            if requirement.degree_level:
                out.append(f"- **Degree level asked for:** {requirement.degree_level}")
            out.append("")
            out.append(f"Record for {row_id}: verdict, profile-evidence shape, requirement demand, "
                       f"and your reason. See `RUBRIC.md`.\n")

            manifest["rows"].append({
                "row_id": row_id, "dataset": source, "pairing": index, "candidate": letter,
                "case_id": case_id, "requirement_id": rid, "job_id": job.id,
                "profile_id": profile.id, "as_of": as_of.isoformat(),
                "category": requirement.category, "is_required": requirement.is_required,
                "has_first_rating": source == DATASET,
            })
            answers["ratings"].append({
                "row_id": row_id,
                "verdict": "",
                "profile_evidence_shape": "",
                "requirement_demand": "",
                "confidence": "",
                "rationale": "",
                "notes": "",
            })
        out.append("---\n")

    out.append(f"**End of packet — {row_number} requirements across {len(sections)} pairings.**\n")
    packet = "\n".join(out)
    _check_no_leaks(packet, dataset, sections)

    (HERE / "packet.md").write_text(packet, encoding="utf-8")
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (HERE / "answers_template.json").write_text(json.dumps(answers, indent=2), encoding="utf-8")
    from_v1 = sum(1 for row in manifest["rows"] if row["dataset"] == DATASET)
    print(f"packet.md              {row_number} rows ({from_v1} from {DATASET}, "
          f"{row_number - from_v1} from {FIXTURES_ONLY_DATASET}), "
          f"{len(sections)} pairings, {len(candidate_letter)} candidates")
    print(f"manifest.json          {len(manifest['rows'])} rows (ANALYST ONLY)")
    print("answers_template.json  blank")


def _check_no_leaks(packet: str, dataset, sections=()) -> None:
    """Refuse to write a packet containing anything the rater must not see.

    Checks the distinctive free text only — a verdict word like "missing" is
    ordinary English and cannot be substring-matched usefully, but a rationale,
    a contested note, a fixture label or an internal id can be, and those are
    the things that would actually give the answer away.

    `sections` additionally covers the fixtures-only dataset, whose labels,
    summaries and `probes` notes say outright which shape each fixture was
    written to test even though it carries no expectations at all.
    """
    banned: list[tuple[str, str]] = []
    banned += _fixtures_only_banned(sections)
    for profile in dataset.profiles.values():
        banned += [(profile.id, "profile id"), (profile.label, "profile label"),
                   (profile.summary, "profile summary")]
        for group in (profile.skills, profile.experiences, profile.education,
                      profile.certifications, profile.projects):
            banned += [(item.ref, "profile item ref") for item in group]
    for job in dataset.jobs.values():
        banned += [(job.id, "job id"), (job.label, "job label")]
        banned += [(r.id, "requirement id") for r in job.requirements]
        banned += [(r.aspect, "requirement aspect") for r in job.requirements if len(r.aspect) > 12]
    for case in dataset.cases:
        banned += [(case.id, "case id"), (case.note, "case note")]
        for truth in case.ground_truth:
            banned += [(truth.rationale, "first rater's rationale")]
            if truth.contested_note:
                banned += [(truth.contested_note, "contested note")]
            if truth.aspect and len(truth.aspect) > 12:
                banned += [(truth.aspect, "aspect override")]

    found = sorted({f"{kind}: {value!r}" for value, kind in banned if value and value in packet})
    if found:
        raise SystemExit("packet would leak first-rater information:\n  " + "\n  ".join(found))


def _fixtures_only_banned(sections) -> list[tuple[str, str]]:
    """The same check, for the fixtures-only dataset's own authoring metadata."""
    directory = groundtruth.DATASETS / FIXTURES_ONLY_DATASET
    if not any(source == FIXTURES_ONLY_DATASET for *_, source in sections):
        return []
    banned: list[tuple[str, str]] = []
    raw_pairings = json.loads((directory / "pairings.json").read_text(encoding="utf-8"))
    for entry in raw_pairings["pairings"]:
        banned += [(entry["id"], "v2 pairing id"), (entry["probes"], "v2 probe note")]
    for raw in json.loads((directory / "profiles.json").read_text(encoding="utf-8"))["profiles"]:
        banned += [(raw["id"], "v2 profile id"), (raw["label"], "v2 profile label"),
                   (raw["summary"], "v2 profile summary")]
        for key in ("skills", "experiences", "education", "certifications", "projects"):
            banned += [(item["ref"], "v2 profile item ref") for item in raw.get(key, [])]
    for raw in json.loads((directory / "jobs.json").read_text(encoding="utf-8"))["jobs"]:
        banned += [(raw["id"], "v2 job id"), (raw["label"], "v2 job label")]
        banned += [(item["id"], "v2 requirement id") for item in raw["requirements"]]
        banned += [(item["aspect"], "v2 requirement aspect")
                   for item in raw["requirements"] if len(item["aspect"]) > 12]
    return banned


if __name__ == "__main__":
    build()
